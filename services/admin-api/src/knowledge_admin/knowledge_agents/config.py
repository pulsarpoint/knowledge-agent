"""The agent config: one shape for agents, the model rules, and loading the resolved snapshot. Loading never
decrypts credentials or starts a runtime."""

import hashlib
import json
import os
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
from typing import Any, TypedDict

import psycopg
from fastapi.encoders import jsonable_encoder
from knowledge_llm import code_compatible
from psycopg.rows import dict_row

from knowledge_admin import access as access_rules
from knowledge_admin.capabilities import mcp
from knowledge_admin.capabilities.inventory import short

AD_HOC_AGENT = 'ad-hoc'  # the config id and run-record agent of a Generate documents session (no agents row)
AD_HOC_NAME = 'Ad-hoc agent'
REVIEWER_LABEL = 'Session reviewer ({name})'  # who a review run is: its start event, the memory it saves
MODEL = """SELECT id,name,provider,model,api_format,base_url,aws_region,capabilities,enabled,revision,
    api_key_ciphertext IS NOT NULL AS has_api_key FROM ka.llm_models WHERE id=%s"""


def load_model(conn, model_id) -> dict[str, Any] | None:
    """A model in config shape: its key as a reference, never decrypted."""
    model = conn.execute(MODEL, (model_id,)).fetchone() if model_id else None
    if model is not None:
        model['api_key_ref'] = (
            {
                'table': 'ka.llm_models',
                'id': model['id'],
                'column': 'api_key_ciphertext',
                'key_env': 'CREDENTIAL_ENCRYPTION_KEY',
                'aad': f'llm:{model["id"]}:v1',
            }
            if model.pop('has_api_key')
            else None
        )
    return model


def private_key_ref(credential_id, project_id, key_version) -> dict[str, Any] | None:
    """An SSH key in config shape, as a reference."""
    return (
        {
            'table': 'ka.credentials',
            'id': credential_id,
            'column': 'ciphertext',
            'key_env': 'CREDENTIAL_ENCRYPTION_KEY',
            'key_version': key_version,
            'aad': f'{project_id}:{credential_id}:ssh_private_key:{key_version}',
        }
        if credential_id is not None
        else None
    )


class AgentConfig(TypedDict):
    """The resolved config of one agent; sessions pin this shape."""

    id: str
    name: str
    description: str
    when_to_use: str
    enabled: bool
    revision: int
    model: dict[str, Any] | None
    system_prompt: dict[str, Any]
    instructions: str
    settings_revision: int | None
    access: dict[str, Any]  # what the agent holds: its stored access with each entry loaded; a run pins its own
    disabled_tools: list[str]  # the catalog tools never offered in a run of it (tools/selection.py)


def model_fits(model: Mapping[str, Any]) -> bool:
    """Agents need a Responses or Claude connection."""
    if model['api_format'] == 'embeddings':
        return False
    return code_compatible(model['provider'], model['api_format'], model['model'])


GRAPH_PROJECTS = """SELECT r.id::text AS id,r.name,r.synced_at,
    ARRAY(SELECT f.path::text FROM ka.code_project_folders f WHERE f.code_project_id=r.id ORDER BY f.path) AS folder_paths
    FROM ka.repositories r WHERE r.project_id=%s AND r.id=ANY(%s::uuid[]) AND r.archived_at IS NULL
    ORDER BY lower(r.name),r.id"""


def load_graphs(conn, project_id, ids: Iterable[str]) -> list[dict[str, Any]]:
    """The generated graphs of these code projects (live, like file contents; never taken from a version). Decided
    from the stored graph manifests: no source file is read."""
    from knowledge_admin.capabilities import code_graph  # imports the code processing stack only when needed

    ids = [str(item) for item in ids]
    if project_id is None or not ids:
        return []
    return [
        graph
        for row in conn.execute(GRAPH_PROJECTS, (project_id, ids))
        if (
            graph := code_graph.current_graph(
                {
                    'id': row['id'],
                    'name': row['name'],
                    'synced_at': row['synced_at'],
                    'folder_paths': list(row['folder_paths']),
                }
            )
        )
        is not None
    ]


def credential_references(config: Mapping[str, Any]) -> list[dict[str, Any] | None]:
    """The credential references of a configuration (None: a part without one): the model key, the SSH keys of the
    usable servers and the tokens of the MCP servers its access holds. Nothing is decrypted here."""
    references = [config['model']['api_key_ref']] if config['model'] is not None else []
    references += [
        server['private_key_ref']
        for server in access_rules.entries(config['access'], 'server')
        if access_rules.ssh_reason(server) is None  # connected: a server it cannot connect to has no key to load
    ]
    references += [server['bearer_token_ref'] for server in mcp.selected_servers(config)]
    return references


