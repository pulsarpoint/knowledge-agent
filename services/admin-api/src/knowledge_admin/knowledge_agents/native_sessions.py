"""Native agents in Generate documents sessions: the session's working folders as the agent's code roots."""
import copy
from typing import Any


def code_roots(config: dict[str, Any], folders: list[dict[str, Any]]) -> dict[str, Any]:
    """The config a native session runs with: the agent's own config plus every working folder of the session that is
    not already one of its code project folders, as code roots grouped by code project. With such folders Code projects
    is on; the agent's own projects stay in only when its Code projects switch was on (a switched-off agent's saved
    selections stay out). Credentials stay references; nothing is decrypted here."""
    pinned = copy.deepcopy(config)
    own = pinned['code']['projects'] if pinned['code']['enabled'] else []
    known = {path for project in own for path in project['folder_paths']}
    added: dict[str, dict[str, Any]] = {}
    for folder in folders:
        if folder['path'] in known:
            continue
        known.add(folder['path'])
        project = added.setdefault(str(folder['code_project_id']), {
            'id': str(folder['code_project_id']), 'name': folder['name'], 'description': 'Chosen for this session.',
            'folder_paths': []})
        project['folder_paths'].append(folder['path'])
    if added:
        pinned['code'] = {**pinned['code'], 'enabled': True, 'all_projects': False, 'projects': [*own, *added.values()]}
    return pinned
