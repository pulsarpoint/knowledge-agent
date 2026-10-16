"""A run's access object, built at start from its pinned configuration (access.py holds the rules).

What the run holds: the resources its pinned configuration lists (servers it may use, services, code projects,
collections, skills, MCP servers, documents) and the environments it was given. What it was given (picked; None:
everything it holds) is narrowed out of that with access.narrow; the direct children of what it was given that it holds
are added by access.expand ('via <type> <id>'): an environment its services, servers and their code projects; a service
its code projects and servers; a server the services deployed on it. Skills and MCP servers are not picked: always
given."""

from typing import Any

from knowledge_admin import access as access_rules
from knowledge_admin.agents.runner import ssh_servers
from knowledge_admin.capabilities import mcp
from knowledge_admin.capabilities.inventory import short
from knowledge_admin.knowledge_agents.run_resources import bring
from knowledge_admin.rag.collections import document_collections

DEPLOYED = 'SELECT DISTINCT service_id FROM ka.service_deployments WHERE server_id=%s'
ENVIRONMENTS = 'SELECT id,name,description FROM ka.environments WHERE project_id=%s AND id=ANY(%s)'
ALWAYS_GIVEN = ('skill', 'mcp_server')


def _entry(type_: str, item: dict[str, Any]) -> dict[str, Any]:
    return {
        'type': type_,
        'id': str(item['id']),
        'name': item['name'],
        'description_short': short(item.get('description')),
    }


def held(conn, project, config: dict[str, Any], environment_ids: list) -> list[dict[str, Any]]:
    """Every resource the pinned configuration lists, once, and the given environments (they are not in it)."""
    servers = ssh_servers(config)
    found = [_entry('server', server) for server in servers]
    if config['services']['enabled']:
        found += [_entry('service', item) for item in config['services']['items']]
    if config['code']['enabled']:
        found += [_entry('code_project', item) for item in config['code']['projects']]
    if config['documents']['enabled']:
        found += [_entry('collection', item) for item in document_collections(config)[1] if 'id' in item]
    if environment_ids:
        rows = conn.execute(ENVIRONMENTS, (project, [str(item) for item in environment_ids])).fetchall()
        by_id = {str(row['id']): row for row in rows}
        found += [_entry('environment', by_id[str(item)]) for item in environment_ids if str(item) in by_id]
    if config['skills']['enabled']:
        skills = [*config['skills']['items'], *(skill for server in servers for skill in server.get('skills', []))]
        found += [_entry('skill', skill) for skill in skills if skill['enabled']]
    found += [_entry('mcp_server', server) for server in mcp.selected_servers(config, servers)]
    found += [
        {
            'type': 'document',
            'id': str(item['id']),
            'name': item['title'],
            'description_short': '',
            'editable': bool(item['editable']),
        }
        for item in config.get('document_access') or []
    ]
    return list({(item['type'], item['id']): item for item in found}.values())


def _children(conn, project, given: list[dict[str, Any]], holds: dict[tuple[str, str], dict]) -> dict:
    """The children of each given server, service and environment that the run holds, by (type, id)."""
    found = {}
    for item in given:
        if item['type'] == 'environment':
            brought = bring(conn, project, [], [item['id']])
            keys = [('service', i) for i in brought['service_ids']] + [('server', i) for i in brought['server_ids']]
            keys += [('code_project', i) for i in brought['code_project_ids']]
        elif item['type'] == 'service':
            brought = bring(conn, project, [item['id']])
            keys = [('code_project', i) for i in brought['code_project_ids']]
            keys += [('server', i) for i in brought['server_ids']]
        elif item['type'] == 'server':
            keys = [('service', str(row['service_id'])) for row in conn.execute(DEPLOYED, (item['id'],)).fetchall()]
        else:
            continue
        found[(item['type'], item['id'])] = [holds[key] for key in dict.fromkeys(keys) if key in holds]
    return found


def run_access(
    conn,
    project,
    config: dict[str, Any],
    picked: list[dict[str, Any]] | None,
    *,
    environment_ids: list = (),
    content_editor: bool = False,
) -> dict[str, Any]:
    """The run's access object. picked: [{type, id}] the run was given (None: everything it holds); raises ValueError
    when a pick is not held (access.narrow)."""
    holds = held(conn, project, config, list(environment_ids))
    if picked is not None:
        picked = [*picked, *(item for item in holds if item['type'] in ALWAYS_GIVEN)]
    narrowed = access_rules.narrow({'resources': holds, 'roles': {'content_editor': content_editor}}, picked)
    by_key = {(item['type'], item['id']): item for item in holds}
    return access_rules.expand(narrowed, _children(conn, project, narrowed['resources'], by_key))
