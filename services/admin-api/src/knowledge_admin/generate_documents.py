"""Docs → Generate documents: what the form asks an agent to document, as the session's context snapshot and the
ad-hoc agent's context. Names, addresses, descriptions and container paths only: never credentials."""
import os
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder


FOLDER_PATHS = 'ARRAY(SELECT path::text FROM ka.code_project_folders f WHERE f.code_project_id=r.id ORDER BY path) AS folder_paths'


def _in_order(rows: list[dict], ids: list[UUID], what: str) -> list[dict]:
    """Rows in the order they were chosen; an id that is not available in this workspace refuses the request."""
    found = {row['id']: row for row in rows}
    if len(found) != len(ids):
        raise HTTPException(400, f'Select available {what}.')
    return [found[item] for item in ids]


def subjects(conn, project: UUID, value) -> dict:
    """The chosen code projects, collections, environments, services and servers with what the message says about them. Code project
    folders are container paths under CODE_ROOT; archived code projects are never linked."""
    code_root: list[PurePosixPath] = []

    def paths(folder_paths: list[str]) -> list[str]:
        if folder_paths and not code_root:
            root = PurePosixPath(os.environ.get('CODE_ROOT', ''))
            if not root.is_absolute() or '..' in root.parts:
                raise HTTPException(503, 'CODE_ROOT must be an absolute mounted path')
            code_root.append(root)
        return [str(code_root[0] / path) for path in folder_paths]

    code = _in_order(conn.execute(f'''SELECT r.id,r.name,{FOLDER_PATHS} FROM ka.repositories r
        WHERE r.project_id=%s AND r.id=ANY(%s) AND r.archived_at IS NULL''', (project, value.code_project_ids)).fetchall(),
        value.code_project_ids, 'code projects')
    for item in code:
        if not item['folder_paths']:
            raise HTTPException(400, f"The code project {item['name']} has no folders.")
    collections = _in_order(conn.execute('''SELECT id,name,description FROM ka.rag_collections
        WHERE project_id=%s AND id=ANY(%s) AND deleted_at IS NULL''', (project, value.collection_ids)).fetchall(),
        value.collection_ids, 'collections')
    environments = _in_order(conn.execute('''SELECT id,name,description,services,servers FROM ka.environment_assets
        WHERE project_id=%s AND id=ANY(%s)''', (project, value.environment_ids)).fetchall(), value.environment_ids, 'environments')
    services = _in_order(conn.execute('''SELECT id,name,description,deployments,links FROM ka.service_assets
        WHERE project_id=%s AND id=ANY(%s)''', (project, value.service_ids)).fetchall(), value.service_ids, 'services')
    servers = _in_order(conn.execute('SELECT id,name,hostname,description FROM ka.servers WHERE project_id=%s AND id=ANY(%s)',
                                     (project, value.server_ids)).fetchall(), value.server_ids, 'servers')
    members = {e['id']: [UUID(s['id']) for s in e['services']] for e in environments}
    linked: dict[UUID, list[dict]] = {}
    for row in conn.execute(f'''SELECT l.from_service_id AS service_id,r.id,r.name,{FOLDER_PATHS} FROM ka.inventory_links l
            JOIN ka.repositories r ON r.id=l.to_code_project_id WHERE l.kind='built_from' AND l.from_service_id=ANY(%s)
            AND r.archived_at IS NULL ORDER BY lower(r.name),r.id''',
            ([item for ids in members.values() for item in ids] + [s['id'] for s in services],)).fetchall():
        linked.setdefault(row['service_id'], []).append({'id': row['id'], 'name': row['name'], 'paths': paths(row['folder_paths'])})
    deployments: dict[UUID, list[str]] = {}
    for row in conn.execute('''SELECT d.environment_id,s.name AS service,h.name AS server FROM ka.service_deployments d
            JOIN ka.services s ON s.id=d.service_id JOIN ka.servers h ON h.id=d.server_id
            WHERE d.environment_id=ANY(%s) ORDER BY lower(s.name),lower(h.name),d.runtime_name,d.id''',
            ([e['id'] for e in environments],)).fetchall():
        deployments.setdefault(row['environment_id'], []).append(f"{row['service']} on {row['server']}")

    def environment_code(environment_id: UUID) -> list[dict]:
        seen, items = set(), []
        for service_id in members[environment_id]:
            for item in linked.get(service_id, []):
                if item['id'] not in seen:
                    seen.add(item['id'])
                    items.append(item)
        return items

    return jsonable_encoder({
        'code_projects': [{'id': item['id'], 'name': item['name'], 'paths': paths(item['folder_paths'])} for item in code],
        'collections': [{'id': c['id'], 'name': c['name'], 'description': c['description']} for c in collections],
        'environments': [{'id': e['id'], 'name': e['name'], 'description': e['description'],
                          'servers': [f"{s['name']} ({s['hostname']})" for s in e['servers']],
                          'services': [s['name'] for s in e['services']], 'deployments': deployments.get(e['id'], []),
                          'code_projects': environment_code(e['id'])} for e in environments],
        'services': [{'id': s['id'], 'name': s['name'], 'description': s['description'],
                      'deployments': [f"{d['server']['name']} ({d['environment']})" for d in s['deployments']],
                      'code_projects': linked.get(s['id'], []),
                      'connections': [connection(link) for link in s['links']]} for s in services],
        'servers': servers})


