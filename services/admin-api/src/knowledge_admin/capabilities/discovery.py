"""What a run started from a server, service or environment (purpose resource) proposes for the inventory: services
(with their deployment on the run's server, its connections and part_of; server runs only) and documents (about the
run's resource or a service). Each call is checked and stored at once as a pending proposal (ka.discovery_proposals)
for an administrator to accept or reject (discovery.py). A proposal of the same service or document while the earlier
one is still pending replaces it."""

import asyncio
from typing import Annotated, Any, Literal

from psycopg.types.json import Jsonb
from pydantic import Field

from knowledge_admin import access as access_rules
from knowledge_admin.capabilities.context import RunContext
from knowledge_admin.capabilities.document_tools import Proposal
from knowledge_admin.capabilities.inventory import connect
from knowledge_admin.documents.store import INVENTORY_TAG, INVENTORY_TAGGED
from knowledge_admin.inventory import rules as inventory_rules
from knowledge_admin.inventory import store as inventory
from knowledge_admin.tools.registry import tool

NOT_DISCOVERY = 'The proposal tools work only in a run started from a server, service or environment.'
NOT_FROM_SERVER = 'propose_service works only in a run started from a server.'
# A document about the run's resource is linked to it: the document_resources column of each resource type. A
# proposal never updates a document tagged inventory: a title matching it is a new document (discovery.py).
RESOURCE_COLUMNS = {'server': 'server_id', 'service': 'service_id', 'environment': 'environment_id'}
RESOURCE_TABLES = {'server': 'ka.servers', 'service': 'ka.services', 'environment': 'ka.environments'}
SELF = 'A service cannot depend on itself.'
SELF_PART = 'A service cannot be part of itself.'
SERVICE_FIELDS = ('name', 'service_type', 'service_role', 'description', 'owner')
DEPLOYMENT_FIELDS = ('runtime', 'runtime_name', 'port', 'log_location', 'deployed_version')
SERVICES = 'SELECT id,name FROM ka.services WHERE project_id=%s'
CODE_PROJECTS = 'SELECT id,name FROM ka.repositories WHERE project_id=%s AND archived_at IS NULL'
SERVICE_BY_NAME = 'SELECT id,name,revision FROM ka.services WHERE project_id=%s AND lower(name)=lower(%s)'
# What a proposed connection can reach: the project's deployments (with their service, server and environment names)
# and its services (an external one is a target itself).
TARGET_DEPLOYMENTS = """SELECT d.id,d.port,d.environment_id,s.name AS service,v.name AS server,e.name AS environment
    FROM ka.service_deployments d JOIN ka.services s ON s.id=d.service_id JOIN ka.servers v ON v.id=d.server_id
    JOIN ka.environments e ON e.id=d.environment_id WHERE d.project_id=%s ORDER BY lower(v.name),lower(e.name),d.id"""
TARGET_SERVICES = (
    'SELECT id,name,service_type::text AS service_type FROM ka.services WHERE project_id=%s ORDER BY lower(name)'
)
PENDING_SERVICE = """SELECT id,number FROM ka.discovery_proposals WHERE session_id=%s AND kind='service' AND status='pending'
    AND lower(payload->>'name')=lower(%s)
    FOR UPDATE"""


def _names(rows) -> str:
    return ', '.join(row['name'] for row in rows) or 'none'


def resolve_environment(environments: list[dict], existing: list[dict], given: str | None) -> dict[str, Any]:
    """The deployment's environment. A given name is any existing environment of the project (`existing`), or a new
    one, created when the proposal is accepted. Without a name: the server's only environment (`environments`, those
    of its deployments)."""
    names = _names(environments)
    if given:
        found = next((item for item in existing if item['name'].lower() == given.strip().lower()), None)
        if found is None:
            return {'environment_id': None, 'environment': given}
        return {'environment_id': str(found['id']), 'environment': found['name']}
    if len(environments) == 1:
        return {'environment_id': str(environments[0]['id']), 'environment': environments[0]['name']}
    if environments:
        raise ValueError(f"This server is in several environments ({names}). Give the deployment's environment.")
    raise ValueError(
        'This server is in no environment. Give the deployment an environment name, for example production.'
    )


