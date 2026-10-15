"""What a run started from a server, service or environment (purpose resource) proposes for the inventory: services
(with their deployment on the run's server; server runs only) and documents (about the run's resource or a service).
Each call is checked and stored at once as a pending proposal (ka.discovery_proposals) for an administrator to accept
or reject (discovery.py). A proposal of the same service or document while the earlier one is still pending replaces
it."""

import asyncio
from typing import Annotated, Any, Literal

from psycopg.types.json import Jsonb
from pydantic import Field

from knowledge_admin.capabilities.context import RunContext
from knowledge_admin.capabilities.document_tools import Proposal
from knowledge_admin.capabilities.inventory import connect
from knowledge_admin.tools.registry import tool

NOT_DISCOVERY = 'The proposal tools work only in a run started from a server, service or environment.'
NOT_FROM_SERVER = 'propose_service works only in a run started from a server.'
# A document about the run's resource is linked to it: the document_resources column of each resource type. A
# proposal never updates a document tagged inventory: a title matching it is a new document (discovery.py).
RESOURCE_COLUMNS = {'server': 'server_id', 'service': 'service_id', 'environment': 'environment_id'}
RESOURCE_TABLES = {'server': 'ka.servers', 'service': 'ka.services', 'environment': 'ka.environments'}
SELF = 'A service cannot depend on itself.'
SERVICE_FIELDS = ('name', 'service_type', 'service_role', 'description', 'owner')
DEPLOYMENT_FIELDS = ('runtime', 'runtime_name', 'port', 'log_location', 'deployed_version')
SERVICES = 'SELECT id,name FROM ka.services WHERE project_id=%s'
CODE_PROJECTS = 'SELECT id,name FROM ka.repositories WHERE project_id=%s AND archived_at IS NULL'
SERVICE_BY_NAME = 'SELECT id,name,revision FROM ka.services WHERE project_id=%s AND lower(name)=lower(%s)'
PENDING_SERVICE = """SELECT id,number FROM ka.discovery_proposals WHERE session_id=%s AND kind='service' AND status='pending'
    AND lower(payload->>'name')=lower(%s)
    FOR UPDATE"""


def _names(rows) -> str:
    return ', '.join(row['name'] for row in rows) or 'none'


def resolve_environment(environments: list[dict], given: str | None) -> dict[str, Any]:
    """The deployment's environment: one of the server's environments (the only one when none is given); a free-text
    name when the server is in no environment."""
    names = _names(environments)
    if given:
        if not environments:
            return {'environment_id': None, 'environment': given}
        found = next((item for item in environments if item['name'].lower() == given.lower()), None)
        if found is None:
            raise ValueError(f"Choose one of this server's environments: {names}.")
        return {'environment_id': str(found['id']), 'environment': found['name']}
    if len(environments) == 1:
        return {'environment_id': str(environments[0]['id']), 'environment': environments[0]['name']}
    if environments:
        raise ValueError(f"This server is in several environments ({names}). Give the deployment's environment.")
    raise ValueError(
        'This server is in no environment. Give the deployment an environment name, for example production.'
    )


