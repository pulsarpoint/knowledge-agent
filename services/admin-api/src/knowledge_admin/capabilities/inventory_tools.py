"""Agent tools that read and change the inventory, thin over inventory/store.py (the one write path) and
inventory/model.py. Replies are JSON; a refusal is {"refused": text} (a store error's message is the refusal).

Access (each tool's pure check_<tool>): an item is usable when it is in the run's access object, or this session
created it (store.created_in_session); a deployment when its server, service or environment is usable; a connection
when its from-deployment is; a relation when its from-service is. Reads need the item usable. Writes need the role
inventory_editor, a session (its person, agent and session are the author), and every item they name usable; a new
server or service needs only an environment in the access. The write tools are offered only in a session turn
(tools/catalog.INVENTORY_WRITES): never to a subagent or an expert."""

import asyncio
import json
from functools import partial
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from pydantic import Field

from knowledge_admin import access as access_rules
from knowledge_admin.capabilities.inventory import UNAVAILABLE, connect, project_of
from knowledge_admin.inventory import model, store
from knowledge_admin.inventory.rules import RELATION_KINDS, SERVICE_ROLES, SERVICE_TYPES, Invalid
from knowledge_admin.tools.registry import tool

MAX_ITEMS = 100
OFF = 'This agent may not change the inventory (inventory editor is off).'
NO_SESSION = 'Inventory changes need a session.'
NO_REVISION = 'revision is required: the revision you read'
EITHER = 'Give either {0} or {1}.'
WORDS = {**access_rules.TYPE_WORDS, 'deployment': 'deployment', 'connection': 'connection', 'relation': 'relation'}
# A field naming another item → that item's kind.
REFERENCES = {
    'environment_id': 'environment',
    'server_id': 'server',
    'service_id': 'service',
    'deployment_id': 'deployment',
    'from_deployment_id': 'deployment',
    'to_deployment_id': 'deployment',
    'from_service_id': 'service',
    'to_service_id': 'service',
    'to_code_project_id': 'code_project',
}
WRITERS = {
    'server': (store.create_server, store.update_server, store.delete_server),
    'service': (store.create_service, store.update_service, store.delete_service),
    'deployment': (store.create_deployment, store.update_deployment, store.delete_deployment),
    'connection': (store.create_connection, store.update_connection, store.delete_connection),
    'relation': (store.create_relation, store.update_relation, store.delete_relation),
}
Kind = Literal['server', 'service', 'deployment', 'connection', 'relation']


def refuse(kind: str) -> str:
    return access_rules.NOT_AVAILABLE.format(type=WORDS[kind])


def usable(access: dict, kind: str, item_id, created=frozenset(), rows=None) -> bool:
    """In the access, created by this session, or (deployment, connection, relation) reached through its row."""
    if (kind, str(item_id)) in created:
        return True
    if kind in access_rules.TYPES:
        return access_rules.can_use(access, kind, item_id)
    row = (rows or {}).get((kind, str(item_id)))
    if row is None:
        return False
    if kind == 'deployment':
        return any(usable(access, end, row[f'{end}_id'], created, rows) for end in ('server', 'service', 'environment'))
    if kind == 'connection':
        return usable(access, 'deployment', row['from_deployment_id'], created, rows)
    return usable(access, 'service', row['from_service_id'], created, rows)  # a relation


def refs(kind: str | None, item_id, values: dict) -> list[tuple[str, Any]]:
    """The items a call names: the item itself (kind, id) and every reference among its values."""
    found = [] if item_id is None else [(kind, item_id)]
    return found + [(REFERENCES[k], v) for k, v in values.items() if k in REFERENCES and v is not None]


def first_refused(access: dict, named: list[tuple[str, Any]], created=frozenset(), rows=None) -> str | None:
    for kind, item_id in named:
        if not usable(access, kind, item_id, created, rows):
            return refuse(kind)
    return None


