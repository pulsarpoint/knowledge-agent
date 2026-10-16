"""The agent config: one shape for agents, the model rules, and loading the resolved snapshot. Loading never
decrypts credentials or starts a runtime."""

import hashlib
import json
import os
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
from typing import Any, NotRequired, TypedDict

import psycopg
from fastapi.encoders import jsonable_encoder
from knowledge_llm import code_compatible
from psycopg.rows import dict_row

from knowledge_admin.data_root import data_path

AD_HOC_AGENT = 'ad-hoc'  # the config id and run-record agent of a Generate documents session (no agents row)
AD_HOC_NAME = 'Ad-hoc agent'
MODEL = """SELECT id,name,provider,model,api_format,base_url,aws_region,enabled,revision,
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
    documents: dict[str, Any]
    code: dict[str, Any]
    skills: dict[str, Any]
    mcp: dict[str, Any]
    ssh: dict[str, Any]
    services: dict[str, Any]
    document_access: list[dict[str, Any]]
    access: dict[str, Any]  # as stored on the agent; a run pins its own (sessions.run_access)
    run_resources: NotRequired[dict[str, list[str]]]


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


def mcp_definitions(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The MCP servers a configuration may connect to."""
    return list(config['mcp']['servers']) if config['mcp']['enabled'] else []


def credential_references(config: Mapping[str, Any]) -> list[dict[str, Any] | None]:
    """The credential references of the enabled parts of a configuration (None: a part without one): the model key,
    the SSH keys of usable servers and the tokens of enabled MCP servers. Nothing is decrypted here."""
    references = [config['model']['api_key_ref']] if config['model'] is not None else []
    if config['ssh']['enabled']:
        references += [
            server['private_key_ref']
            for server in config['ssh']['servers']
            if server['enabled'] and server['ssh_allowed']
        ]
    references += [server['bearer_token_ref'] for server in mcp_definitions(config) if server['enabled']]
    return references


# Each section of sections() and the agent tab it is edited on (the clone Apply dialog names the tabs).
SECTIONS: dict[str, str] = {
    'prompt': 'General',
    'instructions': 'General',
    'when_to_use': 'General',
    'model': 'General',
    'grounded': 'General',
    'resources': 'Resources',
}


def _access(access: Mapping[str, Any]) -> dict[str, Any]:
    """The stored access object in a comparable form: references sorted, editable on documents only."""
    references = [
        {
            'type': r['type'],
            'id': str(r['id']),
            **({'editable': bool(r.get('editable'))} if r['type'] == 'document' else {}),
        }
        for r in access.get('resources', [])
    ]
    return {
        'resources': sorted(references, key=lambda r: (r['type'], r['id'])),
        'content_editor': bool(access.get('roles', {}).get('content_editor')),
    }


