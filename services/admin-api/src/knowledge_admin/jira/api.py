"""Project-scoped Jira settings, connection checks, and cached ticket selections."""

from functools import partial
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import psycopg
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from psycopg.types.json import Jsonb

from knowledge_admin.identity import request_actor
from knowledge_admin.jira import sync as jira_sync
from knowledge_admin.db import audit, storage, workspace
from knowledge_admin.events import handlers_api
from knowledge_admin.jira.client import JQL, JiraClient, JiraSettingsInput, TicketFetchInput
from knowledge_admin.repositories import RepositoryStore

SETTINGS_FIELDS = """id,base_url,username,revision,jql,(credential_id IS NOT NULL) AS has_token,
    last_tested_at,last_test_error,last_successful_sync_at,last_fetch_limit,last_fetch_total"""


NO_CONNECTION = 'Save a Jira URL, username and token in Jira Settings first'
USER_AGENT_FIELDS = """m.jira_user_key,m.jira_user_name,m.agent_id,a.name AS agent_name,a.enabled,m.updated_at"""
UserKey = Annotated[str, Path(min_length=1, max_length=255, pattern=r'\S')]


ASSIGNEE_HANDLER = 'Jira assignee agents'
ASSIGNEE_PROMPT = """You are assigned the Jira ticket {{ event.payload.key }}: {{ event.payload.summary }}
Status: {{ event.payload.status or 'unknown' }}. Reporter: {{ event.payload.reporter or 'unknown' }}.

Description:
{{ event.payload.description or 'No description.' }}
{% for comment in event.payload.comments | default([]) %}{% if loop.first %}
Comments:{% endif %}
{{ comment.author }} ({{ comment.created }}):
{{ comment.body }}
{% endfor %}
Work on the ticket and write your answer for the ticket: it is posted as a Jira comment."""
HANDLER_EXISTS = 'An assignee handler already exists.'


class UserAgentInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    agent_id: str = Field(min_length=1, max_length=200)
    name: str = Field(default='', max_length=255)


def seen_assignees(conn, connection_id, query: str) -> list[dict]:
    """Assignees of the connection's pulled tickets whose key or display name contains `query` (case-insensitive),
    newest name per key, as {key, name, display_name}; the login name is not stored, so it is None."""
    rows = conn.execute(
        """SELECT DISTINCT ON (assignee_id) assignee_id,assignee_name FROM ka.jira_tickets
        WHERE connection_id=%s AND assignee_id IS NOT NULL ORDER BY assignee_id,source_updated_at DESC""",
        (connection_id,),
    ).fetchall()
    needle = query.casefold()
    users = [
        {'key': row['assignee_id'], 'name': None, 'display_name': row['assignee_name'] or row['assignee_id']}
        for row in rows
        if needle in row['assignee_id'].casefold() or needle in (row['assignee_name'] or '').casefold()
    ]
    return sorted(users, key=lambda user: (user['display_name'].casefold(), user['key']))


def merged_users(jira_users: list[dict], seen: list[dict]) -> list[dict]:
    """Jira's users first, then the seen assignees Jira did not return (one entry per key)."""
    keys = {user['key'] for user in jira_users}
    return [*jira_users, *(user for user in seen if user['key'] not in keys)]


def assignee_handler(conn, project) -> dict | None:
    """The handler that runs the mapped agents (the oldest one sending to the assignee's agent), or None."""
    return conn.execute(
        """SELECT id,name,enabled FROM ka.event_handlers WHERE project_id=%s AND agent_from='assignee'
        ORDER BY created_at,id LIMIT 1""",
        (project,),
    ).fetchone()


def create_assignee_handler(conn, project, actor) -> UUID:
    """Create `Jira assignee agents` with its defaults, audited as automatic. It has no condition: every recorded
    update of a ticket assigned to a mapped user runs that user's agent with the whole ticket (our own replies are never
    recorded). Two first saves at once are kept to one handler by the unique handler name: the second insert waits for
    the first and raises UniqueViolation."""
    draft = handlers_api.HandlerDraft(
        name=ASSIGNEE_HANDLER,
        type_pattern='jira.ticket.*',
        action='agent',
        agent_from='assignee',
        prompt=ASSIGNEE_PROMPT,
        reply_to_jira=True,
        give_back=True,
    )
    values = handlers_api.stored(conn, draft)
    with conn.transaction():
        handler_id = handlers_api.insert(conn, project, values, actor)
    audit(
        conn,
        project,
        actor,
        'event_handler.created',
        'event_handler',
        handler_id,
        {**handlers_api.created(values), 'automatic': True},
    )
    return handler_id


