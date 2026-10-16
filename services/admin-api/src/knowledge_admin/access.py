"""Access rules: pure functions over a run's access object.

access = {"resources": [{type, id, name, description_short, source, editable?}], "roles": {"content_editor": bool}}
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
NOT_AVAILABLE = 'This {type} is not available in this run.'
NOT_EDITOR = 'This agent may not change content (content editor is off).'
NONE_OF = 'This run has no {type}s.'


def entry(access: dict, type: str, id: str) -> dict | None:
    for r in access.get('resources', []):
        if r['type'] == type and str(r['id']) == str(id):
            return r
    return None


def _editor(access: dict) -> bool:
    return bool(access.get('roles', {}).get('content_editor'))


def can_use(access: dict, type: str, id: str) -> bool:
    return entry(access, type, id) is not None


def can_write_document(access: dict, document_id: str) -> bool:
    e = entry(access, 'document', document_id)
    return _editor(access) and e is not None and bool(e.get('editable', False))


def can_propose(access: dict, type: str, id: str) -> bool:
    return _editor(access) and can_use(access, type, id)


def can_create_document(access: dict, built_for: list[tuple[str, str]]) -> bool:
    return _editor(access) and all(can_use(access, t, i) for t, i in built_for)


def has_any(access: dict, type: str) -> bool:
    return any(r['type'] == type for r in access.get('resources', []))


def refuse_use(type: str) -> str:
    return NOT_AVAILABLE.format(type=TYPE_WORDS[type])


def refuse_none(type: str) -> str:
    return NONE_OF.format(type=TYPE_WORDS[type])


def narrow(agent_access: dict, picked: list[dict] | None, roles_off: list[str] | None = None) -> dict:
    """The agent's access or less: picked resources must be in the agent's list; roles are only switched off."""
    if picked is None:
        resources = [dict(r) for r in agent_access.get('resources', [])]
    else:
        resources = []
        for p in picked:
            e = entry(agent_access, p['type'], p['id'])
            if e is None:
                raise ValueError(f"{TYPE_WORDS.get(p['type'], p['type'])} {p['id']} is not in the agent's access")
            resources.append(dict(e))
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


def listing(access: dict, type: str | None = None) -> list[dict]:
    return [r for r in access.get('resources', []) if type is None or r['type'] == type]
