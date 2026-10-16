"""A run's access object, built at start from what the run holds (access.py holds the rules).

What the run holds: its access entries (a saved agent's: get_agent_config; an ad-hoc run's: what it was given and what
that brings), each loaded with what the tools need (config.resource_entries: a server its connection, a code project
its folders and graph, a collection its paths). What it was given (picked; None: everything it holds) is narrowed out
of that with access.narrow; the children it holds (a saved agent's run loads the children of what it was given first:
agent_access) are added by access.expand, each 'via <type> <id>' of its direct parent: an environment its services and
servers; a service its code projects and the servers it is deployed on; a server the services deployed on it. Skills
and MCP servers are not picked: always given."""

from typing import Any

from knowledge_admin import access as access_rules
from knowledge_admin.knowledge_agents.config import entry, load_entries
from knowledge_admin.knowledge_agents.run_resources import (
    DEPLOYED_SERVICES,
    ENVIRONMENT_SERVERS,
    ENVIRONMENT_SERVICES,
    SERVICE_CODE,
    SERVICE_SERVERS,
    bring,
)

ALWAYS_GIVEN = ('skill', 'mcp_server')
OWN = {
    'server': 'SELECT id,name,description FROM ka.servers WHERE project_id=%s AND id=%s',
    'service': 'SELECT id,name,description FROM ka.services WHERE project_id=%s AND id=%s',
    'environment': 'SELECT id,name,description FROM ka.environments WHERE project_id=%s AND id=%s',
}


BUILT_FOR = (
    ('code_project', 'code_project_ids'),
    ('environment', 'environment_ids'),
    ('service', 'service_ids'),
    ('server', 'server_ids'),
)


def built_for(config: dict[str, Any]) -> list[list[str]]:
    """The resources the run was started for, [type, id]: what a new document is built for and links on promote
    (what the person chose, an environment rather than what it brings). A run started before chosen_resources was
    recorded: its run_resources."""
    chosen = config.get('chosen_resources') or config.get('run_resources') or {}
    return [[type_, str(item)] for type_, key in BUILT_FOR for item in chosen.get(key, [])]


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
    holds: list[dict[str, Any]],
    picked: list[dict[str, Any]] | None,
    *,
    content_editor: bool = False,
    own: tuple[str, Any] | None = None,
) -> dict[str, Any]:
    """The run's access object out of the entries it holds. picked: [{type, id}] the run was given (None: everything
    it holds); raises ValueError when a pick is not held (access.narrow). own: (type, id) of the run's own resource (a
    resource run's, or the one its inventory document is about): always in its access object."""
    holds = list({(item['type'], item['id']): item for item in holds}.values())
    if picked is not None:
        picked = [*picked, *(item for item in holds if item['type'] in ALWAYS_GIVEN)]
    if own is not None:
        own_pick = {'type': own[0], 'id': str(own[1])}
        if (own[0], str(own[1])) not in {(item['type'], item['id']) for item in holds}:
            # the run's own resource, also one the run holds nothing else of (a server without SSH)
            row = conn.execute(OWN[own[0]], (project, str(own[1]))).fetchone()
            if row is not None:
                holds.append(entry(own[0], row))
        if picked is not None and own_pick not in picked:
            picked = [*picked, own_pick]
    narrowed = access_rules.narrow({'resources': holds, 'roles': {'content_editor': content_editor}}, picked)
    by_key = {(item['type'], item['id']): item for item in holds}
    return access_rules.expand(narrowed, _children(conn, project, by_key))


def agent_access(
    conn, project, config: dict[str, Any], picked: list[dict[str, Any]] | None, *, content_editor: bool
) -> dict[str, Any]:
    """A saved agent's run's access object. The agent's access (as get_agent_config holds it) narrowed to picked
    (None: all of it; ValueError: a pick outside it, access.narrow), then the direct children of what was given brought
    as an ad-hoc run brings them (an environment its services and servers, a service its code projects and servers:
    run_resources.bring; a server the services deployed on it) and loaded: what was given brings them, the agent's
    access need not hold them."""
    holds = config['access']['resources']
    if picked is not None:
        picked = [*picked, *(item for item in holds if item['type'] in ALWAYS_GIVEN)]
    given = access_rules.narrow({'resources': holds}, picked)['resources']

    def ids(type_: str) -> list[str]:
        return [item['id'] for item in given if item['type'] == type_]

    brought = bring(conn, project, ids('service'), ids('environment'))
    deployed = [found for server in ids('server') for found in _ids(conn, DEPLOYED_SERVICES, (server,))]
    listed = {(item['type'], item['id']) for item in holds}
    children = {
        'code_project': brought['code_project_ids'],
        'server': brought['server_ids'],
        'service': [*brought['service_ids'], *deployed],
    }
    wanted = {
        type_: [item for item in dict.fromkeys(str(found) for found in found_ids) if (type_, item) not in listed]
        for type_, found_ids in children.items()
    }
    picks = [
        {'type': item['type'], 'id': item['id'], **({'editable': item['editable']} if 'editable' in item else {})}
        for item in given
    ]
    return run_access(
        conn, project, [*holds, *load_entries(conn, project, wanted)], picks, content_editor=content_editor
    )