def _connection(wanted: dict, deployments: list[dict], services: list[dict], environment_id) -> dict:
    found = {'to_deployment_id': None, 'to_service_id': None, 'to_host': None}
    port = wanted.get('port')
    name, server, host = (wanted.get(key) or '' for key in ('to_service', 'to_server', 'to_host'))
    if bool(name.strip()) == bool(host.strip()):
        raise ValueError('Give to_service or to_host in each connection, not both.')
    if host.strip():
        found['to_host'] = label = inventory_rules.host(host)
        if port is None:
            raise ValueError(f'A connection to an external host needs a port ({label}).')
    else:
        name = name.strip()
        service = next((item for item in services if item['name'].lower() == name.lower()), None)
        external = service is not None and service['service_type'] == 'external'
        runs = [item for item in deployments if item['service'].lower() == name.lower()]
        if external and not server.strip():
            found['to_service_id'], label = str(service['id']), service['name']
        elif service is None:
            known = sorted({item['name'] for item in services}, key=str.lower)
            raise ValueError(f'No service named {name}. Known services: {", ".join(known) or "none"}.')
        elif not runs and external:
            raise ValueError(f'Service {service["name"]} is external and runs on no server; leave to_server empty.')
        elif not runs:
            raise ValueError(
                f'Service {service["name"]} has no deployment to connect to. Connect to one of its deployments once '
                'it is deployed, or mark it external if someone else runs it.'
            )
        else:
            service_name = runs[0]['service']
            servers = sorted({item['server'] for item in runs}, key=str.lower)
            if server.strip():
                runs = [item for item in runs if item['server'].lower() == server.strip().lower()]
                if not runs:
                    raise ValueError(
                        f'{service_name} does not run on {server.strip()}. It runs on: {", ".join(servers)}.'
                    )
            here = [item for item in runs if environment_id and str(item['environment_id']) == str(environment_id)]
            runs = here or runs
            servers = sorted({item['server'] for item in runs}, key=str.lower)
            if len(servers) > 1:
                raise ValueError(
                    f'{service_name} runs on several servers ({", ".join(servers)}). Give to_server: the one it connects to.'
                )
            if len(runs) > 1 and port is not None:
                runs = [item for item in runs if item['port'] == port] or runs
            if len(runs) > 1:
                places = ', '.join(f'{item["environment"]} port {item["port"]}' for item in runs)
                raise ValueError(f'{service_name} has several deployments on {servers[0]} ({places}). Give the port.')
            [target] = runs
            found['to_deployment_id'] = str(target['id'])
            label = f'{target["service"]} @ {target["server"]} ({target["environment"]})'
            port = target['port'] if port is None else port
    return {
        **found,
        'label': label,
        'port': port,
        'protocol': wanted.get('protocol') or 'tcp',
        'description': wanted.get('description') or '',
    }


