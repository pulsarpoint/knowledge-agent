"""A run's access object, built at start from what the run holds (access.py holds the rules); every run is built the
same way (run_access).

What the run holds: a saved agent's run its agent's access entries (get_agent_config), an ad-hoc run what it was
picked (each checked: check_picks), each loaded with what the tools need (config.resource_entries: a server its
connection, a code project its folders and graph). What it was given is narrowed out of that
with access.narrow; the children of what it was given are loaded when it does not hold them (bring: an environment the
services and servers deployed in it; a service its code projects (built_from) and the servers it is deployed on; a
server the services deployed on it) and added by access.expand, each 'via <type> <id>' of its direct parent. Skills and MCP servers are not picked:
always given."""

from collections.abc import Iterable, Mapping
from typing import Any, NamedTuple
from uuid import UUID

from knowledge_admin import access as access_rules
from knowledge_admin.knowledge_agents.config import entry, load_entries

ALWAYS_GIVEN = ('skill', 'mcp_server')
OWN = {
    'server': 'SELECT id,name,description FROM ka.servers WHERE project_id=%s AND id=%s',
    'service': 'SELECT id,name,description FROM ka.services WHERE project_id=%s AND id=%s',
    'environment': 'SELECT id,name,description FROM ka.environments WHERE project_id=%s AND id=%s',
}
NOT_AVAILABLE = '{label} {name} is not available: it was removed, archived or disabled.'


class Kind(NamedTuple):
    key: str  # the resources key of a request (subagent and expert calls)
    label: str  # one resource, in messages
    plural: str  # the kind, in messages
    type: str  # the access object's resource type


KINDS = (
    Kind('code_project_ids', 'Code project', 'code projects', 'code_project'),
    Kind('server_ids', 'Server', 'servers', 'server'),
    Kind('service_ids', 'Service', 'services', 'service'),
)
# The id, name and whether it can be given now, of the picked resources of one type in this workspace.
FOUND = {
    'code_project': 'SELECT id,name,archived_at IS NULL AS usable FROM ka.repositories WHERE project_id=%s AND id=ANY(%s)',
    'server': 'SELECT id,name,ssh_allowed AS usable FROM ka.servers WHERE project_id=%s AND id=ANY(%s)',
    'service': 'SELECT id,name,true AS usable FROM ka.services WHERE project_id=%s AND id=ANY(%s)',
    'environment': 'SELECT id,name,true AS usable FROM ka.environments WHERE project_id=%s AND id=ANY(%s)',
}
# What services and environments bring: only usable code projects (not archived) and servers (SSH allowed). An
# environment holds the services deployed in it and the servers in it (ka.environment_servers); a service's code
# projects are its built_from relations.
ENVIRONMENT_SERVICES = """SELECT s.id FROM ka.services s WHERE s.project_id=%s AND EXISTS (SELECT 1
    FROM ka.service_deployments d WHERE d.service_id=s.id AND d.environment_id=ANY(%s)) ORDER BY lower(s.name),s.id"""
ENVIRONMENT_SERVERS = """SELECT s.id FROM ka.servers s WHERE s.project_id=%s AND s.ssh_allowed AND EXISTS (SELECT 1
    FROM ka.environment_servers m WHERE m.server_id=s.id AND m.environment_id=ANY(%s)) ORDER BY lower(s.name),s.id"""
SERVICE_CODE = """SELECT r.id FROM ka.service_relations l JOIN ka.repositories r ON r.id=l.to_code_project_id
    WHERE l.kind='built_from' AND r.project_id=%s AND l.from_service_id=ANY(%s) AND r.archived_at IS NULL
    ORDER BY lower(r.name),r.id"""
# The services deployed on one server (a server run reads them; they are its children in the run's access).
DEPLOYED_SERVICES = 'SELECT DISTINCT service_id AS id FROM ka.service_deployments WHERE server_id=%s'
SERVICE_SERVERS = """SELECT h.id FROM ka.service_deployments d JOIN ka.servers h ON h.id=d.server_id
    WHERE h.project_id=%s AND d.service_id=ANY(%s) AND h.ssh_allowed ORDER BY lower(h.name),h.id"""
BUILT_FOR = ('code_project', 'environment', 'service', 'server')


def picks(resources: Mapping[str, list] | None) -> list[dict[str, str]]:
    """Request resources ({code_project_ids, server_ids, service_ids}) as access picks [{type, id}],
    each once, in KINDS order."""
    return [
        {'type': kind.type, 'id': str(item)}
        for kind in KINDS
        for item in dict.fromkeys(str(item) for item in (resources or {}).get(kind.key) or [])
    ]


def built_for(given: Iterable[Mapping[str, Any]]) -> list[list[str]]:
    """What a new document of the run is built for and links on promote, [type, id]: the code projects,
    environments, services and servers of what the run was given (its picks, or its access entries with source
    'given'): what the person chose, an environment rather than what it brings."""
    given = list(given)
    return [[type_, str(item['id'])] for type_ in BUILT_FOR for item in given if item['type'] == type_]