def sections(config: Mapping[str, Any]) -> dict[str, Any]:
    """What an agent runs with, by section (SECTIONS): everything except its identity (id, name, description, on/off),
    revisions and live data (the items its access references). Equal sections run the same. Clone and Apply compare
    these."""
    return {
        'prompt': config['system_prompt']['content'],
        'instructions': config['instructions'],
        'when_to_use': config['when_to_use'],
        'model': str(config['model']['id']) if config['model'] else None,
        'grounded': bool(config['documents'].get('grounded', False)),
        'resources': _access(config['access']),
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
COLLECTIONS = """SELECT c.id,c.name,c.description,
    ARRAY(SELECT p.source_path FROM ka.rag_collection_paths p WHERE p.collection_id=c.id ORDER BY p.source_path) AS source_paths
    FROM ka.rag_collections c WHERE c.project_id=%s AND c.id=ANY(%s::uuid[]) AND c.deleted_at IS NULL ORDER BY c.name"""
CODE_PROJECTS = """SELECT r.id,r.name,r.description,r.remote_url AS remote_git_url,
    ARRAY(SELECT f.path::text FROM ka.code_project_folders f WHERE f.code_project_id=r.id ORDER BY f.path) AS folder_paths
    FROM ka.repositories r WHERE r.project_id=%s AND r.id=ANY(%s::uuid[]) AND r.archived_at IS NULL
    ORDER BY lower(r.name),r.id"""
SERVICES = """SELECT s.id,s.name,s.description,s.owner,s.revision,s.repositories,s.deployments,s.dependencies,
    s.dependents,s.links FROM ka.service_assets s WHERE s.project_id=%s AND s.id=ANY(%s::uuid[])
    ORDER BY lower(s.name),s.id"""
SKILLS = """SELECT id,name,description,enabled,revision FROM ka.agent_skills
    WHERE project_id=%s AND id=ANY(%s::uuid[]) ORDER BY name,id"""
MCP_SERVERS = """SELECT id,name,description,url,enabled,tools,revision,token_ciphertext IS NOT NULL AS has_token
    FROM ka.mcp_servers WHERE project_id=%s AND id=ANY(%s::uuid[]) ORDER BY lower(name),id"""
# A server with its own SSH settings.
SERVERS = """SELECT s.id,s.name,s.hostname,s.description,s.tags,s.ssh_allowed,s.revision AS server_revision,
    s.username,s.port,s.allow_sudo,
    coalesce(s.known_hosts,'') AS known_hosts,s.enabled,
    c.id AS credential_id,c.project_id AS credential_project_id,c.key_version
    FROM ka.servers s LEFT JOIN ka.credentials c ON c.id=s.credential_id
    WHERE s.project_id=%s AND s.id=ANY(%s::uuid[]) ORDER BY lower(s.name),s.id"""
DOCUMENTS = 'SELECT id,title FROM ka.live_documents WHERE project_id=%s AND id=ANY(%s::uuid[])'


def access_ids(access: Mapping[str, Any], type_: str) -> list[str]:
    """The ids the access object references of one type, in its order."""
    return [str(entry['id']) for entry in access.get('resources', []) if entry['type'] == type_]


# The referenced code projects, collections, servers and services that still exist.
EXISTING = {
    'code_project': 'SELECT id::text AS id FROM ka.repositories WHERE id=ANY(%s::uuid[]) AND archived_at IS NULL',
    'collection': 'SELECT id::text AS id FROM ka.rag_collections WHERE id=ANY(%s::uuid[]) AND deleted_at IS NULL',
    'server': 'SELECT id::text AS id FROM ka.servers WHERE id=ANY(%s::uuid[])',
    'service': 'SELECT id::text AS id FROM ka.services WHERE id=ANY(%s::uuid[])',
}


def without_missing(conn, access: Mapping[str, Any]) -> dict[str, Any]:
    """The access object without the code projects, collections, servers and services it references that no longer
    exist (the kinds an expert's accepted resources count)."""
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
    """The items of these ids by type (collection, code_project, service, skill, mcp_server, server) in config shape:
    only those that still exist; paths in the agent's container, credentials as references."""
    code_root = _code_root()

    def load(query: str, type_: str) -> list[dict[str, Any]]:
        found = ids.get(type_) or []
        return conn.execute(query, (project_id, found)).fetchall() if found and project_id else []

    collections = load(COLLECTIONS, 'collection')
    for item in collections:
        item['paths'] = [str(data_path(path)) for path in item.pop('source_paths')]
    code_projects = load(CODE_PROJECTS, 'code_project')
    for project in code_projects:
        project['folder_paths'] = [str(code_root / path) for path in project['folder_paths']]
        if project['remote_git_url'] is None:
            del project['remote_git_url']
    services = load(SERVICES, 'service')
    for service in services:
        for repository in service['repositories']:
            if repository['checkout_path'] is not None:
                repository['checkout_path'] = str(code_root / repository['checkout_path'])
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
        'collection': collections,
        'code_project': code_projects,
        'service': services,
        'skill': load(SKILLS, 'skill'),
        'mcp_server': mcp_servers,
        'server': servers,
    }


LOADED = ('collection', 'code_project', 'service', 'skill', 'mcp_server', 'server')
# The sections that hold resources a given resource brings (its children), by type.
CHILD_SECTIONS = {'code_project': ('code', 'projects'), 'server': ('ssh', 'servers'), 'service': ('services', 'items')}


