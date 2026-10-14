"""Read the inventory for an agent: get_resource answers with everything recorded about one server, service,
environment or code project, with links to related resources the model follows by their type and id. Read-only.
The tool runs where agents run (agent-session-worker, admin-api); configure() gives it that process's database.
The workspace is the run's (a resource run pins config['project_id']) or the single application's. It answers only
about a resource the run was given (its allowlist); anything else, a linked resource too, reads as missing."""

import asyncio
import json
from typing import Any, Literal
from uuid import UUID

from knowledge_admin.capabilities.context import RunContext, allowed

MAX_ITEMS = 100
SHORT = 300
UNAVAILABLE = 'The inventory is not available in this run.'
NOT_GIVEN = 'The {type} {id} is not available in this run.'
ResourceType = Literal['server', 'service', 'environment', 'code_project']
_store = None


def configure(store) -> None:
    """The database the inventory and discovery tools read and write: the process's RepositoryStore."""
    global _store
    _store = store


def connect():
    if _store is None:
        raise ValueError(UNAVAILABLE)
    return _store.connect()


def project_of(conn, config: dict[str, Any]) -> UUID:
    if config.get('project_id'):
        return UUID(str(config['project_id']))
    rows = conn.execute('SELECT id FROM ka.projects WHERE archived_at IS NULL').fetchall()
    if len(rows) != 1:
        raise ValueError(UNAVAILABLE)
    return rows[0]['id']


def short(text: str | None) -> str:
    text = (text or '').strip()
    return text if len(text) <= SHORT else text[:SHORT].rstrip() + '…'


