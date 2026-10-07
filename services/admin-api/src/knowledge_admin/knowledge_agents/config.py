"""The agent config: one shape for Native and Claude Code agents, the agent type and model rules, and loading the
resolved snapshot. Loading never decrypts credentials or starts a runtime."""
import os
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
from typing import Any, Literal, NotRequired, TypedDict
from uuid import UUID

import psycopg
from fastapi.encoders import jsonable_encoder
from knowledge_llm import claude_model, code_compatible
from psycopg.rows import dict_row

from knowledge_admin.data_root import data_path

Runtime = Literal['openai', 'claude_code']
ClaudeCodeTool = Literal['Read', 'Grep', 'Glob', 'Bash']
CLAUDE_CODE_TOOLS: tuple[str, ...] = ('Read', 'Grep', 'Glob', 'Bash')
CHOOSE_CLAUDE = 'Choose a Claude model for Claude Code agents'
NATIVE_ONLY = 'Claude Code agents run in Claude Code sessions. Open the agent’s Test tab to start one.'


class AgentConfig(TypedDict):
    """The resolved config of one agent, the same for both types. Versions store this shape."""
    id: str
    name: str
    description: str
    when_to_use: str
    enabled: bool
    revision: int
    runtime: Runtime
    claude_code_tools: list[ClaudeCodeTool]
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
    config_version: NotRequired[dict[str, Any]]


def normalize_tools(values: Iterable[str]) -> list[str]:
    """Claude Code tools in their fixed order, each once."""
    chosen = set(values)
    return [tool for tool in CLAUDE_CODE_TOOLS if tool in chosen]


def model_fits(runtime: str, model: Mapping[str, Any], *, coordinator: bool = False) -> bool:
    """Claude Code agents need a Claude connection; native specialists a Responses or Claude connection; the
    coordinator any chat model."""
    if model['api_format'] == 'embeddings':
        return False
    if runtime == 'claude_code':
        return claude_model(model['provider'], model['api_format'], model['model'])
    return coordinator or code_compatible(model['provider'], model['api_format'], model['model'])


def require_native(config: Mapping[str, Any]) -> None:
    """Refuse a Claude Code agent where the native runtime would run it (test chats, live runs, the board)."""
    if config.get('runtime', 'openai') != 'openai':
        raise ValueError(NATIVE_ONLY)