# Each section of sections() and the agent tab it is edited on (the clone Apply dialog names the tabs).
SECTIONS: dict[str, str] = {
    'prompt': 'General',
    'instructions': 'General',
    'when_to_use': 'General',
    'model': 'General',
    'disabled_tools': 'General',
    'resources': 'Resources',
}


def _access(access: Mapping[str, Any]) -> dict[str, Any]:
    """The stored access object in a comparable form: references sorted (keys an older object may still carry, such
    as roles, and references of a type that is gone, such as skills, are left out)."""
    references = [
        {'type': r['type'], 'id': str(r['id'])} for r in access.get('resources', []) if r['type'] in access_rules.TYPES
    ]
    return {'resources': sorted(references, key=lambda r: (r['type'], r['id']))}


def sections(config: Mapping[str, Any]) -> dict[str, Any]:
    """What an agent runs with, by section (SECTIONS): everything except its identity (id, name, description, on/off),
    revisions and live data (the items its access references). Equal sections run the same. Clone and Apply compare
    these."""
    return {
        'prompt': config['system_prompt']['content'],
        'instructions': config['instructions'],
        'when_to_use': config['when_to_use'],
        'model': str(config['model']['id']) if config['model'] else None,
        'disabled_tools': sorted(config.get('disabled_tools', [])),
        'resources': _access(config['access']),
    }


def known_tools(names: Iterable[str]) -> list[str]:
    """tools.selection.known: the stored off tools still in the catalog (imported here: the catalog imports the
    capabilities, which import this module)."""
    from knowledge_admin.tools.selection import known

    return known(names)


SAVED = """SELECT revision,when_to_use,model_id,system_prompt,access,instructions,disabled_tools
    FROM ka.agents WHERE id=%s"""


def saved_config(conn, agent_id: str) -> dict[str, Any]:
    """An agent's configuration as it is saved, in the shape sections() reads (and its revision): its access object's
    stored references, not loaded entries, so a deleted item does not change it. Clone compare
    and the Apply checks read this. LookupError: no such agent."""
    row = conn.execute(SAVED, (agent_id,)).fetchone()
    if row is None:
        raise LookupError(f'Agent {agent_id!r} was not found')
    return {
        'revision': row['revision'],
        'system_prompt': {'content': row['system_prompt']},
        'instructions': row['instructions'],
        'when_to_use': row['when_to_use'],
        'model': {'id': row['model_id']} if row['model_id'] else None,
        'access': row['access'],
        'disabled_tools': known_tools(row['disabled_tools']),
    }


