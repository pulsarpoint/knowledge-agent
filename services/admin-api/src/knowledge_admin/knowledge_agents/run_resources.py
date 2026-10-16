"""Run resources: what one run of a per-run agent works on, and the configuration that run uses.

A per-run agent's saved selections (code projects, collections, SSH servers, services) are what a caller may pass;
each run passes its own. `resolve` checks the request and narrows each resource list of the agent's snapshot to it,
so the existing tools, which only reach what the configuration lists, reach exactly what the run was given. Nothing
passed for a kind leaves that list empty: never "all". Fixed agents keep their configuration and refuse run
resources. Credentials stay references; nothing is decrypted here."""

import copy
import os
from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Any, NamedTuple
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, field_validator

from knowledge_admin.rag.collections import document_collections
from knowledge_admin.data_root import data_path
from knowledge_admin.knowledge_agents.config import load_graphs, per_run

FIXED = 'This agent uses fixed resources.'
NOT_USED = 'This agent does not use {what}.'
NOT_ALLOWED = '{label} {name} is not allowed for this agent.'
NOT_AVAILABLE = '{label} {name} is not available: it was removed, archived or disabled.'


class Kind(NamedTuple):
    key: str  # the run resources key
    capability: str  # the config section with its switch
    label: str  # one resource, in messages
    plural: str  # the kind, in messages
    option: str  # the options() key
    type: str  # the access object's resource type


KINDS = (
    Kind('code_project_ids', 'code', 'Code project', 'code projects', 'code_projects', 'code_project'),
    Kind('collection_ids', 'documents', 'Collection', 'document collections', 'collections', 'collection'),
    Kind('server_ids', 'ssh', 'Server', 'servers', 'servers', 'server'),
    Kind('service_ids', 'services', 'Service', 'services', 'services', 'service'),
)


def picks(resources: dict[str, list] | None) -> list[dict[str, str]]:
    """Run resources ({code_project_ids, collection_ids, server_ids, service_ids}) as access picks [{type, id}], each
    once, in KINDS order."""
    return [
        {'type': kind.type, 'id': str(item)}
        for kind in KINDS
        for item in dict.fromkeys(str(item) for item in (resources or {}).get(kind.key) or [])
    ]


# The id, name and whether it can be given now, of the requested resources of one kind in this workspace.
FOUND = {
    'code_project_ids': 'SELECT id,name,archived_at IS NULL AS usable FROM ka.repositories WHERE project_id=%s AND id=ANY(%s)',
    'collection_ids': 'SELECT id,name,deleted_at IS NULL AS usable FROM ka.rag_collections WHERE project_id=%s AND id=ANY(%s)',
    'server_ids': 'SELECT id,name,ssh_allowed AS usable FROM ka.servers WHERE project_id=%s AND id=ANY(%s)',
    'service_ids': 'SELECT id,name,true AS usable FROM ka.services WHERE project_id=%s AND id=ANY(%s)',
}
# Every usable resource of one kind in this workspace (what "all" allows, and the options a caller sees).
LISTED = {
    'code_project_ids': 'SELECT id,name FROM ka.repositories WHERE project_id=%s AND archived_at IS NULL ORDER BY lower(name),id',
    'collection_ids': 'SELECT id,name FROM ka.rag_collections WHERE project_id=%s AND deleted_at IS NULL ORDER BY name,id',
    'server_ids': 'SELECT id,name FROM ka.servers WHERE project_id=%s AND ssh_allowed ORDER BY lower(name),id',
    'service_ids': 'SELECT id,name FROM ka.services WHERE project_id=%s ORDER BY lower(name),id',
}
CODE_PROJECTS = """SELECT r.id,r.name,r.description,r.remote_url AS remote_git_url,
    ARRAY(SELECT f.path::text FROM ka.code_project_folders f WHERE f.code_project_id=r.id ORDER BY f.path) AS folder_paths
    FROM ka.repositories r WHERE r.project_id=%s AND r.id=ANY(%s) AND r.archived_at IS NULL"""
COLLECTIONS = """SELECT c.id,c.name,c.description,
    ARRAY(SELECT p.source_path FROM ka.rag_collection_paths p WHERE p.collection_id=c.id ORDER BY p.source_path) AS source_paths
    FROM ka.rag_collections c WHERE c.project_id=%s AND c.id=ANY(%s) AND c.deleted_at IS NULL"""
# What services and environments bring: only usable code projects (not archived) and servers (SSH allowed).
ENVIRONMENT_SERVICES = """SELECT es.service_id AS id FROM ka.environment_services es JOIN ka.services s ON s.id=es.service_id
    WHERE es.project_id=%s AND es.environment_id=ANY(%s) ORDER BY lower(s.name),s.id"""