def get_agent_config(agent_id: str, *, conninfo: str = "", version_id: UUID | None = None, working_copy: bool = False) -> dict[str, object]:
    """Return one JSON-compatible configuration snapshot (AgentConfig) for an agent of
    either type.

    Uses the standard PG* environment variables when conninfo is omitted. Reads
    only: no defaults are saved, credentials decrypted, or tools/SDKs started.
    Disabled capabilities retain their saved selections and explicit flags.
    CODE_ROOT and DATA_ROOT_DIR (where every collection folder lives) must match absolute mounts in the agent's container. Paths are joined without resolving them on the loader's host.

    This is the application's input to agent(), not native Agents SDK constructor
    keyword arguments. Loading it never starts the SDK or resolves secrets.
    """
    with psycopg.connect(conninfo, row_factory=dict_row, connect_timeout=5) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        agent = conn.execute("""SELECT id,name,description,when_to_use,enabled,revision,model_id,is_coordinator,runtime,claude_code_tools,
            documents_enabled,documents_grounded,repositories_enabled,ssh_enabled,skills_enabled,mcp_enabled,services_enabled,
            all_collections,all_repositories,active_config_version_id FROM ka.agents WHERE id=%s""", (agent_id,)).fetchone()
        if agent is None:
            raise LookupError(f"Agent {agent_id!r} was not found")
        if agent['is_coordinator']:
            raise ValueError('The coordinator uses its own Agents SDK configuration on the board.')
        selected_version = version_id or (None if working_copy else agent['active_config_version_id'])
        if selected_version is not None:
            version = conn.execute('SELECT id,version,label,config FROM ka.agent_versions WHERE id=%s AND agent=%s', (selected_version,agent_id)).fetchone()
            if version is None:
                raise LookupError('Version not found for this agent.')
            config = version['config']
            config['enabled'] = agent['enabled']
            config['runtime'] = agent['runtime']  # the agent type belongs to the agent, never to a version
            config.setdefault('claude_code_tools', list(CLAUDE_CODE_TOOLS))  # versions saved before 0072
            config['config_version'] = jsonable_encoder({key:version[key] for key in ('id','version','label')})
            return config
        prompt = conn.execute("""SELECT v.id,v.title,v.version,v.revision,v.content
            FROM ka.agent_prompts p JOIN ka.agent_prompt_versions v
            ON v.agent=p.agent AND v.id=p.active_version_id WHERE p.agent=%s""", (agent_id,)).fetchone()
        if prompt is None:
            raise ValueError(f"Agent {agent_id!r} has no active system prompt")

        code_root = PurePosixPath(os.environ.get('CODE_ROOT', ''))
        if code_root.anchor != '/' or '..' in code_root.parts:
            raise ValueError('CODE_ROOT must be an absolute path mounted in the agent container')

        projects = conn.execute("SELECT id FROM ka.projects WHERE archived_at IS NULL").fetchall()
        if len(projects) > 1:
            raise ValueError("Agent configuration requires a single application database")
        project_id = projects[0]['id'] if projects else None
        configs = conn.execute("""SELECT id,server_id,instructions,inherit_skills,inherit_mcp_servers,revision
            FROM ka.agent_configs WHERE agent=%s AND project_id=%s""", (agent_id, project_id)).fetchall()
        config_by_server = {row['server_id']: row for row in configs}
        agent_settings = config_by_server.get(None)
        instructions = agent_settings['instructions'] if agent_settings is not None else ''

        model = conn.execute("""SELECT id,name,provider,model,api_format,base_url,aws_region,enabled,revision,
            api_key_ciphertext IS NOT NULL AS has_api_key FROM ka.llm_models WHERE id=%s""",
            (agent['model_id'],)).fetchone()
        if model is not None:
            model['api_key_ref'] = {
                'table': 'ka.llm_models', 'id': model['id'], 'column': 'api_key_ciphertext',
                'key_env': 'CREDENTIAL_ENCRYPTION_KEY', 'aad': f"llm:{model['id']}:v1",
            } if model.pop('has_api_key') else None

        collections = conn.execute("""SELECT c.id,c.name,c.description,
            ARRAY(SELECT p.source_path FROM ka.rag_collection_paths p WHERE p.collection_id=c.id ORDER BY p.source_path) AS source_paths
            FROM ka.rag_collections c
            LEFT JOIN ka.agent_collections a ON a.collection_id=c.id AND a.agent_id=%s
            WHERE c.project_id=%s AND c.deleted_at IS NULL AND (%s OR a.collection_id IS NOT NULL)
            ORDER BY c.name""", (agent_id, project_id, agent['all_collections'])).fetchall()
        for item in collections:
            item['paths'] = [str(data_path(path)) for path in item.pop('source_paths')]
        code_projects = conn.execute("""SELECT r.id,r.name,r.description,r.remote_url AS remote_git_url,
            ARRAY(SELECT f.path::text FROM ka.code_project_folders f WHERE f.code_project_id=r.id ORDER BY f.path) AS folder_paths
            FROM ka.repositories r LEFT JOIN ka.agent_repositories a ON a.repository_id=r.id AND a.agent_id=%s
            WHERE r.project_id=%s AND r.archived_at IS NULL AND (%s OR a.repository_id IS NOT NULL)
            ORDER BY lower(r.name),r.id""", (agent_id, project_id, agent['all_repositories'])).fetchall()
        for project in code_projects:
            project['folder_paths'] = [str(code_root / path) for path in project['folder_paths']]
            if project['remote_git_url'] is None:
                del project['remote_git_url']
        services = conn.execute("""SELECT s.id,s.name,s.description,s.owner,s.operations_notes,
            s.revision,s.repositories,s.deployments,s.dependencies,s.dependents FROM ka.service_assets s
            JOIN ka.agent_services a ON a.service_id=s.id WHERE a.agent_id=%s AND s.project_id=%s
            ORDER BY lower(s.name),s.id""", (agent_id, project_id)).fetchall()
        for service in services:
            for repository in service['repositories']:
                if repository['checkout_path'] is not None:
                    repository['checkout_path'] = str(code_root / repository['checkout_path'])

        skills = conn.execute("""SELECT s.id,s.name,s.description,s.instructions,s.enabled,s.revision,
            a.agent_id IS NOT NULL AS selected FROM ka.agent_skills s
            LEFT JOIN ka.skills_agents a ON a.skill_id=s.id AND a.agent_id=%s
            WHERE s.project_id=%s AND (a.agent_id IS NOT NULL OR EXISTS (
                SELECT 1 FROM ka.agent_config_skills cs JOIN ka.agent_configs c ON c.id=cs.config_id
                WHERE cs.skill_id=s.id AND c.agent=%s AND c.project_id=s.project_id))
            ORDER BY s.name,s.id""", (agent_id, project_id, agent_id)).fetchall()
        agent_skills = []
        for skill in skills:
            if skill.pop('selected'):
                agent_skills.append(skill)
        skills_by_id = {skill['id']: skill for skill in skills}
        skills_by_config = {config['id']: [] for config in configs}
        for row in conn.execute("""SELECT s.config_id,s.skill_id FROM ka.agent_config_skills s
            JOIN ka.agent_configs c ON c.id=s.config_id WHERE c.agent=%s AND c.project_id=%s
            ORDER BY s.skill_id""", (agent_id, project_id)):
            skills_by_config[row['config_id']].append(skills_by_id[row['skill_id']])

        mcp_servers = conn.execute("""SELECT m.id,m.name,m.description,m.url,m.enabled,m.tools,m.revision,
            m.token_ciphertext IS NOT NULL AS has_token FROM ka.mcp_servers m
            WHERE m.project_id=%s AND EXISTS (
                SELECT 1 FROM ka.agent_config_mcp_servers s JOIN ka.agent_configs c ON c.id=s.config_id
                WHERE s.mcp_server_id=m.id AND c.agent=%s AND c.project_id=m.project_id)
            ORDER BY lower(m.name),m.id""", (project_id, agent_id)).fetchall()
        for server in mcp_servers:
            server['bearer_token_ref'] = {
                'table': 'ka.mcp_servers', 'id': server['id'], 'column': 'token_ciphertext',
                'key_env': 'CREDENTIAL_ENCRYPTION_KEY', 'aad': f"mcp:{server['id']}:v1",
            } if server.pop('has_token') else None
        mcp_by_id = {server['id']: server for server in mcp_servers}
        mcp_by_config = {config['id']: [] for config in configs}
        for row in conn.execute("""SELECT s.config_id,s.mcp_server_id FROM ka.agent_config_mcp_servers s
            JOIN ka.agent_configs c ON c.id=s.config_id WHERE c.agent=%s AND c.project_id=%s
            ORDER BY s.mcp_server_id""", (agent_id, project_id)):
            mcp_by_config[row['config_id']].append(mcp_by_id[row['mcp_server_id']])
        agent_mcp = mcp_by_config[agent_settings['id']] if agent_settings is not None else []

        servers = conn.execute("""SELECT s.id,s.name,s.hostname,s.description,s.tags,s.ssh_allowed,
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
            ORDER BY lower(s.name),s.id""", (agent_id, project_id)).fetchall()
        for server in servers:
            credential_id = server.pop('credential_id')
            credential_project_id = server.pop('credential_project_id')
            key_version = server.pop('key_version')
            server['private_key_ref'] = {
                'table': 'ka.credentials', 'id': credential_id, 'column': 'ciphertext',
                'key_env': 'CREDENTIAL_ENCRYPTION_KEY', 'key_version': key_version,
                'aad': f'{credential_project_id}:{credential_id}:ssh_private_key:{key_version}',
            } if credential_id is not None else None
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

        return jsonable_encoder({
            'id': agent['id'], 'name': agent['name'], 'description': agent['description'],
            'when_to_use': agent['when_to_use'], 'enabled': agent['enabled'], 'revision': agent['revision'],
            'runtime': agent['runtime'], 'claude_code_tools': list(agent['claude_code_tools']),
            'model': model, 'system_prompt': prompt, 'instructions': instructions,
            'settings_revision': agent_settings['revision'] if agent_settings is not None else None,
            'documents': {'enabled': agent['documents_enabled'], 'grounded': agent['documents_grounded'],
                'all_collections': agent['all_collections'], 'collections': collections},
            'code': {'enabled': agent['repositories_enabled'], 'all_projects': agent['all_repositories'],
                'projects': code_projects},
            'skills': {'enabled': agent['skills_enabled'], 'items': agent_skills},
            'mcp': {'enabled': agent['mcp_enabled'], 'servers': agent_mcp},
            'ssh': {'enabled': agent['ssh_enabled'], 'servers': servers},
            'services': {'enabled': agent['services_enabled'], 'items': services},
        })