def _write(access: dict, kind: str, id, values: dict, created, rows) -> str | None:
    if not access_rules.is_inventory_editor(access):
        return OFF
    if id is None and kind in ('server', 'service') and not access_rules.has_any(access, 'environment'):
        return access_rules.refuse_none('environment')
    return first_refused(access, refs(kind, id, values), created, rows)


def _either(access: dict, values: dict, created, rows) -> str | None:
    given = {k: v for k, v in values.items() if v is not None}
    if len(given) != 1:
        return EITHER.format(*values)
    return first_refused(access, refs(None, None, given), created, rows)


# The checks: pure, over the run's access, the items this session created and the rows of the items named.


def check_get_environment_model(access: dict, environment_id=None, created=frozenset(), rows=None, **_) -> str | None:
    return first_refused(access, [('environment', environment_id)], created, rows)


def check_list_servers(access: dict, environment_id=None, created=frozenset(), rows=None, **_) -> str | None:
    return first_refused(access, [('environment', environment_id)], created, rows)


def check_list_deployments(
    access: dict, server_id=None, service_id=None, created=frozenset(), rows=None, **_
) -> str | None:
    return _either(access, {'server_id': server_id, 'service_id': service_id}, created, rows)


def check_list_connections(
    access: dict, service_id=None, deployment_id=None, created=frozenset(), rows=None, **_
) -> str | None:
    return _either(access, {'service_id': service_id, 'deployment_id': deployment_id}, created, rows)


def check_save_server(access: dict, id=None, revision=None, created=frozenset(), rows=None, **values) -> str | None:
    return _write(access, 'server', id, values, created, rows)


def check_save_service(access: dict, id=None, revision=None, created=frozenset(), rows=None, **values) -> str | None:
    return _write(access, 'service', id, values, created, rows)


def check_save_deployment(access: dict, id=None, revision=None, created=frozenset(), rows=None, **values) -> str | None:
    return _write(access, 'deployment', id, values, created, rows)


def check_save_connection(access: dict, id=None, revision=None, created=frozenset(), rows=None, **values) -> str | None:
    return _write(access, 'connection', id, values, created, rows)


def check_save_relation(access: dict, id=None, revision=None, created=frozenset(), rows=None, **values) -> str | None:
    return _write(access, 'relation', id, values, created, rows)


def check_remove_inventory_item(access: dict, kind: str, id=None, created=frozenset(), rows=None, **_) -> str | None:
    return _write(access, kind, id, {}, created, rows)


# The tools' I/O


