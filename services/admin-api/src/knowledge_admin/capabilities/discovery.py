"""What a run started from a server, service or environment (purpose resource) proposes: documents (about the run's
resource or a recorded service). Each call is checked and stored at once as a pending proposal
(ka.discovery_proposals) for an administrator to accept or reject (discovery.py). A proposal of the same document
while the earlier one is still pending replaces it. Inventory findings are not proposals: they are saved as drafts
with the save_* tools."""

import asyncio
from typing import Any

from psycopg.types.json import Jsonb
from pydantic import Field

from knowledge_admin import access as access_rules
from knowledge_admin.capabilities.context import RunContext
from knowledge_admin.capabilities.document_tools import Proposal
from knowledge_admin.capabilities.inventory import connect
from knowledge_admin.documents.store import INVENTORY_TAG, INVENTORY_TAGGED
from knowledge_admin.tools.registry import tool

NOT_DISCOVERY = 'The proposal tools work only in a run started from a server, service or environment.'
# A document about the run's resource is linked to it: the document_resources column of each resource type. A
# proposal never updates a document tagged inventory: a title matching it is a new document (discovery.py).
RESOURCE_COLUMNS = {'server': 'server_id', 'service': 'service_id', 'environment': 'environment_id'}
RESOURCE_TABLES = {'server': 'ka.servers', 'service': 'ka.services', 'environment': 'ka.environments'}
SERVICE_BY_NAME = 'SELECT id,name,revision FROM ka.services WHERE project_id=%s AND lower(name)=lower(%s)'


def _run(conn, config: dict[str, Any]) -> tuple[dict, dict]:
    """The run's session (config['session_id'], set by the worker; a run started from a resource), locked so its
    proposals are numbered and replaced one at a time, and the resource it started from as its pinned configuration
    records it (`started_from`: {type, id, name}, the name as it is now)."""
    session_id = config.get('session_id')
    session = (
        conn.execute(
            """SELECT id,project_id,server_id,agent_config->'started_from' AS started_from FROM ka.agent_sessions
            WHERE id=%s AND purpose='resource' FOR UPDATE""",
            (session_id,),
        ).fetchone()
        if session_id
        else None
    )
    started = session['started_from'] if session else None
    if not started or started.get('type') not in RESOURCE_TABLES:
        raise ValueError(NOT_DISCOVERY)
    resource = conn.execute(
        f'SELECT id,name FROM {RESOURCE_TABLES[started["type"]]} WHERE id=%s', (started['id'],)
    ).fetchone()
    if resource is None:
        raise ValueError(NOT_DISCOVERY)
    return session, {**resource, 'type': started['type']}


def _save(
    conn, session: dict, payload: dict, existing: dict | None, *, service_id=None, document_id=None
) -> tuple[int, bool]:
    if existing is not None:
        # Re-checked: an administrator may have decided the proposal since it was looked up; a decided one stays as it is.
        updated = conn.execute(
            """UPDATE ka.discovery_proposals SET payload=%s,service_id=%s,document_id=%s,revision=revision+1
            WHERE id=%s AND status='pending' RETURNING id""",
            (Jsonb(payload), service_id, document_id, existing['id']),
        ).fetchone()
        if updated is not None:
            return existing['number'], True
    number = conn.execute(
        'SELECT coalesce(max(number),0)+1 AS n FROM ka.discovery_proposals WHERE session_id=%s', (session['id'],)
    ).fetchone()['n']
    conn.execute(
        """INSERT INTO ka.discovery_proposals(project_id,session_id,server_id,number,kind,payload,service_id,document_id)
        VALUES (%s,%s,%s,%s,'document',%s,%s,%s)""",
        (session['project_id'], session['id'], session['server_id'], number, Jsonb(payload), service_id, document_id),
    )
    return number, False


def _said(number: int, what: str, replaced: bool) -> str:
    return f'Proposal {number}: {what}' + (' (replaces your earlier proposal)' if replaced else '') + '.'


def _propose_document(config: dict[str, Any], value: dict[str, Any]) -> str:
    with connect() as conn:
        session, resource = _run(conn, config)
        about = (value['about_service'] or '').strip() or None
        service_id = None
        if resource['type'] == 'service':
            service_id = resource['id']
        target = f'{resource["type"]} {resource["name"]}'
        if about:
            service = conn.execute(SERVICE_BY_NAME, (session['project_id'], about)).fetchone()
            if service is None:
                raise ValueError(
                    f'No service named {about}. Save it first (save_service), or leave about_service empty.'
                )
            service_id, target = service['id'], f'service {service["name"]}'
        column, entry = (
            ('service_id', service_id) if service_id else (RESOURCE_COLUMNS[resource['type']], resource['id'])
        )
        found = conn.execute(
            f"""SELECT d.id,d.revision FROM ka.document_resources a JOIN ka.document_details d ON d.id=a.document_id
            WHERE a.{column}=%s AND a.removed_at IS NULL AND NOT {INVENTORY_TAGGED} AND lower(d.title)=lower(%s)
            ORDER BY d.id LIMIT 1""",
            (entry, INVENTORY_TAG, value['title']),
        ).fetchone()
        document_id = found['id'] if found else None
        existing = conn.execute(
            """SELECT id,number FROM ka.discovery_proposals WHERE session_id=%s AND kind='document'
            AND status='pending' AND lower(payload->>'title')=lower(%s) AND service_id IS NOT DISTINCT FROM %s
            FOR UPDATE""",
            (session['id'], value['title'], service_id),
        ).fetchone()
        payload = {
            'title': value['title'],
            'description': value['description'],
            'markdown': value['markdown'],
            'about_service': about,
            'matched_document_revision': found['revision'] if document_id else None,
        }
        number, replaced = _save(conn, session, payload, existing, service_id=service_id, document_id=document_id)
    what = 'new version of document' if document_id else 'new document'
    return _said(number, f'{what} "{value["title"]}" about {target}', replaced)


class ProposeDocument(Proposal):
    title: str = Field(
        min_length=1,
        max_length=240,
        description='The document title. A document with this title already linked to the same server, service or environment gets a new version (never the document tagged inventory: that gets a new document beside it).',
    )
    markdown: str = Field(min_length=1, max_length=1_000_000, description='The complete document in Markdown.')
    description: str = Field('', max_length=4000, description='One or two sentences on what the document covers.')
    about_service: str | None = Field(
        None,
        max_length=200,
        description='The service the document is about (recorded); empty: what the run started from.',
    )


def check_propose_document(access: dict, started_from: dict | None = None, **_) -> str | None:
    """None when allowed, else the refusal text: the resource the run started from (what it proposes for) in its
    list. A run not started from a resource is refused by the tool (NOT_DISCOVERY)."""
    if started_from and not access_rules.can_use(access, started_from['type'], started_from['id']):
        return access_rules.refuse_use(started_from['type'])
    return None


@tool(arguments=ProposeDocument)
async def propose_document(context: RunContext, **value: Any) -> str:
    """Propose a document about the server, service or environment this run started from, or about a recorded service (about_service), for an administrator to accept or reject. Inventory findings (services, deployments, connections, relations) are not proposed: save them as drafts with the save_* tools (save_inventory_draft for many at once). Never include passwords, keys or tokens. Proposing the same title about the same resource again replaces your earlier proposal."""
    config = context['config']
    if refusal := check_propose_document(config['access'], started_from=config.get('started_from')):
        return refusal
    return await asyncio.to_thread(_propose_document, config, value)