def fingerprint(config: Mapping[str, Any]) -> str:
    """Changes whenever what the agent runs with changes, whichever tab saved it (an instructions save does not bump
    agents.revision)."""
    return hashlib.sha256(json.dumps(sections(config), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def changed_sections(source: Mapping[str, Any], target: Mapping[str, Any]) -> list[str]:
    """The tabs whose sections differ, each once, in SECTIONS order."""
    ours, theirs = sections(source), sections(target)
    return list(dict.fromkeys(label for key, label in SECTIONS.items() if ours[key] != theirs[key]))


# The items an agent's access references, by type: only those that still exist (deleted or archived ones are skipped).
CODE_PROJECTS = """SELECT r.id,r.name,r.description,
    ARRAY(SELECT f.path::text FROM ka.code_project_folders f WHERE f.code_project_id=r.id ORDER BY f.path) AS folder_paths
    FROM ka.repositories r WHERE r.project_id=%s AND r.id=ANY(%s::uuid[]) AND r.archived_at IS NULL
    ORDER BY lower(r.name),r.id"""
SERVICES = """SELECT id,name,description FROM ka.services WHERE project_id=%s AND id=ANY(%s::uuid[])
    ORDER BY lower(name),id"""
MCP_SERVERS = """SELECT id,name,description,url,enabled,token_ciphertext IS NOT NULL AS has_token
    FROM ka.mcp_servers WHERE project_id=%s AND id=ANY(%s::uuid[]) ORDER BY lower(name),id"""
# A server with its own SSH settings.
SERVERS = """SELECT s.id,s.name,s.hostname,s.description,s.ssh_allowed,s.username,s.port,s.allow_sudo,
    coalesce(s.known_hosts,'') AS known_hosts,s.enabled,
    c.id AS credential_id,c.project_id AS credential_project_id,c.key_version
    FROM ka.servers s LEFT JOIN ka.credentials c ON c.id=s.credential_id
    WHERE s.project_id=%s AND s.id=ANY(%s::uuid[]) ORDER BY lower(s.name),s.id"""
ENVIRONMENTS = """SELECT id,name,description FROM ka.environments WHERE project_id=%s AND id=ANY(%s::uuid[])
    ORDER BY lower(name),id"""
DOCUMENTS = 'SELECT id,title FROM ka.live_documents WHERE project_id=%s AND id=ANY(%s::uuid[])'


def access_ids(access: Mapping[str, Any], type_: str) -> list[str]:
    """The ids the access object references of one type, in its order."""
    return [str(entry['id']) for entry in access.get('resources', []) if entry['type'] == type_]


# The referenced code projects, servers and services that still exist.
EXISTING = {
    'code_project': 'SELECT id::text AS id FROM ka.repositories WHERE id=ANY(%s::uuid[]) AND archived_at IS NULL',
    'server': 'SELECT id::text AS id FROM ka.servers WHERE id=ANY(%s::uuid[])',
    'service': 'SELECT id::text AS id FROM ka.services WHERE id=ANY(%s::uuid[])',
}


def without_missing(conn, access: Mapping[str, Any]) -> dict[str, Any]:
    """The access object without the code projects, servers and services it references that no longer exist (the
    kinds an expert's accepted resources count)."""
    gone = set()
    for type_, query in EXISTING.items():
        ids = access_ids(access, type_)
        kept = {row['id'] for row in conn.execute(query, (ids,))} if ids else set()
        gone |= {(type_, item) for item in ids if item not in kept}
    return {**access, 'resources': [r for r in access.get('resources', []) if (r['type'], str(r['id'])) not in gone]}


def _code_root() -> PurePosixPath:
    code_root = PurePosixPath(os.environ.get('CODE_ROOT', ''))
    if code_root.anchor != '/' or '..' in code_root.parts:
        raise ValueError('CODE_ROOT must be an absolute path mounted in the agent container')
    return code_root


def load_items(conn, project_id, ids: Mapping[str, list[str]]) -> dict[str, list[dict[str, Any]]]:
    """The items of these ids by type (LOADED) in config shape: only those that still exist; paths in the agent's
    container, credentials as references."""
    code_root = _code_root()

    def load(query: str, type_: str) -> list[dict[str, Any]]:
        found = ids.get(type_) or []
        return conn.execute(query, (project_id, found)).fetchall() if found and project_id else []

    code_projects = load(CODE_PROJECTS, 'code_project')
    for project in code_projects:
        project['folder_paths'] = [str(code_root / path) for path in project['folder_paths']]
    mcp_servers = load(MCP_SERVERS, 'mcp_server')
    for server in mcp_servers:
        server['bearer_token_ref'] = (
            {
                'table': 'ka.mcp_servers',
                'id': server['id'],
                'column': 'token_ciphertext',
                'key_env': 'CREDENTIAL_ENCRYPTION_KEY',
                'aad': f'mcp:{server["id"]}:v1',
            }
            if server.pop('has_token')
            else None
        )
    servers = load(SERVERS, 'server')
    for server in servers:
        server['private_key_ref'] = private_key_ref(
            server.pop('credential_id'), server.pop('credential_project_id'), server.pop('key_version')
        )
    return {
        'code_project': code_projects,
        'service': load(SERVICES, 'service'),
        'mcp_server': mcp_servers,
        'server': servers,
        'environment': load(ENVIRONMENTS, 'environment'),
    }


LOADED = ('code_project', 'service', 'mcp_server', 'server', 'environment')
# What an entry carries of its item, besides its type, id, name and short description (access.py).
SERVER_FIELDS = (
    'hostname',
    'port',
    'username',
    'allow_sudo',
    'known_hosts',
    'enabled',
    'ssh_allowed',
    'private_key_ref',
)
MCP_FIELDS = ('url', 'enabled', 'bearer_token_ref')


def entry(type_: str, item: Mapping[str, Any], **extra) -> dict[str, Any]:
    """An access entry of one item: {type, id, name, description_short, **extra}."""
    return {
        'type': type_,
        'id': str(item['id']),
        'name': item['name'],
        'description_short': short(item.get('description')),
        **extra,
    }


def resource_entries(items: Mapping[str, list], graphs: Iterable[Mapping[str, Any]] = ()) -> list[dict[str, Any]]:
    """The access entries of loaded items by type (load_items' shape; 'document': entries already), each once: servers,
    services, code projects, MCP servers (enabled ones), documents, environments. A code project carries its folders
    and its graph (graphs: load_graphs), a server and an MCP server their connection."""
    graph_of = {str(graph['id']): graph for graph in graphs}

    def code_project(item: Mapping[str, Any]) -> dict[str, Any]:
        graph = graph_of.get(str(item['id']))
        found = {'graph_path': graph['graph_path'], 'code_root': graph['code_root']} if graph else {}
        return entry('code_project', item, folder_paths=list(item.get('folder_paths', [])), **found)

    found = [entry('server', item, **{k: item[k] for k in SERVER_FIELDS}) for item in items.get('server', [])]
    found += [entry('service', item) for item in items.get('service', [])]
    found += [code_project(item) for item in items.get('code_project', [])]
    found += [
        entry('mcp_server', item, **{k: item[k] for k in MCP_FIELDS})
        for item in items.get('mcp_server', [])
        if item['enabled']
    ]
    found += [dict(item) for item in items.get('document', [])]
    found += [entry('environment', item) for item in items.get('environment', [])]
    return list({(item['type'], item['id']): item for item in found}.values())


def load_entries(
    conn, project_id, ids: Mapping[str, list[str]], documents: Iterable[dict[str, Any]] = ()
) -> list[dict[str, Any]]:
    """The access entries of these ids by type (LOADED), of those that still exist, and these document entries
    (resource_entries)."""
    items = jsonable_encoder(load_items(conn, project_id, ids))
    graphs = jsonable_encoder(load_graphs(conn, project_id, [item['id'] for item in items['code_project']]))
    return resource_entries({**items, 'document': list(documents)}, graphs)


def get_agent_config(agent_id: str, *, conninfo: str = '') -> dict[str, object]:
    """Return one JSON-compatible configuration snapshot (AgentConfig) for an agent: its instructions and model,
    and its access object as it holds it: the stored references with each item loaded into its entry
    (resource_entries: a server with its connection, a code project with its folders and graph, ...); a reference to
    an item that no longer exists is skipped. Servers use their own SSH settings. A run is given what it picks of it
    and the children of that (sessions.run_access.agent_access).

    Uses the standard PG* environment variables when conninfo is omitted. Reads
    only: no defaults are saved, credentials decrypted, or tools/SDKs started.
    CODE_ROOT must match the absolute mount in the agent's container. Paths are joined without resolving them on the loader's host.

    This is the application's input to agent(), not native Agents SDK constructor
    keyword arguments. Loading it never starts the SDK or resolves secrets.
    """
    with psycopg.connect(conninfo, row_factory=dict_row, connect_timeout=5) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        agent = conn.execute(
            """SELECT id,name,description,when_to_use,enabled,revision,model_id,system_prompt,
            access,instructions,disabled_tools FROM ka.agents WHERE id=%s""",
            (agent_id,),
        ).fetchone()
        if agent is None:
            raise LookupError(f'Agent {agent_id!r} was not found')
        projects = conn.execute('SELECT id FROM ka.projects WHERE archived_at IS NULL').fetchall()
        if len(projects) > 1:
            raise ValueError('Agent configuration requires a single application database')
        project_id = projects[0]['id'] if projects else None
        access = agent['access']
        found = access_ids(access, 'document')
        rows = (
            {str(row['id']): row for row in conn.execute(DOCUMENTS, (project_id, found))}
            if found and project_id
            else {}
        )
        documents = [entry('document', {'id': key, 'name': rows[key]['title']}) for key in found if key in rows]
        ids = {type_: access_ids(access, type_) for type_ in LOADED}
        resources = load_entries(conn, project_id, ids, documents)
        return jsonable_encoder(
            {
                'id': agent['id'],
                'name': agent['name'],
                'description': agent['description'],
                'when_to_use': agent['when_to_use'],
                'enabled': agent['enabled'],
                'revision': agent['revision'],
                'model': load_model(conn, agent['model_id']),
                'system_prompt': {'content': agent['system_prompt']},
                'instructions': agent['instructions'],
                'settings_revision': None,
                'access': {'resources': resources},
                'disabled_tools': known_tools(agent['disabled_tools']),
            }
        )