def _entries(conn, query: str, project, names: list[str], what: str) -> list[dict[str, str]]:
    wanted = list(dict.fromkeys(name.strip() for name in names if name.strip()))
    if not wanted:
        return []
    rows = conn.execute(
        query + ' AND lower(name)=ANY(%s) ORDER BY lower(name)', (project, [name.lower() for name in wanted])
    ).fetchall()
    found = {row['name'].lower() for row in rows}
    unknown = [name for name in wanted if name.lower() not in found]
    if unknown:
        known = conn.execute(query + ' ORDER BY lower(name) LIMIT 100', (project,)).fetchall()
        raise ValueError(f'Unknown {what}: {", ".join(unknown)}. Known {what}: {_names(known)}.')
    return [{'id': str(row['id']), 'name': row['name']} for row in rows]


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
    conn,
    session: dict,
    kind: str,
    payload: dict,
    existing: dict | None,
    *,
    service_id=None,
    service_proposal_id=None,
    document_id=None,
) -> tuple[int, bool]:
    if existing is not None:
        # Re-checked: an administrator may have decided the proposal since it was looked up; a decided one stays as it is.
        updated = conn.execute(
            """UPDATE ka.discovery_proposals SET payload=%s,service_id=%s,service_proposal_id=%s,document_id=%s,
            revision=revision+1 WHERE id=%s AND status='pending' RETURNING id""",
            (Jsonb(payload), service_id, service_proposal_id, document_id, existing['id']),
        ).fetchone()
        if updated is not None:
            return existing['number'], True
    number = conn.execute(
        'SELECT coalesce(max(number),0)+1 AS n FROM ka.discovery_proposals WHERE session_id=%s', (session['id'],)
    ).fetchone()['n']
    conn.execute(
        """INSERT INTO ka.discovery_proposals(project_id,session_id,server_id,number,kind,payload,service_id,
        service_proposal_id,document_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            session['project_id'],
            session['id'],
            session['server_id'],
            number,
            kind,
            Jsonb(payload),
            service_id,
            service_proposal_id,
            document_id,
        ),
    )
    return number, False


def _said(number: int, what: str, replaced: bool) -> str:
    return f'Proposal {number}: {what}' + (' (replaces your earlier proposal)' if replaced else '') + '.'


def _propose_service(config: dict[str, Any], value: dict[str, Any]) -> str:
    with connect() as conn:
        session, server = _run(conn, config)
        if server['type'] != 'server':
            raise ValueError(NOT_FROM_SERVER)
        project = session['project_id']
        environments = conn.execute(
            """SELECT e.id,e.name FROM ka.environment_servers es JOIN ka.environments e
            ON e.id=es.environment_id WHERE es.server_id=%s ORDER BY lower(e.name)""",
            (server['id'],),
        ).fetchall()
        deployment = {key: value['deployment'][key] for key in DEPLOYMENT_FIELDS}
        deployment.update(resolve_environment(environments, value['deployment']['environment']))
        depends_on = _entries(conn, SERVICES, project, value['depends_on'], 'services')
        built_from = _entries(conn, CODE_PROJECTS, project, value['built_from'], 'code projects')
        if any(item['name'].lower() == value['name'].lower() for item in depends_on):
            raise ValueError(SELF)
        match = conn.execute(SERVICE_BY_NAME, (project, value['name'])).fetchone()
        payload = {
            **{key: value[key] for key in SERVICE_FIELDS},
            'deployment': deployment,
            'depends_on': depends_on,
            'built_from': built_from,
            'matched_revision': match['revision'] if match else None,
        }
        existing = conn.execute(PENDING_SERVICE, (session['id'], value['name'])).fetchone()
        number, replaced = _save(conn, session, 'service', payload, existing, service_id=match['id'] if match else None)
    return _said(number, f'update of service {match["name"]}' if match else f'new service {value["name"]}', replaced)


def _propose_document(config: dict[str, Any], value: dict[str, Any]) -> str:
    with connect() as conn:
        session, resource = _run(conn, config)
        about = (value['about_service'] or '').strip() or None
        service_id = service_proposal_id = document_id = None
        if resource['type'] == 'service':
            service_id = resource['id']
        target = f'{resource["type"]} {resource["name"]}'
        if about:
            service = conn.execute(SERVICE_BY_NAME, (session['project_id'], about)).fetchone()
            if service is not None:
                service_id, target = service['id'], f'service {service["name"]}'
            else:
                proposal = conn.execute(PENDING_SERVICE, (session['id'], about)).fetchone()
                if proposal is None:
                    raise ValueError(
                        f'No service named {about}. Propose it with propose_service first, or leave about_service empty.'
                    )
                service_proposal_id, target = proposal['id'], f'proposed service {about}'
        if service_proposal_id is None:
            column, entry = (
                ('service_id', service_id) if service_id else (RESOURCE_COLUMNS[resource['type']], resource['id'])
            )
            found = conn.execute(
                f"""SELECT d.id,d.revision FROM ka.document_resources a JOIN ka.document_details d ON d.id=a.document_id
                WHERE a.{column}=%s AND a.removed_at IS NULL AND NOT 'inventory'=ANY(d.tags) AND lower(d.title)=lower(%s)
                ORDER BY d.id LIMIT 1""",
                (entry, value['title']),
            ).fetchone()
            document_id = found['id'] if found else None
        existing = conn.execute(
            """SELECT id,number FROM ka.discovery_proposals WHERE session_id=%s AND kind='document'
            AND status='pending' AND lower(payload->>'title')=lower(%s) AND service_id IS NOT DISTINCT FROM %s
            AND service_proposal_id IS NOT DISTINCT FROM %s
            FOR UPDATE""",
            (session['id'], value['title'], service_id, service_proposal_id),
        ).fetchone()
        payload = {
            'title': value['title'],
            'description': value['description'],
            'markdown': value['markdown'],
            'about_service': about,
            'matched_document_revision': found['revision'] if document_id else None,
        }
        number, replaced = _save(
            conn,
            session,
            'document',
            payload,
            existing,
            service_id=service_id,
            service_proposal_id=service_proposal_id,
            document_id=document_id,
        )
    what = 'new version of document' if document_id else 'new document'
    return _said(number, f'{what} "{value["title"]}" about {target}', replaced)


ServiceType = Literal['unclassified', 'custom', 'builtin', 'external']
ServiceRole = Literal[
    'unclassified', 'application', 'proxy', 'web_server', 'database', 'cache', 'message_broker', 'worker', 'other'
]


class DeploymentProposal(Proposal):
    environment: str | None = Field(
        None,
        max_length=100,
        description="One of the server's environments; leave it empty when the server is in exactly one.",
    )
    runtime: str = Field('', max_length=80, description='How it runs, for example systemd, docker or cron.')
    runtime_name: str = Field('', max_length=200, description='The unit, container or process name.')
    port: int | None = Field(None, ge=1, le=65535, description='Its main listening port, if any.')
    log_location: str = Field(
        '', max_length=1000, description='Where its logs are, for example journalctl -u nginx or /var/log/nginx/.'
    )
    deployed_version: str = Field('', max_length=200, description='The version that runs.')


class ProposeService(Proposal):
    name: str = Field(
        min_length=1,
        max_length=200,
        description='The service name. A service already recorded with this name is proposed as an update.',
    )
    service_type: ServiceType = Field(
        'unclassified',
        description='custom: built by us; builtin: part of the platform or OS; external: run by someone else.',
    )
    service_role: ServiceRole = Field('unclassified', description='What it does for the system.')
    description: str = Field('', max_length=4000, description='What it does, in a few sentences.')
    owner: str = Field('', max_length=200, description='Who runs it, when you can tell.')
    deployment: DeploymentProposal = Field(description='How it runs on this server.')
    depends_on: list[Annotated[str, Field(max_length=200)]] = Field(
        default_factory=list, max_length=100, description='Names of recorded services it depends on.'
    )
    built_from: list[Annotated[str, Field(max_length=200)]] = Field(
        default_factory=list, max_length=100, description='Names of recorded code projects it is built from.'
    )


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
        description='The service the document is about (recorded, or proposed earlier in this run); empty: what the run started from.',
    )


@tool(arguments=ProposeService)
async def propose_service(context: RunContext, **value: Any) -> str:
    """Propose a service running on this server, with its deployment here, for an administrator to accept or reject. A service already recorded under this name is proposed as an update; proposing the same service again replaces your earlier proposal. If the call is refused, correct what it says and call again."""
    return await asyncio.to_thread(_propose_service, context['config'], value)


@tool(arguments=ProposeDocument)
async def propose_document(context: RunContext, **value: Any) -> str:
    """Propose a document about the server, service or environment this run started from, or about a service (about_service), for an administrator to accept or reject. Never include passwords, keys or tokens. Proposing the same title about the same resource again replaces your earlier proposal."""
    return await asyncio.to_thread(_propose_document, context['config'], value)
