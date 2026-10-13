"""The ad-hoc agent of Docs → Generate documents: a native agent that exists only in its session. Its configuration
has the shape get_agent_config returns, without an agents row: the user's system prompt and model, every catalog tool
on (run_subagent too, never given to a subagent), and every resource of the workspace allowed, so that run_resources.resolve narrows it to exactly what the run
documents. A kind left empty is switched off, so the runtime offers only the tools of the selected kinds. Credentials
stay references."""
import os
from pathlib import PurePosixPath
from typing import Any

from fastapi.encoders import jsonable_encoder

from knowledge_admin.knowledge_agents.config import AD_HOC_AGENT, AD_HOC_NAME, private_key_ref, tool_items
from knowledge_admin.knowledge_agents.experts import pin
from knowledge_admin.knowledge_agents.run_resources import RunResources, resolve

# Every server with its own connection (an ad-hoc agent has no per-agent connections, instructions, skills or MCP).
SERVERS = """SELECT s.id,s.name,s.hostname,s.description,s.tags,s.ssh_allowed,s.revision AS server_revision,
    NULL::integer AS connection_revision,'server' AS connection_source,s.username,s.port,s.allow_sudo,
    coalesce(s.known_hosts,'') AS known_hosts,s.enabled,
    c.id AS credential_id,c.project_id AS credential_project_id,c.key_version
    FROM ka.servers s LEFT JOIN ka.credentials c ON c.id=s.credential_id
    WHERE s.project_id=%s ORDER BY lower(s.name),s.id"""
SERVICES = """SELECT id,name,description,owner,operations_notes,revision,repositories,deployments,dependencies,dependents,links
    FROM ka.service_assets WHERE project_id=%s ORDER BY lower(name),id"""
SECTIONS = (('code', 'projects'), ('documents', 'collections'), ('ssh', 'servers'), ('services', 'items'))


def _servers(conn, project_id) -> list[dict[str, Any]]:
    servers = conn.execute(SERVERS, (project_id,)).fetchall()
    for server in servers:
        server['private_key_ref'] = private_key_ref(server.pop('credential_id'), server.pop('credential_project_id'),
                                                    server.pop('key_version'))
        server.update(config_revision=None, instructions='', instructions_source='agent', skills=[],
                      skills_source='agent', mcp_servers=[], mcp_source='agent')
    return servers


def _services(conn, project_id) -> list[dict[str, Any]]:
    services = conn.execute(SERVICES, (project_id,)).fetchall()
    code_root = PurePosixPath(os.environ.get('CODE_ROOT', ''))
    for service in services:
        for repository in service['repositories']:
            if repository['checkout_path'] is not None:
                repository['checkout_path'] = str(code_root / repository['checkout_path'])
    return services


def pin_services(conn, project_id, config: dict[str, Any], service_ids: list[str]) -> dict[str, Any]:
    """The configuration with these services added to its services as they are, bringing nothing (no code projects,
    no servers): a run started from a server reads the services deployed on it."""
    have = {str(item['id']) for item in config['services']['items']}
    wanted = {str(item) for item in service_ids} - have
    if not wanted:
        return config
    added = [service for service in _services(conn, project_id) if str(service['id']) in wanted]
    return {**config, 'services': {**config['services'], 'enabled': True,
                                   'items': [*config['services']['items'], *jsonable_encoder(added)]}}


def base_config(conn, project_id, *, model: dict[str, Any], system_prompt: str) -> dict[str, Any]:
    """Everything allowed, before the run's selection narrows it."""
    from knowledge_admin.tools.catalog import CATALOG, SUBAGENT  # the catalog imports config

    return jsonable_encoder({
        'id': AD_HOC_AGENT, 'name': AD_HOC_NAME, 'description': '', 'when_to_use': '', 'enabled': True, 'revision': 0,
        'resources': 'per_run', 'model': model,
        'system_prompt': {'content': system_prompt}, 'instructions': '', 'settings_revision': None,
        'documents': {'enabled': True, 'grounded': False, 'all_collections': True, 'collections': []},
        'code': {'enabled': True, 'all_projects': True, 'projects': [], 'graphs': []},
        'skills': {'enabled': False, 'items': []}, 'mcp': {'enabled': False, 'servers': []},
        'ssh': {'enabled': True, 'servers': _servers(conn, project_id)},
        'services': {'enabled': True, 'items': _services(conn, project_id)},
        'tools': tool_items({'tool': name, 'mode': 'on', 'usage': None, 'subagent_allowed': name != SUBAGENT}
                            for name in CATALOG),
    })


def ad_hoc_config(conn, project_id, *, model: dict[str, Any], system_prompt: str, selection) -> dict[str, Any]:
    """The configuration one Generate documents session pins: exactly the selection (an environment brings its services
    and servers, a service its code projects and servers, as for per-run agents)."""
    config = resolve(conn, project_id, base_config(conn, project_id, model=model, system_prompt=system_prompt),
                     RunResources(code_project_ids=selection.code_project_ids, collection_ids=selection.collection_ids,
                                  server_ids=selection.server_ids, service_ids=selection.service_ids),
                     environment_ids=selection.environment_ids)
    for section, key in SECTIONS:
        if not config[section][key]:
            config[section] = {**config[section], 'enabled': False}
    return pin(conn, config, None)