def resolve_connections(
    connections: list[dict], deployments: list[dict], services: list[dict], environment_id
) -> list[dict]:
    """The proposal's connections with their targets as ids and a label. A target is a host (`to_host`, with a port),
    an external service by name (`services`: {id, name, service_type}, the project's services), or a deployment of a
    service by name (`deployments`: {id, service, server, environment_id, environment, port}): on `to_server` when given; in the proposal's environment (`environment_id`)
    when it runs there, else in any; refused when that leaves more than one server. A deployment target without a port
    takes the deployment's port."""
    return [_connection(item, deployments, services, environment_id) for item in connections]


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
        environments = inventory.list_environments(conn, project, server_id=server['id'])
        deployment = {key: value['deployment'][key] for key in DEPLOYMENT_FIELDS}
        existing = inventory.list_environments(conn, project)
        deployment.update(resolve_environment(environments, existing, value['deployment']['environment']))
        depends_on = _entries(conn, SERVICES, project, value['depends_on'], 'services')
        built_from = _entries(conn, CODE_PROJECTS, project, value['built_from'], 'code projects')
        part_of = _entries(conn, SERVICES, project, value['part_of'], 'services')
        if any(item['name'].lower() == value['name'].lower() for item in depends_on):
            raise ValueError(SELF)
        if any(item['name'].lower() == value['name'].lower() for item in part_of):
            raise ValueError(SELF_PART)
        connections = resolve_connections(
            value['connections'],
            conn.execute(TARGET_DEPLOYMENTS, (project,)).fetchall(),
            conn.execute(TARGET_SERVICES, (project,)).fetchall(),
            deployment['environment_id'],
        )
        match = conn.execute(SERVICE_BY_NAME, (project, value['name'])).fetchone()
        payload = {
            **{key: value[key] for key in SERVICE_FIELDS},
            'deployment': deployment,
            'depends_on': depends_on,
            'built_from': built_from,
            'connections': connections,
            'part_of': part_of,
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
                WHERE a.{column}=%s AND a.removed_at IS NULL AND NOT {INVENTORY_TAGGED} AND lower(d.title)=lower(%s)
                ORDER BY d.id LIMIT 1""",
                (entry, INVENTORY_TAG, value['title']),
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
        description="The environment's name: an existing environment, or a new one created when the proposal is accepted; leave it empty when the server is in exactly one.",
    )
    runtime: str = Field('', max_length=80, description='How it runs, for example systemd, docker or cron.')
    runtime_name: str = Field('', max_length=200, description='The unit, container or process name.')
    port: int | None = Field(None, ge=1, le=65535, description='Its main listening port, if any.')
    log_location: str = Field(
        '', max_length=1000, description='Where its logs are, for example journalctl -u nginx or /var/log/nginx/.'
    )
    deployed_version: str = Field('', max_length=200, description='The version that runs.')


class ConnectionProposal(Proposal):
    to_service: str | None = Field(
        None,
        max_length=200,
        description='A recorded service it connects to: a deployment of it (see to_server), or a service of type external.',
    )
    to_server: str | None = Field(
        None,
        max_length=200,
        description='The server of to_service it connects to, when that service runs on more than one server.',
    )
    to_host: str | None = Field(
        None,
        max_length=253,
        description='Or an external host (IP address or hostname) nobody has recorded as a service.',
    )
    port: int | None = Field(
        None, ge=1, le=65535, description="The port it connects to; empty: the deployment's port (a host needs one)."
    )
    protocol: str = Field(
        'tcp', pattern=r'^[a-z0-9.+-]{1,20}$', description='For example tcp, http, https or postgres.'
    )
    description: str = Field('', max_length=4000, description='What the connection is for.')


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
    connections: list[ConnectionProposal] = Field(
        default_factory=list,
        max_length=50,
        description="What this deployment connects to: a recorded service's deployment, an external service, or an external host.",
    )
    part_of: list[Annotated[str, Field(max_length=200)]] = Field(
        default_factory=list,
        max_length=100,
        description='Names of recorded services this service is part of (as a library or component).',
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


def check_propose_service(access: dict, started_from: dict | None = None, **_) -> str | None:
    """None when allowed, else the refusal text: a content editor, and the resource the run started from (what it
    proposes for) in its list. A run not started from a resource is refused by the tool (NOT_DISCOVERY)."""
    if not access_rules.is_content_editor(access):
        return access_rules.NOT_EDITOR
    if started_from and not access_rules.can_propose(access, started_from['type'], started_from['id']):
        return access_rules.refuse_use(started_from['type'])
    return None


def check_propose_document(access: dict, started_from: dict | None = None, **_) -> str | None:
    """None when allowed, else the refusal text (as check_propose_service)."""
    return check_propose_service(access, started_from)


@tool(arguments=ProposeService)
async def propose_service(context: RunContext, **value: Any) -> str:
    """Propose a service running on this server, with its deployment here, what that deployment connects to (connections) and the services it is part of (part_of), for an administrator to accept or reject. A service already recorded under this name is proposed as an update; proposing the same service again replaces your earlier proposal. If the call is refused, correct what it says and call again."""
    config = context['config']
    if refusal := check_propose_service(config['access'], started_from=config.get('started_from')):
        return refusal
    return await asyncio.to_thread(_propose_service, config, value)


@tool(arguments=ProposeDocument)
async def propose_document(context: RunContext, **value: Any) -> str:
    """Propose a document about the server, service or environment this run started from, or about a service (about_service), for an administrator to accept or reject. Never include passwords, keys or tokens. Proposing the same title about the same resource again replaces your earlier proposal."""
    config = context['config']
    if refusal := check_propose_document(config['access'], started_from=config.get('started_from')):
        return refusal
    return await asyncio.to_thread(_propose_document, config, value)
