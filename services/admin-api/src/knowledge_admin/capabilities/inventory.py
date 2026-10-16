"""Read the inventory for an agent: get_resource answers with everything recorded about one server, service,
environment or code project, with links to related resources the model follows by their type and id. Read-only.
The tool runs where agents run (agent-session-worker, admin-api); configure() gives it that process's database.
The workspace is the run's (a resource run pins config['project_id']) or the single application's. It answers only
about a resource in the run's access object (check_get_resource); anything else, a linked resource too, reads as
missing."""

import asyncio
import json
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field

from knowledge_admin import access as access_rules
from knowledge_admin.capabilities.context import RunContext
from knowledge_admin.documents.store import inventory_document, resource_document_list
from knowledge_admin.tools import texts
from knowledge_admin.tools.registry import tool

MAX_ITEMS = 100
SHORT = 300
UNAVAILABLE = 'The inventory is not available in this run.'
ResourceType = Literal['server', 'service', 'environment', 'code_project']
AccessType = Literal[access_rules.TYPES]
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
SERVICE = """SELECT id,name,service_type,service_role,description,owner FROM ka.services
    WHERE id=%s AND project_id=%s"""
ENVIRONMENT = 'SELECT id,name,description FROM ka.environments WHERE id=%s AND project_id=%s'
CODE_PROJECT = """SELECT id,name,remote_url,requested_ref AS ref,current_commit FROM ka.repositories
    WHERE id=%s AND project_id=%s AND archived_at IS NULL"""
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
    'server': (SERVER, SERVER_LINKS),
    'service': (SERVICE, SERVICE_LINKS),
    'environment': (ENVIRONMENT, ENVIRONMENT_LINKS),
    'code_project': (CODE_PROJECT, CODE_PROJECT_LINKS),
}


def _rows(conn, query: str, resource_id: UUID) -> list[dict]:
    """One link or document query, MAX_ITEMS + 1 rows at most (the extra one says the list was cut)."""
    limited = query.replace('{limit}', str(MAX_ITEMS + 1))
    parameters = {'id': resource_id} if '%(id)s' in limited else (resource_id,)
    return conn.execute(limited, parameters).fetchall()


def _readable_documents(conn, config: dict[str, Any], documents: list[dict]) -> list[dict]:
    """The listed documents get_document would read in this run (MAX_ITEMS + 1 at most): those of a resource the run
    was given, and the others it may read (given directly, or also linked to a resource it was given)."""
    from knowledge_admin.capabilities.document_tools import readable  # document_tools imports this module

    found = []
    for document in documents:
        if len(found) > MAX_ITEMS:
            break
        resource = document['resource']
        given = access_rules.can_use(config['access'], resource['type'], str(resource['id']))
        if given or readable(conn, config['access'], document['id']):
            found.append(document)
    return found


def _server_extras(config: dict[str, Any], server_id: str) -> dict[str, Any]:
    """What the run's configuration says about the server: its connection as pinned (the server's own username, port
    and sudo)."""
    entry = next((item for item in (config.get('ssh') or {}).get('servers', []) if str(item['id']) == server_id), {})
    return {key: entry[key] for key in ('port', 'username', 'allow_sudo') if key in entry}


def check_get_resource(access: dict, resource_type: str, resource_id: str, **_) -> str | None:
    """None when allowed, else the refusal text. A refused resource and a missing one answer alike (_resource), so a
    run cannot learn what exists outside its list."""
    if not access_rules.has_any(access, resource_type):
        return access_rules.refuse_none(resource_type)
    if not access_rules.can_use(access, resource_type, resource_id):
        return access_rules.refuse_use(resource_type)
    return None


def check_get_available_resources(access: dict, **_) -> str | None:
    """Always allowed: it lists the run's own access object."""
    return None


def _resource(config: dict[str, Any], resource_type: str, resource_id: str) -> str:
    refused = access_rules.refuse_use(resource_type)
    try:
        key = UUID(str(resource_id))
    except ValueError:
        return refused
    fields, link_queries = READS[resource_type]
    with connect() as conn:
        project = project_of(conn, config)
        row = conn.execute(fields, (key, project)).fetchone()
        if row is None:
            return refused
        links = [link for query in link_queries for link in _rows(conn, query, key)]
        # The live document tagged inventory built for it, as the resource page picks it (a brand-new draft is not a
        # document get_document reads).
        inventory = inventory_document(conn, project, resource_type, key, live_only=True)
        documents = _readable_documents(conn, config, resource_document_list(conn, project, resource_type, key))
    found = {'type': resource_type, **row}
    if resource_type == 'server':
        found.update(_server_extras(config, str(key)))
    return dumps(
        {
            **found,
            'links': links[:MAX_ITEMS],
            'inventory_document': inventory and {'id': inventory['id'], 'title': inventory['title']},
            'documents': documents[:MAX_ITEMS],
            'truncated': len(links) > MAX_ITEMS or len(documents) > MAX_ITEMS,
        }
    )


@tool(usage=texts.GET_RESOURCE)
async def get_resource(
    context: RunContext,
    resource_type: Annotated[ResourceType, Field(description='The kind of resource.')],
    resource_id: Annotated[
        str,
        Field(
            min_length=1,
            max_length=64,
            description='The resource id: from the Resource line, get_available_resources or a link of an earlier get_resource.',
        ),
    ],
) -> str:
    if refusal := check_get_resource(context['config']['access'], resource_type, resource_id):
        return refusal
    return await asyncio.to_thread(_resource, context['config'], resource_type, resource_id)


@tool
async def get_available_resources(
    context: RunContext,
    resource_type: Annotated[AccessType | None, Field(description='Only resources of this type.')] = None,
) -> str:
    """The resources this run may use, as JSON: each with its type, id, name, short description and source (given to
    the run, or via the resource it belongs to). Pass a type to list only those. Use get_resource for one resource's
    details."""
    if refusal := check_get_available_resources(context['config']['access'], resource_type=resource_type):
        return refusal
    return dumps(access_rules.listing(context['config']['access'], resource_type))
