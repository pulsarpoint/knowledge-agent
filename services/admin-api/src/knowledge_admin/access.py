"""Access rules: pure functions over a run's access object.

access = {"resources": [{type, id, name, description_short, source, editable?, ...}], "roles": {"content_editor": bool}}
An entry also carries what the tools need to reach its resource: a code project its folder_paths (and graph_path,
code_root), a collection its paths, a skill its description, a new document where it goes; a server its connection
(hostname, port, username, allow_sudo, known_hosts, enabled, ssh_allowed, private_key_ref) and an MCP server its (url,
enabled, bearer_token_ref): references only, never secrets. listing shows only the PUBLIC fields: the model and the
Debug tab never see a connection. A server entry with ssh_allowed has a pinned connection (runner.connected_servers).
No I/O. Every tool decides access through these functions.
"""

TYPES = ('server', 'service', 'environment', 'code_project', 'collection', 'document', 'skill', 'mcp_server')
TYPE_WORDS = {
    'server': 'server',
    'service': 'service',
    'environment': 'environment',
    'code_project': 'code project',
    'collection': 'collection',
    'document': 'document',
    'skill': 'skill',
    'mcp_server': 'MCP server',
}
# What listing shows of an entry (get_available_resources, the Debug tab): never a connection.
PUBLIC = (
    'type',
    'id',
    'name',
    'description_short',
    'source',
    'editable',
    'description',
    'folder_paths',
    'paths',
    'graph_path',
    'code_root',
    'new',
    'folder',
    'slug',
    'tags',
    'built_for',
)
NOT_AVAILABLE = 'This {type} is not available in this run.'
NOT_EDITOR = 'This agent may not change content (content editor is off).'
NONE_OF = 'This run has no {type}s.'


def entry(access: dict, type: str, id: str) -> dict | None:
    for r in access.get('resources', []):
        if r['type'] == type and str(r['id']) == str(id):
            return r
    return None


SSH_NOT_SET_UP = 'SSH is not set up for this server.'
SSH_NOT_ALLOWED = 'SSH is not allowed for this server.'


def ssh_reason(server: dict) -> str | None:
    """Why the run cannot use this server entry over SSH, as a run start says it; None when it is connected (enabled,
    SSH allowed and a key). A server stays in the run's access either way: it is listed and read."""
    if not server.get('enabled') or not server.get('private_key_ref'):
        return SSH_NOT_SET_UP
    if not server.get('ssh_allowed'):
        return SSH_NOT_ALLOWED
    return None


def is_content_editor(access: dict) -> bool:
    return bool(access.get('roles', {}).get('content_editor'))


def can_use(access: dict, type: str, id: str) -> bool:
    return entry(access, type, id) is not None


def can_write_document(access: dict, document_id: str) -> bool:
    e = entry(access, 'document', document_id)
    return is_content_editor(access) and e is not None and bool(e.get('editable', False))


def can_propose(access: dict, type: str, id: str) -> bool:
    return is_content_editor(access) and can_use(access, type, id)


def can_create_document(access: dict, built_for: list[tuple[str, str]]) -> bool:
    return is_content_editor(access) and all(can_use(access, t, i) for t, i in built_for)


def has_any(access: dict, type: str) -> bool:
    return any(r['type'] == type for r in access.get('resources', []))


def refuse_use(type: str) -> str:
    return NOT_AVAILABLE.format(type=TYPE_WORDS[type])


def refuse_none(*types: str) -> str:
    """NONE_OF for one type, or several joined with 'or' (the file tools: code projects or collections)."""
    return NONE_OF.format(type='s or '.join(TYPE_WORDS[type] for type in types))


def narrow(agent_access: dict, picked: list[dict] | None, roles_off: list[str] | None = None) -> dict:
    """The agent's access or less: picked resources must be in the agent's list (each once); a pick may make an
    editable document read-only (editable only when the pick and the agent's entry both say so; a pick without
    editable keeps the entry's); roles are only switched off."""
    if picked is None:
        resources = [dict(r) for r in agent_access.get('resources', [])]
    else:
        resources = []
        for p in picked:
            e = entry(agent_access, p['type'], p['id'])
            if e is None:
                raise ValueError(f"{TYPE_WORDS.get(p['type'], p['type'])} {p['id']} is not in the agent's access")
            if entry({'resources': resources}, p['type'], p['id']) is not None:
                continue
            found = dict(e)
            if 'editable' in p and 'editable' in found:
                found['editable'] = bool(found['editable'] and p['editable'])
            resources.append(found)
    roles = dict(agent_access.get('roles', {}))
    roles['content_editor'] = bool(roles.get('content_editor'))
    for role in roles_off or []:
        roles[role] = False
    return {'resources': resources, 'roles': roles}


def expand(access: dict, children: dict[tuple[str, str], list[dict]]) -> dict:
    """Add the children of each resource, marked 'via <type> <id>' of their direct parent: those of the given
    resources, then those of the children added (an environment's service brings its code projects); never
    duplicates."""
    resources = [{**r, 'source': 'given'} for r in access.get('resources', [])]
    for r in resources:  # grows while it is read: children of children are reached too
        for c in children.get((r['type'], str(r['id'])), []):
            if entry({'resources': resources}, c['type'], c['id']) is None:
                resources.append({**c, 'source': f'via {r["type"]} {r["id"]}'})
    return {**access, 'resources': resources}


def entries(access: dict, type: str | None = None) -> list[dict]:
    """The entries of one type (None: all), whole: with their connections (for the tools, never for the model)."""
    return [r for r in access.get('resources', []) if type is None or r['type'] == type]


def listing(access: dict, type: str | None = None) -> list[dict]:
    """The entries of one type (None: all) with only their PUBLIC fields."""
    return [{key: r[key] for key in PUBLIC if key in r} for r in entries(access, type)]