def connection(link: dict) -> str:
    """A connects_to link of a service as one line: 'connects to prod-db (server)' or 'billing (service) connects to it'."""
    other = f"{link['name']} ({link['entry_kind'].replace('_', ' ')})"
    return f'connects to {other}' if link['direction'] == 'outgoing' else f'{other} connects to it'


def _flat(text: str) -> str:
    """One line: a name or description cannot start a section of its own."""
    return ' '.join(text.split())


def _code(item: dict) -> str:
    return f"{_flat(item['name'])} ({', '.join(map(_flat, item['paths']))})" if item['paths'] else _flat(item['name'])


def _line(name: str, description: str, parts: list[tuple[str, list[str]]]) -> str:
    text = f'- {_flat(name)}' + (f': {_flat(description)}' if _flat(description) else '')
    details = [label + ', '.join(map(_flat, values)) for label, values in parts if values]
    return text + (' — ' + '; '.join(details) if details else '')


def _sections(context: dict[str, Any]) -> list[str]:
    """The selected subjects, one Markdown section per kind; names and descriptions stay on one line."""
    sections = []
    if context['code_projects']:
        sections.append('## Code\n' + '\n'.join(
            f"- {_flat(p['name'])} — {', '.join(map(_flat, p['paths']))}" for p in context['code_projects']))
    if context.get('collections'):
        sections.append('## Documents\n' + '\n'.join(
            _line(c['name'], c['description'], []) for c in context['collections']))
    if context['environments']:
        sections.append('## Environments\n' + '\n'.join(_line(e['name'], e['description'], [
            ('servers: ', e['servers']), ('services: ', e['services']), ('deployments: ', e['deployments']),
            ('code: ', [_code(p) for p in e['code_projects']])]) for e in context['environments']))
    if context['services']:
        sections.append('## Services\n' + '\n'.join(_line(s['name'], s['description'], [
            ('deployed on ', s['deployments']), ('code: ', [_code(p) for p in s['code_projects']]),
            ('connections: ', s['connections'])]) for s in context['services']))
    if context['servers']:
        sections.append('## Servers\n' + '\n'.join(
            _line(f"{s['name']} ({s['hostname']})", s['description'], []) for s in context['servers']))
    return sections


def resource_context(context: dict[str, Any]) -> str:
    """What a run documents, for the ad-hoc agent's system prompt: the selection under one heading ('' for none)."""
    sections = _sections(context)
    return '\n\n'.join(['# What you are documenting', *sections]) if sections else ''
