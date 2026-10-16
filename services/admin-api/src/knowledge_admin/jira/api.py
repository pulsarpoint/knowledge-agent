"""Project-scoped Jira settings, connection checks, and cached ticket selections."""

from functools import partial
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import psycopg
from fastapi import APIRouter, HTTPException, Request
from psycopg.types.json import Jsonb

from knowledge_admin.identity import request_actor
from knowledge_admin.jira import sync as jira_sync
from knowledge_admin.data_root import jira_folder
from knowledge_admin.db import storage, workspace
from knowledge_admin.jira.client import JQL, JiraClient, JiraSettingsInput, TicketFetchInput
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.jira.export import TicketExportInput, publish_ticket, ticket_markdown

SETTINGS_FIELDS = """id,base_url,username,revision,jql,(credential_id IS NOT NULL) AS has_token,
    last_tested_at,last_test_error,last_successful_sync_at,last_fetch_limit,last_fetch_total"""


def export_folder() -> str | None:
    """The Jira export folder, or None when JIRA_EXPORT_ROOT is not a folder inside DATA_ROOT_DIR."""
    try:
        return jira_folder()
    except ValueError:
        return None


def build_api(
    app: str,
    store: RepositoryStore | None,
    transport: httpx.BaseTransport | None = None,
    backoffice_client: httpx.Client | None = None,
) -> APIRouter:
    router = APIRouter()

    database = partial(storage, store, 'Jira storage', message='Jira storage is not configured')

    @router.get('/jira/settings')
    def settings() -> dict:
        db = database()
        with workspace(db, app) as (conn, project):
            row = conn.execute(
                f"SELECT {SETTINGS_FIELDS} FROM ka.jira_connections WHERE project_id=%s AND name='primary'", (project,)
            ).fetchone()
        return {'connection': row}

    @router.put('/jira/settings')
    def save_settings(value: JiraSettingsInput, request: Request) -> dict:
        db = database()
        subject, email = request_actor(request)
        try:
            with workspace(db, app) as (conn, project):
                actor = db.actor(conn, subject, email)
                current = conn.execute(
                    "SELECT * FROM ka.jira_connections WHERE project_id=%s AND name='primary' FOR UPDATE", (project,)
                ).fetchone()
                if (current is None and value.revision is not None) or (
                    current is not None and value.revision != current['revision']
                ):
                    raise HTTPException(409, 'Jira settings changed. Reload settings before saving')
                changed_identity = current is not None and (
                    current['base_url'] != value.base_url or current['username'] != value.username
                )
                if changed_identity and value.token is None:
                    raise HTTPException(400, 'Re-enter the token when changing the Jira URL or username')
                connection_id = current['id'] if current is not None else uuid4()
                credential = current['credential_id'] if current is not None else None
                previous_credential = credential
                if 'token' in value.model_fields_set:
                    if value.token is None:
                        credential = None
                    else:
                        credential = credential or uuid4()
                        nonce = os.urandom(12)
                        ciphertext = nonce + db.cipher.encrypt(
                            nonce,
                            value.token.get_secret_value().encode(),
                            f'{project}:{credential}:jira_pat:v1'.encode(),
                        )
                        conn.execute(
                            """INSERT INTO ka.credentials(id,project_id,name,kind,ciphertext,key_version,created_by)
                            VALUES (%s,%s,%s,'jira_pat',%s,'v1',%s) ON CONFLICT(id) DO UPDATE
                            SET ciphertext=EXCLUDED.ciphertext,key_version='v1'""",
                            (credential, project, f'jira-{connection_id}', ciphertext, actor),
                        )
                conn.execute(
                    """INSERT INTO ka.jira_connections(id,project_id,name,base_url,username,credential_id,jql)
                    VALUES (%s,%s,'primary',%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET
                    base_url=EXCLUDED.base_url,username=EXCLUDED.username,credential_id=EXCLUDED.credential_id,
                    revision=jira_connections.revision+1,last_tested_at=NULL,last_test_error=NULL""",
                    (connection_id, project, value.base_url, value.username, credential, JQL),
                )
                if changed_identity:
                    # Remote IDs are unique within a Jira installation, not across servers.
                    conn.execute('DELETE FROM ka.jira_tickets WHERE connection_id=%s', (connection_id,))
                if changed_identity or 'token' in value.model_fields_set:
                    conn.execute(
                        'UPDATE ka.jira_tickets SET out_of_scope_at=now() WHERE connection_id=%s AND out_of_scope_at IS NULL',
                        (connection_id,),
                    )
                    conn.execute(
                        """UPDATE ka.jira_connections SET last_successful_sync_at=NULL,last_fetch_limit=NULL,
                        last_fetch_total=NULL,fetch_revision=fetch_revision+1 WHERE id=%s""",
                        (connection_id,),
                    )
                if previous_credential is not None and credential is None:
                    conn.execute('DELETE FROM ka.credentials WHERE id=%s', (previous_credential,))
                row = conn.execute(
                    f'SELECT {SETTINGS_FIELDS} FROM ka.jira_connections WHERE id=%s', (connection_id,)
                ).fetchone()
                conn.execute(
                    """INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
                    VALUES (%s,%s,'jira.settings_saved','jira_connection',%s,%s)""",
                    (
                        project,
                        actor,
                        connection_id,
                        Jsonb({'revision': row['revision'], 'has_token': row['has_token']}),
                    ),
                )
            return {'connection': row}
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, 'Jira settings changed. Reload settings before saving') from None

    def connection_credentials(db: RepositoryStore) -> tuple[dict, str]:
        with db.connect() as conn:
            project = db.project(conn, app)
        return jira_sync.credentials(db, project)

    @router.post('/jira/test')
    def test_connection(request: Request) -> dict:
        db = database()
        subject, email = request_actor(request)
        config, token = connection_credentials(db)
        failure = None
        user = None
        try:
            with JiraClient(config['base_url'], token, transport) as jira:
                user = jira.current_user(config['username'])
                # Authentication alone does not establish permission to search issues.
                jira.tickets(1)
        except HTTPException as error:
            failure = error
        tested_at = datetime.now(UTC)
        with db.connect() as conn:
            updated = conn.execute(
                """UPDATE ka.jira_connections SET last_tested_at=%s,last_test_error=%s
                WHERE id=%s AND project_id=%s AND revision=%s RETURNING id""",
                (
                    tested_at,
                    str(failure.detail) if failure is not None else None,
                    config['id'],
                    config['project_id'],
                    config['revision'],
                ),
            ).fetchone()
            if updated is None:
                raise HTTPException(409, 'Jira settings changed during the connection test. Run the test again')
            actor = db.actor(conn, subject, email)
            conn.execute(
                """INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
                VALUES (%s,%s,'jira.connection_tested','jira_connection',%s,%s)""",
                (
                    config['project_id'],
                    actor,
                    config['id'],
                    Jsonb({'ok': failure is None, 'revision': config['revision']}),
                ),
            )
        if failure is not None:
            raise failure
        return {'ok': True, 'user': user, 'tested_at': tested_at}

    @router.get('/jira/tickets')
    def list_tickets() -> dict:
        db = database()
        with workspace(db, app) as (conn, project):
            # Lock the connection while reading its snapshot metadata and rows together.
            config = conn.execute(
                f"SELECT {SETTINGS_FIELDS} FROM ka.jira_connections WHERE project_id=%s AND name='primary' FOR SHARE",
                (project,),
            ).fetchone()
            rows = (
                []
                if config is None
                else conn.execute(
                    """SELECT id,issue_key,jira_project_key,issue_type,labels,summary,status_name,priority,
                assignee_name,source_updated_at FROM ka.jira_tickets WHERE connection_id=%s AND project_id=%s
                AND out_of_scope_at IS NULL ORDER BY source_updated_at DESC,issue_key DESC LIMIT 200""",
                    (config['id'], project),
                ).fetchall()
            )
        return {'connection': config, 'tickets': rows, 'export_folder': export_folder()}

    @router.post('/jira/tickets/fetch')
    def fetch_tickets(value: TicketFetchInput, request: Request) -> dict:
        db = database()
        actor = request_actor(request)
        with db.connect() as conn:
            project = db.project(conn, app)
        jira_sync.pull_tickets(db, project, value.jql, value.limit, actor, transport)
        return list_tickets()

    @router.post('/jira/tickets/{ticket_id}/export')
    def export_ticket(ticket_id: UUID, value: TicketExportInput, request: Request) -> dict:
        if export_folder() is None:
            raise HTTPException(503, 'JIRA_EXPORT_ROOT must be a folder inside DATA_ROOT_DIR')
        db = database()
        subject, email = request_actor(request)
        rag_key = os.environ.get('RAG_BACKOFFICE_KEY')
        if backoffice_client is None or not rag_key:
            raise HTTPException(503, 'RAG administration is not configured')
        config, token = connection_credentials(db)
        if config['revision'] != value.connection_revision:
            raise HTTPException(409, 'Jira settings changed. Reload the ticket list before exporting')
        with db.connect() as conn:
            ticket = conn.execute(
                """SELECT remote_id,issue_key FROM ka.jira_tickets
                WHERE id=%s AND project_id=%s AND connection_id=%s AND out_of_scope_at IS NULL""",
                (ticket_id, config['project_id'], config['id']),
            ).fetchone()
        if ticket is None:
            raise HTTPException(404, 'Ticket is no longer in the saved selection. Fetch tickets again')
        with JiraClient(config['base_url'], token, transport) as jira:
            jira.current_user(config['username'])
            issue, comments = jira.issue_with_comments(ticket['remote_id'])
        if issue['key'] != ticket['issue_key']:
            raise HTTPException(409, 'The ticket key changed in Jira. Fetch the ticket list again before exporting')
        content = ticket_markdown(config['base_url'], issue, comments)
        with db.connect() as conn:
            # Keep settings stable during the short local publish, after all Jira traffic finishes.
            current = conn.execute(
                'SELECT revision,fetch_revision FROM ka.jira_connections WHERE id=%s FOR SHARE', (config['id'],)
            ).fetchone()
            if (
                current is None
                or current['revision'] != config['revision']
                or current['fetch_revision'] != config['fetch_revision']
            ):
                raise HTTPException(409, 'Jira settings or tickets changed during export. Reload the list and retry')
            actor = db.actor(conn, subject, email)
            result = publish_ticket(backoffice_client, rag_key, config['project_id'], actor, issue['key'], content)
            conn.execute(
                """INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
                VALUES (%s,%s,'jira.ticket_exported','jira_ticket',%s,%s)""",
                (
                    config['project_id'],
                    actor,
                    ticket_id,
                    Jsonb({'path': result['path'], 'status': result['status'], 'comments': len(comments)}),
                ),
            )
        return {**result, 'issue_key': issue['key'], 'comments': len(comments)}

    return router