def given(access: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The access entries the run was given (not brought by them): what the person chose. The session detail's
    given_resources (sessions/rows.py DETAIL) selects the same entries in SQL."""
    return [item for item in access.get('resources', []) if item.get('source') == 'given']


def _ids(conn, query: str, parameters: tuple) -> list[str]:
    return [str(row['id']) for row in conn.execute(query, parameters)]


def bring(
    conn, project_id, service_ids: Iterable[str], environment_ids: Iterable[UUID | str] = ()
) -> dict[str, list[str]]:
    """What chosen services and environments bring: an environment its services and servers, a service (chosen or
    brought) its code projects and the servers it is deployed on. Only code projects not archived and servers with SSH
    allowed; the ids of what was chosen itself are not repeated in service_ids."""
    environments = [str(item) for item in environment_ids]
    brought: dict[str, list[str]] = {'code_project_ids': [], 'server_ids': [], 'service_ids': []}
    if environments:
        brought['service_ids'] = _ids(conn, ENVIRONMENT_SERVICES, (project_id, environments))
        brought['server_ids'] = _ids(conn, ENVIRONMENT_SERVERS, (project_id, environments))
    services = list(dict.fromkeys([*(str(item) for item in service_ids), *brought['service_ids']]))
    if services:
        brought['code_project_ids'] = _ids(conn, SERVICE_CODE, (project_id, services))
        brought['server_ids'] += _ids(conn, SERVICE_SERVERS, (project_id, services))
    return brought


def check_picks(conn, project, picked: Iterable[Mapping[str, Any]]) -> None:
    """ValueError naming the first picked code project, server, service or environment that is not in this workspace
    or cannot be given now (archived, SSH not allowed)."""
    picked = list(picked)
    for type_, query in FOUND.items():
        ids = [str(item['id']) for item in picked if item['type'] == type_]
        found = {str(row['id']): row for row in conn.execute(query, (project, ids))} if ids else {}
        label = access_rules.TYPE_WORDS[type_].capitalize()
        for item in ids:
            row = found.get(item)
            if row is None or not row['usable']:
                raise ValueError(NOT_AVAILABLE.format(label=label, name=row['name'] if row else item))


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


def _brought(conn, project, holds: list[dict[str, Any]], given: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The loaded entries of what the given resources bring that the run does not hold yet (bring; a server the
    services deployed on it). A brought server stays even when the run cannot connect to it: it is listed and read,
    and SSH skips it (access.ssh_reason)."""

    def ids(type_: str) -> list[str]:
        return [str(item['id']) for item in given if item['type'] == type_]

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
    return load_entries(conn, project, wanted)


def run_access(
    conn,
    project,
    holds: list[dict[str, Any]],
    picked: list[dict[str, Any]] | None,
    *,
    own: tuple[str, Any] | None = None,
) -> dict[str, Any]:
    """The run's access object out of the entries it holds. picked: [{type, id}] the run was given (None:
    everything it holds); raises ValueError when a pick is not held (access.narrow). What it was given brings its
    children (_brought). own: (type, id) of the run's own resource (a resource run's, or the one its inventory
    document is about): always in its access object, bringing nothing of its own."""
    holds = list({(item['type'], item['id']): item for item in holds}.values())
    if picked is not None:
        picked = [*picked, *(item for item in holds if item['type'] in ALWAYS_GIVEN)]
    holds += _brought(conn, project, holds, holds if picked is None else picked)
    if own is not None:
        own_pick = {'type': own[0], 'id': str(own[1])}
        if (own[0], str(own[1])) not in {(item['type'], item['id']) for item in holds}:
            # the run's own resource, also one the run holds nothing else of (a server without SSH)
            row = conn.execute(OWN[own[0]], (project, str(own[1]))).fetchone()
            if row is not None:
                holds.append(entry(own[0], row))
        if picked is not None and own_pick not in picked:
            picked = [*picked, own_pick]
    narrowed = access_rules.narrow({'resources': holds}, picked)
    by_key = {(item['type'], item['id']): item for item in holds}
    return access_rules.expand(narrowed, _children(conn, project, by_key))


def agent_access(
    conn,
    project,
    config: dict[str, Any],
    picked: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """A saved agent's run's access object: the agent's access (as get_agent_config holds it) narrowed to picked
    (None: all of it; ValueError: a pick outside it, access.narrow; its skills and MCP servers are added by
    run_access), what was given bringing its children (run_access: the agent's access need not hold them)."""
    holds = config['access']['resources']
    given_entries = access_rules.narrow({'resources': holds}, picked)['resources']
    picks_given = [{'type': item['type'], 'id': item['id']} for item in given_entries]
    return run_access(conn, project, holds, picks_given)