ENVIRONMENT_SERVERS = """SELECT es.server_id AS id FROM ka.environment_servers es JOIN ka.servers s ON s.id=es.server_id
    WHERE es.project_id=%s AND es.environment_id=ANY(%s) AND s.ssh_allowed ORDER BY lower(s.name),s.id"""
SERVICE_CODE = """SELECT r.id FROM ka.inventory_links l JOIN ka.repositories r ON r.id=l.to_code_project_id
    WHERE l.kind='built_from' AND r.project_id=%s AND l.from_service_id=ANY(%s) AND r.archived_at IS NULL
    ORDER BY lower(r.name),r.id"""
# The services deployed on one server (a server run reads them; they are its children in the run's access).
DEPLOYED_SERVICES = 'SELECT DISTINCT service_id AS id FROM ka.service_deployments WHERE server_id=%s'
SERVICE_SERVERS = """SELECT h.id FROM ka.service_deployments d JOIN ka.servers h ON h.id=d.server_id
    WHERE h.project_id=%s AND d.service_id=ANY(%s) AND h.ssh_allowed ORDER BY lower(h.name),h.id"""


class RunResourceError(ValueError):
    """The run cannot be given a resource; the message names it."""


class RunResources(BaseModel):
    """What a caller passes to one run of a per-run agent: each id once, in the caller's order."""

    model_config = ConfigDict(extra='forbid')
    code_project_ids: list[UUID] = Field(default_factory=list, max_length=50)
    collection_ids: list[UUID] = Field(default_factory=list, max_length=200)
    server_ids: list[UUID] = Field(default_factory=list, max_length=100)
    service_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @field_validator('code_project_ids', 'collection_ids', 'server_ids', 'service_ids')
    @classmethod
    def once(cls, value: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(value))


def _ids(rows: Iterable[dict[str, Any]]) -> list[str]:
    return [str(row['id']) for row in rows]


def _selection(config: dict[str, Any], kind: Kind) -> dict[str, dict[str, Any]] | None:
    """The agent's saved selection of one kind, by id, as its snapshot lists it; None when it allows any (All code
    projects, All collections)."""
    if kind.capability == 'code':
        return (
            None
            if config['code'].get('all_projects')
            else {str(item['id']): item for item in config['code']['projects']}
        )
    if kind.capability == 'documents':
        everything, items = document_collections(config)
        return None if everything else {str(item['id']): item for item in items if 'id' in item}
    if kind.capability == 'ssh':
        return {str(item['id']): item for item in config['ssh']['servers']}
    return {str(item['id']): item for item in config['services']['items']}


def _fresh(conn, project_id, kind: Kind, ids: list[str]) -> dict[str, dict[str, Any]]:
    """Code projects or collections an "all" selection allows, in the snapshot's shape (container paths)."""
    if kind.capability == 'code':
        rows = conn.execute(CODE_PROJECTS, (project_id, ids)).fetchall()
        root = PurePosixPath(os.environ.get('CODE_ROOT', ''))
        if rows and (root.anchor != '/' or '..' in root.parts):
            raise ValueError('CODE_ROOT must be an absolute path mounted in the agent container')
        for row in rows:
            row['folder_paths'] = [str(root / path) for path in row['folder_paths']]
            if row['remote_git_url'] is None:
                del row['remote_git_url']
    else:
        rows = conn.execute(COLLECTIONS, (project_id, ids)).fetchall()
        for row in rows:
            row['paths'] = [str(data_path(path)) for path in row.pop('source_paths')]
    return {str(row['id']): jsonable_encoder(row) for row in rows}


def bring(
    conn, project_id, service_ids: Iterable[str], environment_ids: Iterable[UUID | str] = ()
) -> dict[str, list[str]]:
    """What chosen services and environments bring: an environment its services and servers, a service (chosen or
    brought) its code projects and the servers it is deployed on. Only code projects not archived and servers with SSH
    allowed; the ids of what was chosen itself are not repeated in service_ids."""
    environments = [str(item) for item in environment_ids]
    brought: dict[str, list[str]] = {'code_project_ids': [], 'server_ids': [], 'service_ids': []}
    if environments:
        brought['service_ids'] = _ids(conn.execute(ENVIRONMENT_SERVICES, (project_id, environments)))
        brought['server_ids'] = _ids(conn.execute(ENVIRONMENT_SERVERS, (project_id, environments)))
    services = list(dict.fromkeys([*(str(item) for item in service_ids), *brought['service_ids']]))
    if services:
        brought['code_project_ids'] = _ids(conn.execute(SERVICE_CODE, (project_id, services)))
        brought['server_ids'] += _ids(conn.execute(SERVICE_SERVERS, (project_id, services)))
    return brought