def _reply(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _shown(kind: str, row: dict) -> dict:
    """A saved item as a tool shows it: a server without its SSH settings (the model's fields)."""
    return {f: row[f] for f in model.SERVER_FIELDS} if kind == 'server' else row


def _normal(kind: str | None, item_id, values: dict) -> tuple[str | None, dict] | str:
    """The item id and the referenced ids as canonical text, or the refusal for one that is not an id (as for an item
    not in the run)."""
    try:
        found_id = None if item_id is None else str(UUID(str(item_id)))
    except ValueError:
        return refuse(kind)
    found = dict(values)
    for field, value in values.items():
        if field in REFERENCES and value is not None:
            try:
                found[field] = str(UUID(str(value)))
            except ValueError:
                return refuse(REFERENCES[field])
    return found_id, found


def _facts(conn, project: UUID, session, named: list[tuple[str, str]]) -> tuple[set, dict]:
    """What the checks decide on: the items this session created, and the rows of the named deployments, connections
    and relations (a connection's from-deployment too); an item not in the project has no row."""
    created = store.created_in_session(conn, project, session) if session else set()
    rows = {}
    pending = [(k, v) for k, v in named if k in ('deployment', 'connection', 'relation')]
    while pending:
        kind, item_id = pending.pop()
        try:
            row = store.get(conn, project, kind, UUID(str(item_id)))
        except store.NotFound:
            continue
        rows[(kind, str(item_id))] = row
        if kind == 'connection':
            pending.append(('deployment', row['from_deployment_id']))
    return created, rows


def _guarded(run) -> str:
    """run() → its reply; a store error is the refusal (the transaction rolled back), so is an unavailable database."""
    try:
        return _reply(run())
    except (Invalid, store.NotFound, store.Conflict) as error:
        return _reply({'refused': str(error)})
    except ValueError as error:
        if str(error) != UNAVAILABLE:
            raise
        return _reply({'refused': UNAVAILABLE})
    except psycopg.OperationalError:
        return _reply({'refused': UNAVAILABLE})


def _read(config: dict[str, Any], check, values: dict, answer) -> str:
    """check(access, created=, rows=, **values) on the named item, then answer(conn, project, {field: UUID})."""
    normal = _normal(None, None, values)
    if isinstance(normal, str):
        return _reply({'refused': normal})
    given = {k: v for k, v in normal[1].items() if v is not None}

    def run():
        with connect() as conn:
            project = project_of(conn, config)
            named = refs(None, None, given)
            created, rows = _facts(conn, project, config.get('session_id'), named)
            if refusal := check(config['access'], created=created, rows=rows, **given):
                return {'refused': refusal}
            for kind, item_id in named:
                try:
                    store.get(conn, project, kind, UUID(item_id))
                except store.NotFound:
                    return {'refused': refuse(kind)}  # an id of another project reads as not available
            return answer(conn, project, {k: UUID(v) for k, v in given.items()})

    return _guarded(run)


def _listed(key: str, items: list[dict]) -> dict:
    return {key: items[:MAX_ITEMS], 'truncated': len(items) > MAX_ITEMS}


def _change(config: dict[str, Any], kind: str, item_id, revision, values: dict, check, remove: bool = False) -> str:
    """Create (no id), update (id and revision) or remove one item as the session's person, agent and session."""
    access = config['access']
    if not access_rules.is_inventory_editor(access):
        return _reply({'refused': OFF})
    session = config.get('session_id')
    if not session:
        return _reply({'refused': NO_SESSION})
    if item_id is not None and revision is None:
        return _reply({'refused': NO_REVISION})
    normal = _normal(kind, item_id, values)
    if isinstance(normal, str):
        return _reply({'refused': normal})
    found_id, found = normal
    create, update, delete = WRITERS[kind]

    def run():
        with connect() as conn:
            project = project_of(conn, config)
            started = conn.execute(
                'SELECT actor_id,agent_id FROM ka.agent_sessions WHERE id=%s', (str(session),)
            ).fetchone()
            if started is None:
                return {'refused': NO_SESSION}
            store.lock_project(conn, project)
            created, rows = _facts(conn, project, session, refs(kind, found_id, found))
            if refusal := check(access, id=found_id, created=created, rows=rows, **found):
                return {'refused': refusal}
            author = {'actor_id': started['actor_id'], 'agent_id': started['agent_id'], 'session_id': str(session)}
            if remove:
                return {'removed': delete(conn, project, UUID(found_id), revision, author)['id']}
            if found_id is None:
                return _shown(kind, create(conn, project, found, author))
            return _shown(kind, update(conn, project, UUID(found_id), revision, found, author))

    return _guarded(run)


def _given(**values) -> dict:
    """The values the call gave (None: not given; an update keeps the saved value)."""
    return {k: v for k, v in values.items() if v is not None}


Id = Annotated[str, Field(min_length=1, max_length=64)]
OptionalId = Annotated[str | None, Field(max_length=64)]
ItemId = Annotated[str | None, Field(max_length=64, description='The item to update; omit to create one.')]
Revision = Annotated[int | None, Field(ge=1, description='With id: the revision you read (from the model or a list).')]
Text = Annotated[str | None, Field(max_length=4000)]
Port = Annotated[int | None, Field(ge=1, le=65535)]


@tool
async def get_environment_model(
    context, environment_id: Annotated[Id, Field(description='The environment id.')]
) -> str:
    """One environment as JSON: its servers with their deployments (service, runtime, port, version), its services,
    the connections touching it (an end in another environment under outside), external hosts with their ports, and
    the relations of its services. Every item carries the id and revision you need to change it."""
    return await asyncio.to_thread(
        _read,
        context['config'],
        check_get_environment_model,
        {'environment_id': environment_id},
        lambda conn, project, ids: model.build_model(**store.load_model_rows(conn, project, ids['environment_id'])),
    )


@tool
async def list_servers(context, environment_id: Annotated[Id, Field(description='The environment id.')]) -> str:
    """The servers with a deployment in the environment, as JSON (id, name, hostname, description, tags, revision)."""
    return await asyncio.to_thread(
        _read,
        context['config'],
        check_list_servers,
        {'environment_id': environment_id},
        lambda conn, project, ids: _listed(
            'servers', [_shown('server', s) for s in store.list_servers(conn, project, **ids)]
        ),
    )


@tool
async def list_deployments(
    context,
    server_id: Annotated[OptionalId, Field(description='The deployments on this server.')] = None,
    service_id: Annotated[OptionalId, Field(description='The deployments of this service.')] = None,
) -> str:
    """The deployments on a server or of a service (give one), as JSON: service, server, environment, runtime, port,
    version and revision."""
    return await asyncio.to_thread(
        _read,
        context['config'],
        check_list_deployments,
        {'server_id': server_id, 'service_id': service_id},
        lambda conn, project, ids: _listed('deployments', store.list_deployments(conn, project, **ids)),
    )


@tool
async def list_connections(
    context,
    service_id: Annotated[OptionalId, Field(description="Connections of the service's deployments.")] = None,
    deployment_id: Annotated[OptionalId, Field(description='Connections of this deployment.')] = None,
) -> str:
    """The connections from or to a service's deployments or one deployment (give one), as JSON: from and to
    deployment or external host, port, protocol, description and revision."""
    return await asyncio.to_thread(
        _read,
        context['config'],
        check_list_connections,
        {'service_id': service_id, 'deployment_id': deployment_id},
        lambda conn, project, ids: _listed('connections', store.list_connections(conn, project, **ids)),
    )


@tool
async def save_server(
    context,
    id: ItemId = None,
    revision: Revision = None,
    name: Annotated[str | None, Field(max_length=200, description='Required to create.')] = None,
    hostname: Annotated[
        str | None, Field(max_length=253, description='IP address or hostname; required to create.')
    ] = None,
    description: Text = None,
    tags: Annotated[list[str] | None, Field(max_length=32)] = None,
) -> str:
    """Create a server (without id; needs an environment in this run) or update one (id and the revision you read;
    omitted fields keep their values). Then deploy services on it with save_deployment. SSH access is set up by a
    person, not here. Returns the saved server as JSON."""
    values = _given(name=name, hostname=hostname, description=description, tags=tags)
    return await asyncio.to_thread(_change, context['config'], 'server', id, revision, values, check_save_server)


@tool
async def save_service(
    context,
    id: ItemId = None,
    revision: Revision = None,
    name: Annotated[str | None, Field(max_length=200, description='Required to create.')] = None,
    service_type: Literal[SERVICE_TYPES] | None = None,
    service_role: Literal[SERVICE_ROLES] | None = None,
    description: Text = None,
    owner: Annotated[str | None, Field(max_length=200)] = None,
) -> str:
    """Create a service (without id; needs an environment in this run) or update one (id and the revision you read;
    omitted fields keep their values). Returns the saved service as JSON."""
    values = _given(
        name=name, service_type=service_type, service_role=service_role, description=description, owner=owner
    )
    return await asyncio.to_thread(_change, context['config'], 'service', id, revision, values, check_save_service)


@tool
async def save_deployment(
    context,
    id: ItemId = None,
    revision: Revision = None,
    service_id: Annotated[OptionalId, Field(description='The service deployed; required to create.')] = None,
    server_id: Annotated[OptionalId, Field(description='The server it runs on; required to create.')] = None,
    environment_id: Annotated[OptionalId, Field(description='Its environment; required to create.')] = None,
    runtime: Annotated[str | None, Field(max_length=80, description='e.g. systemd, docker, k8s.')] = None,
    runtime_name: Annotated[str | None, Field(max_length=200, description='Unit, container or pod name.')] = None,
    port: Port = None,
    log_location: Annotated[str | None, Field(max_length=1000)] = None,
    deployed_version: Annotated[str | None, Field(max_length=200)] = None,
) -> str:
    """Record that a service runs on a server in an environment (without id), or update a deployment (id and the
    revision you read; omitted fields keep their values). Returns the saved deployment as JSON."""
    values = _given(
        service_id=service_id,
        server_id=server_id,
        environment_id=environment_id,
        runtime=runtime,
        runtime_name=runtime_name,
        port=port,
        log_location=log_location,
        deployed_version=deployed_version,
    )
    return await asyncio.to_thread(
        _change, context['config'], 'deployment', id, revision, values, check_save_deployment
    )


@tool
async def save_connection(
    context,
    id: ItemId = None,
    revision: Revision = None,
    from_deployment_id: Annotated[
        OptionalId, Field(description='The deployment that connects; required to create.')
    ] = None,
    to_deployment_id: Annotated[OptionalId, Field(description='The deployment it connects to (or to_host).')] = None,
    to_host: Annotated[
        str | None, Field(max_length=253, description='An external host it connects to (or to_deployment_id).')
    ] = None,
    port: Annotated[Port, Field(description="Needed for a host; a deployment's own port when omitted.")] = None,
    protocol: Annotated[str | None, Field(max_length=20, description='e.g. tcp (default), http, postgres.')] = None,
    description: Text = None,
) -> str:
    """Record that a deployment connects to another deployment or an external host on a port (without id), or update a
    connection (id and the revision you read; omitted fields keep their values). Returns the saved connection as
    JSON."""
    values = _given(
        from_deployment_id=from_deployment_id,
        to_deployment_id=to_deployment_id,
        to_host=to_host,
        port=port,
        protocol=protocol,
        description=description,
    )
    return await asyncio.to_thread(
        _change, context['config'], 'connection', id, revision, values, check_save_connection
    )


@tool
async def save_relation(
    context,
    id: ItemId = None,
    revision: Revision = None,
    from_service_id: Annotated[OptionalId, Field(description='The service; required to create.')] = None,
    kind: Annotated[
        Literal[RELATION_KINDS] | None,
        Field(description='depends_on and part_of point at a service, built_from at a code project.'),
    ] = None,
    to_service_id: OptionalId = None,
    to_code_project_id: OptionalId = None,
    description: Text = None,
) -> str:
    """Record how a service relates to another service or a code project (without id), or update a relation (id and
    the revision you read; omitted fields keep their values). Returns the saved relation as JSON."""
    values = _given(
        from_service_id=from_service_id,
        kind=kind,
        to_service_id=to_service_id,
        to_code_project_id=to_code_project_id,
        description=description,
    )
    return await asyncio.to_thread(_change, context['config'], 'relation', id, revision, values, check_save_relation)


@tool
async def remove_inventory_item(
    context,
    kind: Kind,
    id: Annotated[Id, Field(description='The item to remove.')],
    revision: Annotated[int, Field(ge=1, description='The revision you read.')],
) -> str:
    """Remove a server, service, deployment, connection or relation. A deployment takes its connections with it; a
    server or service is refused while deployments use it. Returns {"removed": id}."""
    check = partial(check_remove_inventory_item, kind=kind)
    return await asyncio.to_thread(_change, context['config'], kind, id, revision, {}, check, True)
