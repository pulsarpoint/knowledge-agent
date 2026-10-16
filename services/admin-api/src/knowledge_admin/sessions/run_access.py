"""A run's access object, built at start from its pinned configuration (access.py holds the rules).

What the run holds: the resources its pinned configuration lists (servers it may use, services, code projects,
collections, skills, MCP servers, documents) and the environments it was given. What it was given (picked; None:
everything it holds) is narrowed out of that with access.narrow; the children it holds are added by access.expand,
each 'via <type> <id>' of its direct parent: an environment its services and servers; a service its code projects and
the servers it is deployed on; a server the services deployed on it. Skills and MCP servers are not picked: always
given. Code projects and collections carry their paths (folder_paths, paths) and a code project its graph (graph_path,
code_root): the file, shell and graph tools work on them."""

from typing import Any

from knowledge_admin import access as access_rules
from knowledge_admin.agents.runner import ssh_servers
from knowledge_admin.capabilities import mcp
from knowledge_admin.capabilities.inventory import short
from knowledge_admin.knowledge_agents.run_resources import (
    DEPLOYED_SERVICES,
    ENVIRONMENT_SERVERS,
    ENVIRONMENT_SERVICES,
    SERVICE_CODE,
    SERVICE_SERVERS,
)
from knowledge_admin.rag.collections import document_collections

ENVIRONMENTS = 'SELECT id,name,description FROM ka.environments WHERE project_id=%s AND id=ANY(%s)'
ALWAYS_GIVEN = ('skill', 'mcp_server')
OWN = {
    'server': 'SELECT id,name,description FROM ka.servers WHERE project_id=%s AND id=%s',
    'service': 'SELECT id,name,description FROM ka.services WHERE project_id=%s AND id=%s',
    'environment': 'SELECT id,name,description FROM ka.environments WHERE project_id=%s AND id=%s',
}


def _entry(type_: str, item: dict[str, Any], **extra) -> dict[str, Any]:
    return {
        'type': type_,
        'id': str(item['id']),
        'name': item['name'],
        'description_short': short(item.get('description')),
        **extra,
    }


def _code_project(item: dict[str, Any], graphs: dict[str, dict]) -> dict[str, Any]:
    graph = graphs.get(str(item['id']))
    found = {'graph_path': graph['graph_path'], 'code_root': graph['code_root']} if graph else {}
    return _entry('code_project', item, folder_paths=list(item.get('folder_paths', [])), **found)


BUILT_FOR = (
    ('code_project', 'code_project_ids'),
    ('environment', 'environment_ids'),
    ('service', 'service_ids'),
    ('server', 'server_ids'),
)
NEW_DOCUMENT = ('folder', 'slug', 'tags')


def built_for(config: dict[str, Any]) -> list[list[str]]:
    """The resources the run was started for, [type, id]: what a new document is built for and links on promote
    (what the person chose, an environment rather than what it brings). A run started before chosen_resources was
    recorded: its run_resources."""
    chosen = config.get('chosen_resources') or config.get('run_resources') or {}
    return [[type_, str(item)] for type_, key in BUILT_FOR for item in chosen.get(key, [])]