def build_api(
    app: str,
    store: RepositoryStore | None,
    transport: httpx.BaseTransport | None = None,
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
        return {'connection': config, 'tickets': rows}

    @router.post('/jira/tickets/fetch')
    def fetch_tickets(value: TicketFetchInput, request: Request) -> dict:
        db = database()
        actor = request_actor(request)
        with db.connect() as conn:
            project = db.project(conn, app)
        jira_sync.pull_tickets(db, project, value.jql, value.limit, actor, transport)
        return list_tickets()

    def connection_id(conn, project) -> UUID | None:
        row = conn.execute(
            "SELECT id FROM ka.jira_connections WHERE project_id=%s AND name='primary' FOR SHARE", (project,)
        ).fetchone()
        return None if row is None else row['id']

    def user_agents(conn, connection: UUID | None) -> list[dict]:
        if connection is None:
            return []
        return conn.execute(
            f"""SELECT {USER_AGENT_FIELDS} FROM ka.jira_user_agents m JOIN ka.agents a ON a.id=m.agent_id
            WHERE m.connection_id=%s ORDER BY lower(m.jira_user_name),m.jira_user_key""",
            (connection,),
        ).fetchall()

    def mappings(conn, project, connection: UUID | None) -> dict:
        return {'user_agents': user_agents(conn, connection), 'handler': assignee_handler(conn, project)}

    @router.get('/jira/user-agents')
    def list_user_agents() -> dict:
        db = database()
        with workspace(db, app) as (conn, project):
            return mappings(conn, project, connection_id(conn, project))

    @router.post('/jira/user-agents/handler', status_code=201)
    def create_handler(request: Request) -> dict:
        """Create handler: the automatic handler on demand, when no assignee handler exists."""
        db = database()
        with workspace(db, app) as (conn, project):
            if assignee_handler(conn, project) is not None:
                raise HTTPException(409, HANDLER_EXISTS)
            try:
                create_assignee_handler(conn, project, db.actor(conn, *request_actor(request)))
            except psycopg.errors.UniqueViolation:
                raise HTTPException(409, handlers_api.DUPLICATE) from None
            return {'handler': assignee_handler(conn, project)}

    @router.put('/jira/user-agents/{key}')
    def save_user_agent(key: UserKey, value: UserAgentInput, request: Request) -> dict:
        db = database()
        subject, email = request_actor(request)
        key = key.strip()
        with workspace(db, app) as (conn, project):
            connection = connection_id(conn, project)
            if connection is None:
                raise HTTPException(400, NO_CONNECTION)
            if conn.execute('SELECT 1 FROM ka.agents WHERE id=%s FOR SHARE', (value.agent_id,)).fetchone() is None:
                raise HTTPException(400, 'Choose an existing agent')
            actor = db.actor(conn, subject, email)
            row = conn.execute(
                """INSERT INTO ka.jira_user_agents(connection_id,jira_user_key,jira_user_name,agent_id,created_by,updated_by)
                VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (connection_id,jira_user_key) DO UPDATE SET
                jira_user_name=EXCLUDED.jira_user_name,agent_id=EXCLUDED.agent_id,updated_by=EXCLUDED.updated_by
                RETURNING id""",
                (connection, key, value.name or key, value.agent_id, actor, actor),
            ).fetchone()
            audit(
                conn,
                project,
                actor,
                'jira.user_agent_saved',
                'jira_user_agent',
                row['id'],
                {'jira_user_key': key, 'agent_id': value.agent_id},
            )
            if assignee_handler(conn, project) is None:
                try:
                    create_assignee_handler(conn, project, actor)
                except psycopg.errors.UniqueViolation:
                    pass  # another save created it meanwhile, or a handler has the name: the mapping still saves
            return mappings(conn, project, connection)

    @router.delete('/jira/user-agents/{key}', status_code=204)
    def delete_user_agent(key: UserKey, request: Request) -> Response:
        db = database()
        subject, email = request_actor(request)
        with workspace(db, app) as (conn, project):
            row = conn.execute(
                """DELETE FROM ka.jira_user_agents WHERE connection_id=%s AND jira_user_key=%s
                RETURNING id,agent_id""",
                (connection_id(conn, project), key.strip()),
            ).fetchone()
            if row is None:
                raise HTTPException(404, 'This Jira user is not mapped to an agent')
            audit(
                conn,
                project,
                db.actor(conn, subject, email),
                'jira.user_agent_removed',
                'jira_user_agent',
                row['id'],
                {'jira_user_key': key.strip(), 'agent_id': row['agent_id']},
            )
        return Response(status_code=204)

    @router.get('/jira/users')
    def search_users(query: Annotated[str, Query(min_length=1, max_length=100, pattern=r'\S')]) -> dict:
        """Jira users for the user picker: Jira's own search merged with the assignees seen on pulled tickets. The
        token goes only to the configured Jira; a Jira failure still returns the seen assignees, with a warning."""
        db = database()
        query = query.strip()
        with workspace(db, app) as (conn, project):
            connection = connection_id(conn, project)
            if connection is None:
                raise HTTPException(400, NO_CONNECTION)
            seen = seen_assignees(conn, connection, query)
        try:
            config, token = connection_credentials(db)
            with JiraClient(config['base_url'], token, transport) as jira:
                found = jira.search_users(query)
        except HTTPException as error:
            return {'users': seen, 'warning': f'Jira user search failed: {error.detail}'}
        return {'users': merged_users(found, seen), 'warning': None}

    return router