def resolve(
    conn, project_id, config: dict[str, Any], requested: RunResources | None, *, environment_ids: Iterable[UUID] = ()
) -> dict[str, Any]:
    """The configuration one run uses.

    Fixed agents: their own configuration, unchanged; passing run resources is refused (FIXED).
    Per-run agents: a copy whose code projects, collections, SSH servers and services are exactly the run's resources,
    with `run_resources` recording them (what the tools may reach) and `chosen_resources` recording only what the
    caller chose, environments too, before anything is brought (what a new document links). A chosen service brings its code projects and the servers it is deployed on;
    an environment (Generate documents) brings its services and servers, and through them their code projects and
    servers. What they bring is added only for kinds the agent uses, and only when it can be given (not archived, SSH
    allowed, a usable connection). Every resource must exist in this workspace, be usable, be of a kind the agent uses and be inside the
    agent's saved selection; otherwise RunResourceError names it.
    """
    if not per_run(config):
        if requested is not None:
            raise RunResourceError(FIXED)
        return config
    effective = copy.deepcopy(config)
    requested = requested or RunResources()
    wanted = {kind.key: [str(item) for item in getattr(requested, kind.key)] for kind in KINDS}
    environments = list(dict.fromkeys(str(item) for item in environment_ids))
    chosen = {**{key: list(ids) for key, ids in wanted.items()}, 'environment_ids': environments}
    for kind in KINDS:
        if wanted[kind.key] and not effective[kind.capability]['enabled']:
            raise RunResourceError(NOT_USED.format(what=kind.plural))
    brought = bring(conn, project_id, wanted['service_ids'], environments)
    for kind in KINDS:
        if kind.key in brought and effective[kind.capability]['enabled']:
            extra = brought[kind.key]
            if kind.capability == 'ssh':  # a brought server it cannot connect to is left out, as archived projects are
                servers = _selection(effective, kind)
                extra = [item for item in extra if item not in servers or _usable_server(servers[item])]
            wanted[kind.key] = list(dict.fromkeys([*wanted[kind.key], *extra]))
    given: dict[str, list[dict[str, Any]]] = {}
    for kind in KINDS:
        ids = wanted[kind.key]
        found = {str(row['id']): row for row in conn.execute(FOUND[kind.key], (project_id, ids))} if ids else {}
        selection = _selection(effective, kind)
        for item in ids:
            row = found.get(item)
            if row is None or not row['usable']:
                raise RunResourceError(NOT_AVAILABLE.format(label=kind.label, name=row['name'] if row else item))
            if selection is not None and item not in selection:
                raise RunResourceError(NOT_ALLOWED.format(label=kind.label, name=row['name']))
            if kind.capability == 'ssh' and not (selection[item]['enabled'] and selection[item]['ssh_allowed']):
                raise RunResourceError(NOT_AVAILABLE.format(label=kind.label, name=row['name']))
        source = _fresh(conn, project_id, kind, ids) if selection is None and ids else (selection or {})
        given[kind.key] = [source[item] for item in ids]
    code_ids = set(wanted['code_project_ids'])
    effective['code'] = {**effective['code'], 'all_projects': False, 'projects': given['code_project_ids']}
    graphs = [graph for graph in effective['code'].get('graphs', []) if str(graph.get('id')) in code_ids]
    known = {str(graph['id']) for graph in graphs}
    missing = [item for item in wanted['code_project_ids'] if item not in known]
    if missing and effective['code']['enabled']:
        graphs += load_graphs(conn, project_id, missing)  # "All code projects": projects added after the snapshot
    effective['code']['graphs'] = graphs
    documents = {key: value for key, value in effective['documents'].items() if key not in ('folders', 'all_folders')}
    effective['documents'] = {**documents, 'all_collections': False, 'collections': given['collection_ids']}
    effective['ssh'] = {**effective['ssh'], 'servers': given['server_ids']}
    effective['services'] = {**effective['services'], 'items': given['service_ids']}
    effective['run_resources'] = wanted
    effective['chosen_resources'] = chosen
    return jsonable_encoder(effective)


def _usable_server(server: dict[str, Any]) -> bool:
    """As `resolve` checks it: the snapshot's server is enabled and allows SSH."""
    return bool(server['enabled'] and server['ssh_allowed'])


def options(conn, project_id, config: dict[str, Any]) -> dict[str, list[dict[str, str]] | None]:
    """What a caller may pass to one run of a per-run agent, per kind: the usable resources inside its saved selection
    (every usable one for All), as id and name; None for a kind the agent does not use."""
    result: dict[str, list[dict[str, str]] | None] = {}
    for kind in KINDS:
        if not config[kind.capability]['enabled']:
            result[kind.option] = None
            continue
        selection = _selection(config, kind)
        result[kind.option] = [
            {'id': str(row['id']), 'name': row['name']}
            for row in conn.execute(LISTED[kind.key], (project_id,))
            if selection is None
            or str(row['id']) in selection
            and (kind.capability != 'ssh' or _usable_server(selection[str(row['id'])]))
        ]
    return result