def dumps(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


SERVER = """SELECT id,name,hostname,port,username,description,tags,allow_sudo FROM ka.servers
    WHERE id=%s AND project_id=%s"""
SERVICE = """SELECT id,name,service_type,service_role,description,owner,operations_notes FROM ka.services
    WHERE id=%s AND project_id=%s"""
ENVIRONMENT = 'SELECT id,name,description FROM ka.environments WHERE id=%s AND project_id=%s'
CODE_PROJECT = """SELECT id,name,remote_url,requested_ref AS ref,current_commit FROM ka.repositories
    WHERE id=%s AND project_id=%s AND archived_at IS NULL"""
ATTACHED = """SELECT d.id,d.title FROM ka.inventory_documents a JOIN ka.document_details d ON d.id=a.document_id
    WHERE a.{column}=%s ORDER BY lower(d.title),d.id LIMIT {{limit}}"""
# Each query lists one kind of link: (type, id, name, relation, ...facts); {limit} caps it one past MAX_ITEMS.
SERVER_LINKS = (
    """SELECT 'environment' AS type,e.id,e.name,'in_environment' AS relation FROM ka.environment_servers es
    JOIN ka.environments e ON e.id=es.environment_id WHERE es.server_id=%s ORDER BY lower(e.name),e.id LIMIT {limit}""",
    """SELECT 'service' AS type,s.id,s.name,'runs' AS relation,COALESCE(e.name,d.environment) AS environment,d.runtime,
    d.runtime_name,d.port,d.deployed_version AS version FROM ka.service_deployments d JOIN ka.services s ON s.id=d.service_id
    LEFT JOIN ka.environments e ON e.id=d.environment_id WHERE d.server_id=%s
    ORDER BY lower(s.name),environment,d.runtime_name,s.id LIMIT {limit}""",
)
SERVICE_LINKS = (
    """SELECT 'server' AS type,h.id,h.name,'deployed_on' AS relation,COALESCE(e.name,d.environment) AS environment,
    d.runtime,d.runtime_name,d.port,d.deployed_version AS version FROM ka.service_deployments d
    JOIN ka.servers h ON h.id=d.server_id LEFT JOIN ka.environments e ON e.id=d.environment_id WHERE d.service_id=%s
    ORDER BY lower(h.name),environment,d.runtime_name,h.id LIMIT {limit}""",
    """SELECT 'environment' AS type,e.id,e.name,'in_environment' AS relation FROM ka.environment_services es
    JOIN ka.environments e ON e.id=es.environment_id WHERE es.service_id=%s ORDER BY lower(e.name),e.id LIMIT {limit}""",
    # depends_on (needed_by from the other side), built_from and connects_to (connected_from); the other end's name
    # from inventory_entries
    """SELECT CASE WHEN l.from_id=%(id)s THEN l.to_kind ELSE l.from_kind END AS type,
    CASE WHEN l.from_id=%(id)s THEN l.to_id ELSE l.from_id END AS id,o.name,
    CASE WHEN l.from_id=%(id)s THEN l.kind WHEN l.kind='depends_on' THEN 'needed_by' ELSE 'connected_from' END AS relation,
    l.description FROM ka.inventory_links l JOIN ka.inventory_entries o
    ON o.id=CASE WHEN l.from_id=%(id)s THEN l.to_id ELSE l.from_id END
    WHERE l.from_id=%(id)s OR l.to_id=%(id)s
    ORDER BY l.kind,lower(o.name),o.id LIMIT {limit}""",
)
ENVIRONMENT_LINKS = (
    """SELECT 'service' AS type,s.id,s.name,'contains' AS relation FROM ka.environment_services es
    JOIN ka.services s ON s.id=es.service_id WHERE es.environment_id=%s ORDER BY lower(s.name),s.id LIMIT {limit}""",
    """SELECT 'server' AS type,s.id,s.name,'contains' AS relation FROM ka.environment_servers es
    JOIN ka.servers s ON s.id=es.server_id WHERE es.environment_id=%s ORDER BY lower(s.name),s.id LIMIT {limit}""",
)
CODE_PROJECT_LINKS = (
    """SELECT 'service' AS type,s.id,s.name,'builds' AS relation FROM ka.inventory_links l
    JOIN ka.services s ON s.id=l.from_service_id WHERE l.kind='built_from' AND l.to_code_project_id=%s
    ORDER BY lower(s.name),s.id LIMIT {limit}""",
)
READS = {
    'server': (SERVER, SERVER_LINKS, 'server_id'),
    'service': (SERVICE, SERVICE_LINKS, 'service_id'),
    'environment': (ENVIRONMENT, ENVIRONMENT_LINKS, 'environment_id'),
    'code_project': (CODE_PROJECT, CODE_PROJECT_LINKS, 'code_project_id'),
}


def _given(config: dict[str, Any], kind: str, resource_id: str) -> dict | None:
    """The run's pinned entry of this resource, or None when it was not given. A refused resource and a missing one
    answer alike, so a run cannot learn what exists outside its allowlist."""
    try:
        return allowed(config, kind, resource_id)
    except ValueError:
        return None


def _rows(conn, query: str, resource_id: UUID) -> list[dict]:
    """One link or document query, MAX_ITEMS + 1 rows at most (the extra one says the list was cut)."""
    limited = query.replace('{limit}', str(MAX_ITEMS + 1))
    parameters = {'id': resource_id} if '%(id)s' in limited else (resource_id,)
    return conn.execute(limited, parameters).fetchall()


def _server_extras(config: dict[str, Any], entry: dict) -> dict[str, Any]:
    """What the run's configuration says about the server: its connection as pinned (a saved agent may connect with
    its own username, port and sudo), its instructions, and its skills and MCP servers when the run uses them."""
    extras = {key: entry[key] for key in ('port', 'username', 'allow_sudo') if key in entry}
    extras['instructions'] = entry.get('instructions') or ''
    if (config.get('skills') or {}).get('enabled'):
        extras['skills'] = [skill for skill in entry.get('skills', []) if skill.get('enabled')]
    if (config.get('mcp') or {}).get('enabled'):
        extras['mcp_server_ids'] = [server['id'] for server in entry.get('mcp_servers', []) if server.get('enabled')]
    return extras


def _resource(config: dict[str, Any], resource_type: str, resource_id: str) -> str:
    refused = NOT_GIVEN.format(type=resource_type, id=resource_id)
    try:
        key = UUID(str(resource_id))
    except ValueError:
        return refused
    entry = _given(config, resource_type, str(key))
    if entry is None:
        return refused
    fields, link_queries, column = READS[resource_type]
    with connect() as conn:
        row = conn.execute(fields, (key, project_of(conn, config))).fetchone()
        if row is None:
            return refused
        links = [link for query in link_queries for link in _rows(conn, query, key)]
        documents = _rows(conn, ATTACHED.format(column=column), key)
    found = {'type': resource_type, **row}
    if resource_type == 'server':
        found.update(_server_extras(config, entry))
    return dumps(
        {
            **found,
            'links': links[:MAX_ITEMS],
            'documents': documents[:MAX_ITEMS],
            'truncated': len(links) > MAX_ITEMS or len(documents) > MAX_ITEMS,
        }
    )


async def get_resource(context: RunContext, resource_type: ResourceType, resource_id: str) -> str:
    return await asyncio.to_thread(_resource, context['config'], resource_type, resource_id)
