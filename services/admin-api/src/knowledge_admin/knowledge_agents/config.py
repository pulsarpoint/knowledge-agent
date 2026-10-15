"""The agent config: one shape for agents, the model rules, and loading the resolved snapshot. Loading never
decrypts credentials or starts a runtime."""

import hashlib
import json
import os
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
from typing import Any, Literal, NotRequired, TypedDict

import psycopg
from fastapi.encoders import jsonable_encoder
from knowledge_llm import code_compatible
from psycopg.rows import dict_row

from knowledge_admin.data_root import data_path

Resources = Literal['fixed', 'per_run']
NO_RUN_RESOURCES = 'This agent receives its resources per run, and this run was started without them.'
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
    resources: Resources
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
    tools: dict[str, list[dict[str, Any]]]
    run_resources: NotRequired[dict[str, list[str]]]


def tool_items(rows: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """The agent's tools in config shape: every catalog tool (no row: off), then MCP rows by name; each with whether
    run_subagent may give it to a subagent (no row, or a row read without it: yes)."""
    from knowledge_admin.tools.catalog import CATALOG, item  # the catalog imports this module

    saved = {row['tool']: row for row in rows}
    items = []
    for name in CATALOG:
        row = saved.get(name)
        items.append(
            item(
                name,
                row['mode'] if row else 'off',
                row['usage'] if row else None,
                subagent_allowed=row.get('subagent_allowed', True) if row else True,
            )
        )
    for name in sorted(tool for tool in saved if tool.startswith('mcp:')):
        row = saved[name]
        items.append(
            item(name, row['mode'], row['usage'], group='mcp', subagent_allowed=row.get('subagent_allowed', True))
        )
    return {'items': items}


def native_tools(conn, agent_id: str) -> dict[str, list[dict[str, Any]]]:
    return tool_items(
        conn.execute('SELECT tool,mode,usage,subagent_allowed FROM ka.agent_tools WHERE agent_id=%s', (agent_id,))
    )


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


def per_run(config: Mapping[str, Any]) -> bool:
    """Per-run agents: the saved selections are what a run may be given (versions saved before 0075 are fixed)."""
    return config.get('resources', 'fixed') == 'per_run'


def require_run_resources(config: Mapping[str, Any]) -> None:
    """A per-run agent runs only with the resources its run was given, never with its whole selection."""
    if per_run(config) and 'run_resources' not in config:
        raise ValueError(NO_RUN_RESOURCES)


def mcp_definitions(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The MCP servers a configuration may connect to: the agent's and, with MCP on, those of its usable SSH servers."""
    if not config['mcp']['enabled']:
        return []
    servers = list(config['mcp']['servers'])
    if config['ssh']['enabled']:
        for server in config['ssh']['servers']:
            if server['enabled'] and server['ssh_allowed']:
                servers.extend(server['mcp_servers'])
    return servers


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


SECTIONS: dict[str, str] = {
    'prompt': 'Prompt',
    'instructions': 'Instructions',
    'when_to_use': 'When to use',
    'model': 'Model',
    'resources': 'Resources',
    'tools': 'Tools',
    'skills': 'Skills',
    'documents': 'Documents',
    'code': 'Code projects',
    'ssh': 'SSH',
    'mcp': 'MCP',
    'services': 'Services',
}


def _ids(items: Iterable[Mapping[str, Any]]) -> list[str]:
    return sorted(str(item['id']) for item in items)


def _server(entry: Mapping[str, Any]) -> dict[str, Any]:
    """A selected SSH server: only what the agent decides (inherited values are the agent's own sections)."""

    def own(source: str) -> bool:
        return entry.get(source, 'agent') != 'agent'

    agent_connection = entry.get('connection_source', 'server') == 'agent'
    return {
        'id': str(entry['id']),
        'instructions': entry['instructions'] if own('instructions_source') else None,
        'skills': _ids(entry['skills']) if own('skills_source') else None,
        'mcp_servers': _ids(entry['mcp_servers']) if own('mcp_source') else None,
        'connection': {
            'username': entry['username'],
            'port': entry['port'],
            'allow_sudo': entry['allow_sudo'],
            'known_hosts': entry['known_hosts'],
            'credential_id': (entry.get('private_key_ref') or {}).get('id'),
        }
        if agent_connection
        else None,
    }


def sections(config: Mapping[str, Any]) -> dict[str, Any]:
    """What an agent runs with, by section (SECTIONS): everything except its identity (id, name, description, on/off),
    revisions and live data (graphs; the details of shared servers, collections, skills). Equal sections run the
    same. Clone, Apply and the 0080 migration compare these."""
    documents, code = config['documents'], config['code']
    return {
        'prompt': config['system_prompt']['content'],
        'instructions': config['instructions'],
        'when_to_use': config['when_to_use'],
        'model': str(config['model']['id']) if config['model'] else None,
        'resources': config.get('resources', 'fixed'),
        'tools': [
            {
                'tool': entry['tool'],
                'mode': entry['mode'],
                'usage': entry['usage'],
                'subagent_allowed': entry.get('subagent_allowed', True),
            }
            for entry in config['tools']['items']
        ],
        'skills': {'enabled': config['skills']['enabled'], 'items': _ids(config['skills']['items'])},
        'documents': {
            'enabled': documents['enabled'],
            'grounded': documents.get('grounded', False),
            'all_collections': documents.get('all_collections', False),
            'collections': [] if documents.get('all_collections') else _ids(documents.get('collections', [])),
        },
        'code': {
            'enabled': code['enabled'],
            'all_projects': code.get('all_projects', False),
            'projects': [] if code.get('all_projects') else _ids(code['projects']),
        },
        'ssh': {
            'enabled': config['ssh']['enabled'],
            'servers': sorted((_server(entry) for entry in config['ssh']['servers']), key=lambda s: s['id']),
        },
        'mcp': {'enabled': config['mcp']['enabled'], 'servers': _ids(config['mcp']['servers'])},
        'services': {'enabled': config['services']['enabled'], 'items': _ids(config['services']['items'])},
    }


def fingerprint(config: Mapping[str, Any]) -> str:
    """Changes whenever what the agent runs with changes, whichever tab saved it (instructions and SSH saves do not
    bump agents.revision)."""
    return hashlib.sha256(json.dumps(sections(config), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def changed_sections(source: Mapping[str, Any], target: Mapping[str, Any]) -> list[str]:
    """The labels of the sections that differ, in SECTIONS order."""
    ours, theirs = sections(source), sections(target)
    return [label for key, label in SECTIONS.items() if ours[key] != theirs[key]]


def get_agent_config(agent_id: str, *, conninfo: str = '') -> dict[str, object]:
    """Return one JSON-compatible configuration snapshot (AgentConfig) for an agent of
    either type. The agent's own configuration: what its tabs show is what runs (0080 removed configuration versions).

    Uses the standard PG* environment variables when conninfo is omitted. Reads
    only: no defaults are saved, credentials decrypted, or tools/SDKs started.
    Disabled capabilities retain their saved selections and explicit flags.
    CODE_ROOT and DATA_ROOT_DIR (where every collection folder lives) must match absolute mounts in the agent's container. Paths are joined without resolving them on the loader's host.

    This is the application's input to agent(), not native Agents SDK constructor
    keyword arguments. Loading it never starts the SDK or resolves secrets.
    """
    with psycopg.connect(conninfo, row_factory=dict_row, connect_timeout=5) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        agent = conn.execute(
            """SELECT id,name,description,when_to_use,enabled,revision,model_id,resources,system_prompt,
            documents_enabled,documents_grounded,repositories_enabled,ssh_enabled,skills_enabled,mcp_enabled,services_enabled,
            all_collections,all_repositories FROM ka.agents WHERE id=%s""",
            (agent_id,),
        ).fetchone()
        if agent is None:
            raise LookupError(f'Agent {agent_id!r} was not found')
        projects = conn.execute('SELECT id FROM ka.projects WHERE archived_at IS NULL').fetchall()
        if len(projects) > 1:
            raise ValueError('Agent configuration requires a single application database')
        project_id = projects[0]['id'] if projects else None
        code_root = PurePosixPath(os.environ.get('CODE_ROOT', ''))
        if code_root.anchor != '/' or '..' in code_root.parts:
            raise ValueError('CODE_ROOT must be an absolute path mounted in the agent container')

        configs = conn.execute(
            """SELECT id,server_id,instructions,inherit_skills,inherit_mcp_servers,revision
            FROM ka.agent_configs WHERE agent=%s AND project_id=%s""",
            (agent_id, project_id),
        ).fetchall()
        config_by_server = {row['server_id']: row for row in configs}
        agent_settings = config_by_server.get(None)
        instructions = agent_settings['instructions'] if agent_settings is not None else ''

        model = load_model(conn, agent['model_id'])

        collections = conn.execute(
            """SELECT c.id,c.name,c.description,
            ARRAY(SELECT p.source_path FROM ka.rag_collection_paths p WHERE p.collection_id=c.id ORDER BY p.source_path) AS source_paths
            FROM ka.rag_collections c
            LEFT JOIN ka.agent_collections a ON a.collection_id=c.id AND a.agent_id=%s
            WHERE c.project_id=%s AND c.deleted_at IS NULL AND (%s OR a.collection_id IS NOT NULL)
            ORDER BY c.name""",
            (agent_id, project_id, agent['all_collections']),
        ).fetchall()
        for item in collections:
            item['paths'] = [str(data_path(path)) for path in item.pop('source_paths')]
        code_projects = conn.execute(
            """SELECT r.id,r.name,r.description,r.remote_url AS remote_git_url,
            ARRAY(SELECT f.path::text FROM ka.code_project_folders f WHERE f.code_project_id=r.id ORDER BY f.path) AS folder_paths
            FROM ka.repositories r LEFT JOIN ka.agent_repositories a ON a.repository_id=r.id AND a.agent_id=%s
            WHERE r.project_id=%s AND r.archived_at IS NULL AND (%s OR a.repository_id IS NOT NULL)
            ORDER BY lower(r.name),r.id""",
            (agent_id, project_id, agent['all_repositories']),
        ).fetchall()
        for project in code_projects:
            project['folder_paths'] = [str(code_root / path) for path in project['folder_paths']]
            if project['remote_git_url'] is None:
                del project['remote_git_url']
        services = conn.execute(
            """SELECT s.id,s.name,s.description,s.owner,s.operations_notes,
            s.revision,s.repositories,s.deployments,s.dependencies,s.dependents,s.links FROM ka.service_assets s
            JOIN ka.agent_services a ON a.service_id=s.id WHERE a.agent_id=%s AND s.project_id=%s
            ORDER BY lower(s.name),s.id""",
            (agent_id, project_id),
        ).fetchall()
        for service in services:
            for repository in service['repositories']:
                if repository['checkout_path'] is not None:
                    repository['checkout_path'] = str(code_root / repository['checkout_path'])

        skills = conn.execute(
            """SELECT s.id,s.name,s.description,s.instructions,s.enabled,s.revision,
            a.agent_id IS NOT NULL AS selected FROM ka.agent_skills s
            LEFT JOIN ka.skills_agents a ON a.skill_id=s.id AND a.agent_id=%s
            WHERE s.project_id=%s AND (a.agent_id IS NOT NULL OR EXISTS (
                SELECT 1 FROM ka.agent_config_skills cs JOIN ka.agent_configs c ON c.id=cs.config_id
                WHERE cs.skill_id=s.id AND c.agent=%s AND c.project_id=s.project_id))
            ORDER BY s.name,s.id""",
            (agent_id, project_id, agent_id),
        ).fetchall()
        agent_skills = []
        for skill in skills:
            if skill.pop('selected'):
                agent_skills.append(skill)
        skills_by_id = {skill['id']: skill for skill in skills}
        skills_by_config = {config['id']: [] for config in configs}
        for row in conn.execute(
            """SELECT s.config_id,s.skill_id FROM ka.agent_config_skills s
            JOIN ka.agent_configs c ON c.id=s.config_id WHERE c.agent=%s AND c.project_id=%s
            ORDER BY s.skill_id""",
            (agent_id, project_id),
        ):
            skills_by_config[row['config_id']].append(skills_by_id[row['skill_id']])

        mcp_servers = conn.execute(
            """SELECT m.id,m.name,m.description,m.url,m.enabled,m.tools,m.revision,
            m.token_ciphertext IS NOT NULL AS has_token FROM ka.mcp_servers m
            WHERE m.project_id=%s AND EXISTS (
                SELECT 1 FROM ka.agent_config_mcp_servers s JOIN ka.agent_configs c ON c.id=s.config_id
                WHERE s.mcp_server_id=m.id AND c.agent=%s AND c.project_id=m.project_id)
            ORDER BY lower(m.name),m.id""",
            (project_id, agent_id),
        ).fetchall()
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
        mcp_by_id = {server['id']: server for server in mcp_servers}
        mcp_by_config = {config['id']: [] for config in configs}
        for row in conn.execute(
            """SELECT s.config_id,s.mcp_server_id FROM ka.agent_config_mcp_servers s
            JOIN ka.agent_configs c ON c.id=s.config_id WHERE c.agent=%s AND c.project_id=%s
            ORDER BY s.mcp_server_id""",
            (agent_id, project_id),
        ):
            mcp_by_config[row['config_id']].append(mcp_by_id[row['mcp_server_id']])
        agent_mcp = mcp_by_config[agent_settings['id']] if agent_settings is not None else []

        servers = conn.execute(
            """SELECT s.id,s.name,s.hostname,s.description,s.tags,s.ssh_allowed,
            s.revision AS server_revision,a.revision AS connection_revision,
            CASE WHEN a.server_id IS NULL THEN 'server' ELSE 'agent' END AS connection_source,
            CASE WHEN a.server_id IS NULL THEN s.username ELSE a.username END AS username,
            coalesce(a.port,s.port) AS port,coalesce(a.allow_sudo,s.allow_sudo) AS allow_sudo,
            coalesce(a.known_hosts,s.known_hosts,'') AS known_hosts,
            CASE WHEN a.server_id IS NULL THEN s.enabled
                ELSE a.credential_id IS NOT NULL AND a.username IS NOT NULL END AS enabled,
            c.id AS credential_id,c.project_id AS credential_project_id,c.key_version
            FROM ka.operations_servers selected JOIN ka.servers s ON s.id=selected.server_id
            LEFT JOIN ka.agent_server_connections a ON a.server_id=s.id AND a.agent_id=selected.agent
            LEFT JOIN ka.credentials c ON c.id=CASE WHEN a.server_id IS NULL THEN s.credential_id ELSE a.credential_id END
            WHERE selected.agent=%s AND selected.project_id=%s AND s.project_id=selected.project_id
            ORDER BY lower(s.name),s.id""",
            (agent_id, project_id),
        ).fetchall()
        for server in servers:
            credential_id = server.pop('credential_id')
            credential_project_id = server.pop('credential_project_id')
            key_version = server.pop('key_version')
            server['private_key_ref'] = private_key_ref(credential_id, credential_project_id, key_version)
            server_settings = config_by_server.get(server['id'])
            server['config_revision'] = server_settings['revision'] if server_settings is not None else None
            overrides_instructions = server_settings is not None and server_settings['instructions'] is not None
            server['instructions'] = server_settings['instructions'] if overrides_instructions else instructions
            server['instructions_source'] = 'server_override' if overrides_instructions else 'agent'
            overrides_skills = server_settings is not None and not server_settings['inherit_skills']
            server['skills'] = skills_by_config[server_settings['id']] if overrides_skills else agent_skills
            server['skills_source'] = 'server_override' if overrides_skills else 'agent'
            overrides_mcp = server_settings is not None and not server_settings['inherit_mcp_servers']
            server['mcp_servers'] = mcp_by_config[server_settings['id']] if overrides_mcp else agent_mcp
            server['mcp_source'] = 'server_override' if overrides_mcp else 'agent'

        return jsonable_encoder(
            {
                'id': agent['id'],
                'name': agent['name'],
                'description': agent['description'],
                'when_to_use': agent['when_to_use'],
                'enabled': agent['enabled'],
                'revision': agent['revision'],
                'resources': agent['resources'],
                'model': model,
                'system_prompt': {'content': agent['system_prompt']},
                'instructions': instructions,
                'settings_revision': agent_settings['revision'] if agent_settings is not None else None,
                'documents': {
                    'enabled': agent['documents_enabled'],
                    'grounded': agent['documents_grounded'],
                    'all_collections': agent['all_collections'],
                    'collections': collections,
                },
                'code': {
                    'enabled': agent['repositories_enabled'],
                    'all_projects': agent['all_repositories'],
                    'projects': code_projects,
                    'graphs': load_graphs(conn, project_id, [p['id'] for p in code_projects])
                    if agent['repositories_enabled']
                    else [],
                },
                'skills': {'enabled': agent['skills_enabled'], 'items': agent_skills},
                'mcp': {'enabled': agent['mcp_enabled'], 'servers': agent_mcp},
                'ssh': {'enabled': agent['ssh_enabled'], 'servers': servers},
                'services': {'enabled': agent['services_enabled'], 'items': services},
                'tools': native_tools(conn, agent_id),
            }
        )
