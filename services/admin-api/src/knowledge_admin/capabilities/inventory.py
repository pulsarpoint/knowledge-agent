"""Read the inventory for an agent: servers, services, code projects and the documents attached to them. Read-only.
The tools run where agents run (agent-session-worker, admin-api); configure() gives them that process's database.
The workspace is the run's (a discovery run pins config['project_id']) or the single application's. Each tool answers
only about a server, service or code project the run was given (its allowlist); anything else reads as missing."""
import asyncio
import json
from typing import Any
from uuid import UUID

from knowledge_admin.capabilities.context import KIND_NAMES, NOT_AVAILABLE, RunContext, allowed

MAX_ITEMS = 100
SHORT = 300
UNAVAILABLE = 'The inventory is not available in this run.'
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


def listed(rows: list[dict]) -> dict[str, Any]:
    return {'items': rows[:MAX_ITEMS], 'truncated': len(rows) > MAX_ITEMS}


def dumps(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


SERVER = '''SELECT id,name,hostname,description,tags FROM ka.servers WHERE project_id=%(project)s
    AND (lower(hostname)=lower(%(key)s) OR lower(name)=lower(%(key)s))
    ORDER BY lower(hostname)=lower(%(key)s) DESC,id LIMIT 1'''
SERVICE = '''SELECT id,name,service_type,service_role,description,owner,operations_notes FROM ka.services
    WHERE project_id=%s AND lower(name)=lower(%s)'''
CODE_PROJECT = '''SELECT id,name,remote_url,requested_ref,current_commit FROM ka.repositories
    WHERE project_id=%s AND archived_at IS NULL AND lower(name)=lower(%s) ORDER BY id LIMIT 1'''
ATTACHED = '''SELECT d.id,d.title,d.description FROM ka.inventory_documents a JOIN ka.document_details d ON d.id=a.document_id
    WHERE a.{column}=%s ORDER BY lower(d.title),d.id LIMIT {limit}'''
DEPLOYMENTS = '''SELECT s.name AS service,h.name AS server,COALESCE(e.name,d.environment) AS environment,d.runtime,
    d.runtime_name,d.port,d.log_location,d.deployed_version
    FROM ka.service_deployments d JOIN ka.services s ON s.id=d.service_id JOIN ka.servers h ON h.id=d.server_id
    LEFT JOIN ka.environments e ON e.id=d.environment_id WHERE d.{column}=%s
    ORDER BY lower(s.name),lower(h.name),environment,d.runtime_name LIMIT {limit}'''


def _given(config: dict[str, Any], kind: str, row: dict | None) -> bool:
    """Whether the run was given this entry. A refused entry and a missing one answer alike (_unavailable), so a run
    cannot learn what exists outside its allowlist."""
    if row is None:
        return False
    try:
        allowed(config, kind, str(row['id']))
        return True
    except ValueError:
        return False


def _unavailable(kind: str) -> str:
    return NOT_AVAILABLE.format(kind=KIND_NAMES[kind])


def _documents(conn, column: str, entry_id) -> list[dict]:
    rows = conn.execute(ATTACHED.format(column=column, limit=MAX_ITEMS + 1), (entry_id,)).fetchall()
    return [{**row, 'description': short(row['description'])} for row in rows]


def _server_info(config, hostname: str) -> str:
    with connect() as conn:
        server = conn.execute(SERVER, {'project': project_of(conn, config), 'key': hostname}).fetchone()
        if not _given(config, 'server', server):
            return _unavailable('server')
        environments = [row['name'] for row in conn.execute('''SELECT e.name FROM ka.environment_servers es
            JOIN ka.environments e ON e.id=es.environment_id WHERE es.server_id=%s ORDER BY lower(e.name)''', (server['id'],))]
        counts = conn.execute('''SELECT (SELECT count(DISTINCT service_id) FROM ka.service_deployments WHERE server_id=%(id)s) AS services,
            (SELECT count(*) FROM ka.inventory_documents WHERE server_id=%(id)s) AS documents''', {'id': server['id']}).fetchone()
    return dumps({**server, 'environments': environments, 'service_count': counts['services'],
                  'document_count': counts['documents']})


def _server_services(config, hostname: str) -> str:
    with connect() as conn:
        server = conn.execute(SERVER, {'project': project_of(conn, config), 'key': hostname}).fetchone()
        if not _given(config, 'server', server):
            return _unavailable('server')
        rows = conn.execute(f'''SELECT s.id,s.name,s.service_type,s.service_role,s.description,COALESCE(e.name,d.environment) AS environment,
            d.runtime,d.runtime_name,d.port,d.deployed_version FROM ka.service_deployments d JOIN ka.services s ON s.id=d.service_id
            LEFT JOIN ka.environments e ON e.id=d.environment_id WHERE d.server_id=%s
            ORDER BY lower(s.name),environment,d.runtime_name LIMIT {MAX_ITEMS + 1}''', (server['id'],)).fetchall()
    return dumps({'server': server['name'], **listed([{**row, 'description': short(row['description'])} for row in rows])})


def _server_docs(config, hostname: str) -> str:
    with connect() as conn:
        server = conn.execute(SERVER, {'project': project_of(conn, config), 'key': hostname}).fetchone()
        if not _given(config, 'server', server):
            return _unavailable('server')
        return dumps({'server': server['name'], **listed(_documents(conn, 'server_id', server['id']))})


def _service_info(config, name: str) -> str:
    with connect() as conn:
        project = project_of(conn, config)
        service = conn.execute(SERVICE, (project, name)).fetchone()
        if not _given(config, 'service', service):
            return _unavailable('service')
        deployments = conn.execute(DEPLOYMENTS.format(column='service_id', limit=MAX_ITEMS), (service['id'],)).fetchall()
        links = conn.execute('''SELECT l.kind,l.description,l.from_id=%(id)s AS outgoing,
            CASE WHEN l.from_id=%(id)s THEN t.kind ELSE f.kind END AS other_kind,
            CASE WHEN l.from_id=%(id)s THEN t.name ELSE f.name END AS other
            FROM ka.inventory_links l JOIN ka.inventory_entries f ON f.id=l.from_id JOIN ka.inventory_entries t ON t.id=l.to_id
            WHERE l.from_id=%(id)s OR l.to_id=%(id)s ORDER BY l.kind,other''', {'id': service['id']}).fetchall()
        documents = conn.execute(ATTACHED.format(column='service_id', limit=MAX_ITEMS), (service['id'],)).fetchall()
    return dumps({**service,
                  'deployments': [{key: value for key, value in row.items() if key != 'service'} for row in deployments],
                  'depends_on': [row['other'] for row in links if row['kind'] == 'depends_on' and row['outgoing']],
                  'needed_by': [row['other'] for row in links if row['kind'] == 'depends_on' and not row['outgoing']],
                  'built_from': [row['other'] for row in links if row['kind'] == 'built_from'],
                  'connects_to': [{'kind': row['other_kind'], 'name': row['other'], 'description': row['description']}
                                  for row in links if row['kind'] == 'connects_to'],
                  'documents': [{'id': row['id'], 'title': row['title']} for row in documents]})


def _service_docs(config, name: str) -> str:
    with connect() as conn:
        service = conn.execute(SERVICE, (project_of(conn, config), name)).fetchone()
        if not _given(config, 'service', service):
            return _unavailable('service')
        return dumps({'service': service['name'], **listed(_documents(conn, 'service_id', service['id']))})


def _project_info(config, name: str) -> str:
    with connect() as conn:
        code = conn.execute(CODE_PROJECT, (project_of(conn, config), name)).fetchone()
        if not _given(config, 'code_project', code):
            return _unavailable('code_project')
        services = [row['name'] for row in conn.execute('''SELECT s.name FROM ka.inventory_links l JOIN ka.services s ON s.id=l.from_service_id
            WHERE l.kind='built_from' AND l.to_code_project_id=%s ORDER BY lower(s.name)''', (code['id'],))]
        documents = conn.execute(ATTACHED.format(column='code_project_id', limit=MAX_ITEMS), (code['id'],)).fetchall()
    return dumps({**code, 'services': services, 'documents': [{'id': row['id'], 'title': row['title']} for row in documents]})


async def get_server_info_basic(context: RunContext, hostname: str) -> str:
    return await asyncio.to_thread(_server_info, context['config'], hostname)


async def get_server_services(context: RunContext, hostname: str) -> str:
    return await asyncio.to_thread(_server_services, context['config'], hostname)


async def get_server_docs(context: RunContext, hostname: str) -> str:
    return await asyncio.to_thread(_server_docs, context['config'], hostname)


async def get_service_info(context: RunContext, name: str) -> str:
    return await asyncio.to_thread(_service_info, context['config'], name)


async def get_service_docs(context: RunContext, name: str) -> str:
    return await asyncio.to_thread(_service_docs, context['config'], name)


async def get_project_info(context: RunContext, name: str) -> str:
    return await asyncio.to_thread(_project_info, context['config'], name)