def document_entry(config: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    found = {
        'type': 'document',
        'id': str(item['id']),
        'name': item['title'],
        'description_short': '',
        'editable': bool(item.get('editable')),
    }
    if item.get('new'):  # the new document the run writes: its name, where it goes and what it is built for
        found.update({key: item.get(key) for key in NEW_DOCUMENT}, new=True, built_for=built_for(config))
    return found


def config_resources(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Every resource the pinned configuration lists, once (pure: no environments, they are not in it)."""
    servers = ssh_servers(config)
    found = [_entry('server', server) for server in servers]
    if config['services']['enabled']:
        found += [_entry('service', item) for item in config['services']['items']]
    if config['code']['enabled']:
        graphs = {str(graph['id']): graph for graph in config['code'].get('graphs', [])}
        found += [_code_project(item, graphs) for item in config['code']['projects']]
    if config['documents']['enabled']:
        found += [
            _entry('collection', item, paths=list(item.get('paths', [])))
            for item in document_collections(config)[1]
            if 'id' in item
        ]
    if config['skills']['enabled']:
        skills = [*config['skills']['items'], *(skill for server in servers for skill in server.get('skills', []))]
        found += [_entry('skill', skill) for skill in skills if skill['enabled']]
    found += [_entry('mcp_server', server) for server in mcp.selected_servers(config, servers)]
    found += [document_entry(config, item) for item in config.get('document_access') or []]
    return list({(item['type'], item['id']): item for item in found}.values())


def held(conn, project, config: dict[str, Any], environment_ids: list) -> list[dict[str, Any]]:
    """Every resource the pinned configuration lists, once, and the given environments (they are not in it)."""
    found = config_resources(config)
    if environment_ids:
        rows = conn.execute(ENVIRONMENTS, (project, [str(item) for item in environment_ids])).fetchall()
        by_id = {str(row['id']): row for row in rows}
        found += [_entry('environment', by_id[str(item)]) for item in environment_ids if str(item) in by_id]
    return list({(item['type'], item['id']): item for item in found}.values())


SECTIONS = (
    ('ssh', 'servers', 'server'),
    ('services', 'items', 'service'),
    ('code', 'projects', 'code_project'),
    ('documents', 'collections', 'collection'),
    ('skills', 'items', 'skill'),
    ('mcp', 'servers', 'mcp_server'),
)


def cut_to(config: dict[str, Any], access: dict[str, Any]) -> dict[str, Any]:
    """The configuration with only what the run's access object holds in its sections, graphs and documents: the
    tools and the credentials see exactly the run's resources (a section stays enabled when the agent has items of its
    kind: its tools say what the run was not given)."""
    keep = {(entry['type'], str(entry['id'])) for entry in access['resources']}
    cut = dict(config)
    for section, key, type_ in SECTIONS:
        cut[section] = {
            **config[section],
            key: [item for item in config[section][key] if (type_, str(item['id'])) in keep],
        }
    cut['code']['graphs'] = [g for g in config['code'].get('graphs', []) if ('code_project', str(g['id'])) in keep]
    cut['document_access'] = [d for d in config.get('document_access') or [] if ('document', str(d['id'])) in keep]
    return cut


def _ids(conn, query: str, parameters: tuple) -> list[str]:
    return [str(row['id']) for row in conn.execute(query, parameters)]


def _children(conn, project, holds: dict[tuple[str, str], dict]) -> dict:
    """The direct children the run holds of each server, service and environment it holds, by (type, id)."""
    found = {}
    for kind, key in holds:
        if kind == 'environment':
            keys = [('service', i) for i in _ids(conn, ENVIRONMENT_SERVICES, (project, [key]))]
            keys += [('server', i) for i in _ids(conn, ENVIRONMENT_SERVERS, (project, [key]))]
        elif kind == 'service':
            keys = [('code_project', i) for i in _ids(conn, SERVICE_CODE, (project, [key]))]
            keys += [('server', i) for i in _ids(conn, SERVICE_SERVERS, (project, [key]))]
        elif kind == 'server':
            keys = [('service', i) for i in _ids(conn, DEPLOYED_SERVICES, (key,))]
        else:
            continue
        found[(kind, key)] = [holds[child] for child in dict.fromkeys(keys) if child in holds]
    return found


def run_access(
    conn,
    project,
    config: dict[str, Any],
    picked: list[dict[str, Any]] | None,
    *,
    environment_ids: list = (),
    content_editor: bool = False,
    own: tuple[str, Any] | None = None,
) -> dict[str, Any]:
    """The run's access object. picked: [{type, id}] the run was given (None: everything it holds); raises ValueError
    when a pick is not held (access.narrow). own: (type, id) of the run's own resource (a resource run's, or the one
    its inventory document is about): always in its access object."""
    holds = held(conn, project, config, list(environment_ids))
    if picked is not None:
        picked = [*picked, *(item for item in holds if item['type'] in ALWAYS_GIVEN)]
    if own is not None:
        own_pick = {'type': own[0], 'id': str(own[1])}
        if (own[0], str(own[1])) not in {(item['type'], item['id']) for item in holds}:
            # the run's own resource, also one the run holds nothing else of (a server without SSH)
            row = conn.execute(OWN[own[0]], (project, str(own[1]))).fetchone()
            if row is not None:
                holds.append(_entry(own[0], row))
        if picked is not None and own_pick not in picked:
            picked = [*picked, own_pick]
    narrowed = access_rules.narrow({'resources': holds, 'roles': {'content_editor': content_editor}}, picked)
    by_key = {(item['type'], item['id']): item for item in holds}
    return access_rules.expand(narrowed, _children(conn, project, by_key))