def add_children(conn, project_id, config: dict[str, Any], ids: Mapping[str, list[str]]) -> dict[str, Any]:
    """The configuration with these code projects, servers and services added to its sections (those it lists
    already stay as they are; a section with items is enabled) and the graphs of the added code projects."""
    config = dict(config)
    wanted = {}
    for type_, (section, key) in CHILD_SECTIONS.items():
        listed = {str(item['id']) for item in config[section][key]}
        wanted[type_] = [str(item) for item in dict.fromkeys(ids.get(type_) or []) if str(item) not in listed]
    loaded = jsonable_encoder(load_items(conn, project_id, wanted))
    for type_, (section, key) in CHILD_SECTIONS.items():
        items = [*config[section][key], *loaded[type_]]
        config[section] = {**config[section], 'enabled': bool(items), key: items}
    if loaded['code_project']:
        added = load_graphs(conn, project_id, [item['id'] for item in loaded['code_project']])
        config['code'] = {**config['code'], 'graphs': [*config['code'].get('graphs', []), *jsonable_encoder(added)]}
    return config


def get_agent_config(agent_id: str, *, conninfo: str = '') -> dict[str, object]:
    """Return one JSON-compatible configuration snapshot (AgentConfig) for an agent: its access object (`access`, as
    stored: references and roles), its instructions and the items its access references loaded into the sections
    the tools read (code projects, collections, SSH servers, services, skills, MCP servers; documents as
    document_access). A section is enabled when it has items; a reference to an item that no longer exists is
    skipped. Servers use their own SSH settings. A run adds the children of what it was given (add_children).

    Uses the standard PG* environment variables when conninfo is omitted. Reads
    only: no defaults are saved, credentials decrypted, or tools/SDKs started.
    CODE_ROOT and DATA_ROOT_DIR (where every collection folder lives) must match absolute mounts in the agent's container. Paths are joined without resolving them on the loader's host.

    This is the application's input to agent(), not native Agents SDK constructor
    keyword arguments. Loading it never starts the SDK or resolves secrets.
    """
    with psycopg.connect(conninfo, row_factory=dict_row, connect_timeout=5) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        agent = conn.execute(
            """SELECT id,name,description,when_to_use,enabled,revision,model_id,system_prompt,
            documents_grounded,access,instructions FROM ka.agents WHERE id=%s""",
            (agent_id,),
        ).fetchone()
        if agent is None:
            raise LookupError(f'Agent {agent_id!r} was not found')
        projects = conn.execute('SELECT id FROM ka.projects WHERE archived_at IS NULL').fetchall()
        if len(projects) > 1:
            raise ValueError('Agent configuration requires a single application database')
        project_id = projects[0]['id'] if projects else None
        access = agent['access']
        items = load_items(conn, project_id, {type_: access_ids(access, type_) for type_ in LOADED})
        editable = {
            str(entry['id']): bool(entry.get('editable'))
            for entry in access.get('resources', [])
            if entry['type'] == 'document'
        }
        found = access_ids(access, 'document')
        documents = (
            {str(row['id']): row for row in conn.execute(DOCUMENTS, (project_id, found))}
            if found and project_id
            else {}
        )
        document_access = [
            {'id': key, 'title': documents[key]['title'], 'editable': editable[key]}
            for key in found
            if key in documents
        ]
        collections, code_projects = items['collection'], items['code_project']
        skills, mcp_servers, servers, services = items['skill'], items['mcp_server'], items['server'], items['service']

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
                'documents': {
                    'enabled': bool(collections),
                    'grounded': agent['documents_grounded'],
                    'all_collections': False,
                    'collections': collections,
                },
                'code': {
                    'enabled': bool(code_projects),
                    'all_projects': False,
                    'projects': code_projects,
                    'graphs': load_graphs(conn, project_id, [p['id'] for p in code_projects]),
                },
                'skills': {'enabled': bool(skills), 'items': skills},
                'mcp': {'enabled': bool(mcp_servers), 'servers': mcp_servers},
                'ssh': {'enabled': bool(servers), 'servers': servers},
                'services': {'enabled': bool(services), 'items': services},
                'document_access': document_access,
                'access': access,
            }
        )
