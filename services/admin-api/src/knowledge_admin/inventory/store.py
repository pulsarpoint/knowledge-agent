"""The one write path for the inventory: servers, services, environments, deployments, connections and service
relations. Every function works in the caller's transaction (the caller commits), scoped to one project, and returns
plain dicts. Writes lock the project row first (every inventory writer takes that lock first, so concurrent writers
serialize instead of deadlocking), validate with rules.py, check that referenced items exist in the project, check the
revision on update and delete, and record an audit event with the person and, for agent runs, the agent and session.

author: {'actor_id': principal id or None, 'agent_id': agent id or None, 'session_id': agent session id or None}.
Errors: rules.Invalid (bad values or a missing reference), NotFound (the item is not in this project), Conflict (stale
revision, duplicate, or a delete refused while deployments use the item)."""

from uuid import UUID

import psycopg

from knowledge_admin.db import audit
from knowledge_admin.inventory import rules
from knowledge_admin.inventory.rules import Invalid


class NotFound(LookupError):
    pass


class Conflict(Exception):
    pass


# What a reader gets: public columns only (no SSH credentials, no free-text deployment label).
SERVER = 'SELECT x.* FROM ka.server_assets x'
SERVICE = """SELECT x.id,x.project_id,x.name,x.description,x.owner,x.service_type::text AS service_type,
    x.service_role::text AS service_role,x.revision,x.created_by,x.created_at,x.updated_at FROM ka.services x"""
ENVIRONMENT = 'SELECT x.id,x.project_id,x.name,x.description,x.revision,x.created_by,x.created_at,x.updated_at FROM ka.environments x'
DEPLOYMENT = """SELECT x.id,x.project_id,x.service_id,x.server_id,x.environment_id,x.runtime,x.runtime_name,x.port,
    x.log_location,x.deployed_version,x.revision FROM ka.service_deployments x"""
CONNECTION = 'SELECT x.* FROM ka.connections x'
RELATION = 'SELECT x.* FROM ka.service_relations x'
DEPLOYMENT_IDENTITY = (
    'ka.service_deployments_service_id_server_id_environment_runtim_key,ka.service_deployments_environment_identity'
)

KINDS = {
    'server': {
        'table': 'ka.servers',
        'read': SERVER,
        'rule': rules.server,
        'fields': rules.SERVER_FIELDS,
        'duplicate': 'A server with this name already exists',
    },
    'service': {
        'table': 'ka.services',
        'read': SERVICE,
        'rule': rules.service,
        'fields': rules.SERVICE_FIELDS,
        'duplicate': 'A service with this name already exists',
    },
    'environment': {
        'table': 'ka.environments',
        'read': ENVIRONMENT,
        'rule': rules.environment,
        'fields': rules.ENVIRONMENT_FIELDS,
        'duplicate': 'An environment with this name already exists',
    },
    'deployment': {
        'table': 'ka.service_deployments',
        'read': DEPLOYMENT,
        'rule': rules.deployment,
        'fields': rules.DEPLOYMENT_FIELDS,
        'duplicate': 'This service already has a deployment with this runtime name on this server in this environment',
    },
    'connection': {
        'table': 'ka.connections',
        'read': CONNECTION,
        'rule': rules.connection,
        'fields': rules.CONNECTION_FIELDS,
        'duplicate': 'This connection already exists',
    },
    'relation': {
        'table': 'ka.service_relations',
        'read': RELATION,
        'rule': rules.relation,
        'fields': rules.RELATION_FIELDS,
        'duplicate': 'This relation already exists',
    },
}


# Reads


def list_servers(conn, project_id: UUID, environment_id: UUID | None = None) -> list[dict]:
    """Servers, or the servers with a deployment in the environment."""
    return conn.execute(
        f"""{SERVER} WHERE x.project_id=%(p)s AND (%(e)s::uuid IS NULL OR EXISTS
        (SELECT 1 FROM ka.service_deployments d WHERE d.server_id=x.id AND d.environment_id=%(e)s))
        ORDER BY lower(x.name),x.id""",
        {'p': project_id, 'e': environment_id},
    ).fetchall()


def list_services(conn, project_id: UUID, environment_id: UUID | None = None) -> list[dict]:
    """Services, or the services with a deployment in the environment."""
    return conn.execute(
        f"""{SERVICE} WHERE x.project_id=%(p)s AND (%(e)s::uuid IS NULL OR EXISTS
        (SELECT 1 FROM ka.service_deployments d WHERE d.service_id=x.id AND d.environment_id=%(e)s))
        ORDER BY lower(x.name),x.id""",
        {'p': project_id, 'e': environment_id},
    ).fetchall()


def list_environments(conn, project_id: UUID, server_id: UUID | None = None) -> list[dict]:
    """Environments, or the environments the server has a deployment in."""
    return conn.execute(
        f"""{ENVIRONMENT} WHERE x.project_id=%(p)s AND (%(s)s::uuid IS NULL OR EXISTS
        (SELECT 1 FROM ka.service_deployments d WHERE d.environment_id=x.id AND d.server_id=%(s)s))
        ORDER BY lower(x.name),x.id""",
        {'p': project_id, 's': server_id},
    ).fetchall()


def list_deployments(
    conn,
    project_id: UUID,
    server_id: UUID | None = None,
    service_id: UUID | None = None,
    environment_id: UUID | None = None,
) -> list[dict]:
    return conn.execute(
        f"""{DEPLOYMENT} WHERE x.project_id=%(p)s AND (%(server)s::uuid IS NULL OR x.server_id=%(server)s)
        AND (%(service)s::uuid IS NULL OR x.service_id=%(service)s)
        AND (%(environment)s::uuid IS NULL OR x.environment_id=%(environment)s)
        ORDER BY x.runtime_name,x.id""",
        {'p': project_id, 'server': server_id, 'service': service_id, 'environment': environment_id},
    ).fetchall()


def list_connections(
    conn, project_id: UUID, deployment_id: UUID | None = None, service_id: UUID | None = None
) -> list[dict]:
    """Connections, or those from or to the deployment, or from or to any deployment of the service."""
    return conn.execute(
        f"""{CONNECTION} WHERE x.project_id=%(p)s
        AND (%(d)s::uuid IS NULL OR %(d)s IN (x.from_deployment_id,x.to_deployment_id))
        AND (%(s)s::uuid IS NULL OR EXISTS (SELECT 1 FROM ka.service_deployments d
            WHERE d.service_id=%(s)s AND d.id IN (x.from_deployment_id,x.to_deployment_id)))
        ORDER BY x.created_at,x.id""",
        {'p': project_id, 'd': deployment_id, 's': service_id},
    ).fetchall()


def list_relations(conn, project_id: UUID, service_id: UUID | None = None) -> list[dict]:
    """Relations, or those from or to the service."""
    return conn.execute(
        f"""{RELATION} WHERE x.project_id=%(p)s AND (%(s)s::uuid IS NULL OR %(s)s IN (x.from_service_id,x.to_service_id))
        ORDER BY x.created_at,x.id""",
        {'p': project_id, 's': service_id},
    ).fetchall()


def get(conn, project_id: UUID, kind: str, item_id: UUID) -> dict:
    """One item of the project; NotFound when it is not there."""
    row = conn.execute(f'{KINDS[kind]["read"]} WHERE x.id=%s AND x.project_id=%s', (item_id, project_id)).fetchone()
    if row is None:
        raise NotFound(f'{kind.capitalize()} not found')
    return row


# Helpers


def _get(conn, kind: str, item_id: UUID) -> dict:
    return conn.execute(f'{KINDS[kind]["read"]} WHERE x.id=%s', (item_id,)).fetchone()


def lock_project(conn, project_id: UUID) -> None:
    """Every inventory writer takes this lock first (callers that upsert the person take it before that)."""
    conn.execute('SELECT id FROM ka.projects WHERE id=%s FOR NO KEY UPDATE', (project_id,))


def _locked(conn, kind: str, project_id: UUID, item_id: UUID, revision: int) -> dict:
    row = conn.execute(
        f'SELECT * FROM {KINDS[kind]["table"]} WHERE id=%s AND project_id=%s FOR UPDATE', (item_id, project_id)
    ).fetchone()
    if row is None:
        raise NotFound(f'{kind.capitalize()} not found')
    if row['revision'] != revision:
        raise Conflict(
            f'This {kind} changed since you read it (now revision {row["revision"]}). Read it again and retry'
        )
    return row


def _require(conn, table: str, project_id: UUID, item_id: UUID | None, label: str) -> dict | None:
    if item_id is None:
        return None
    row = conn.execute(f'SELECT * FROM {table} WHERE id=%s AND project_id=%s', (item_id, project_id)).fetchone()
    if row is None:
        raise Invalid(f'Choose an existing {label} in this project')
    return row


def _references(conn, project_id: UUID, kind: str, item: dict) -> dict:
    """Check the item's references exist in the project; returns extra columns to write."""
    if kind == 'deployment':
        _require(conn, 'ka.services', project_id, item['service_id'], 'service')
        _require(conn, 'ka.servers', project_id, item['server_id'], 'server')
        environment = _require(conn, 'ka.environments', project_id, item['environment_id'], 'environment')
        return {'environment': environment['name']}  # the free-text label, until 0126 drops it
    if kind == 'connection':
        _require(conn, 'ka.service_deployments', project_id, item['from_deployment_id'], 'deployment to connect from')
        target = _require(
            conn, 'ka.service_deployments', project_id, item['to_deployment_id'], 'deployment to connect to'
        )
        if target is not None and item['port'] is None:
            return {'port': target['port']}
    if kind == 'relation':
        _require(conn, 'ka.services', project_id, item['from_service_id'], 'service')
        _require(conn, 'ka.services', project_id, item['to_service_id'], 'service to relate to')
        _require(conn, 'ka.repositories', project_id, item['to_code_project_id'], 'code project')
    return {}


def _write(conn, kind: str, sql: str, params: list) -> UUID:
    # A deployment's identity constraints are deferrable (left from the old service form): check them now, so a
    # duplicate is a Conflict here and not a failed commit.
    if kind == 'deployment':
        conn.execute(f'SET CONSTRAINTS {DEPLOYMENT_IDENTITY} IMMEDIATE')
    try:
        with conn.transaction():  # a savepoint: a duplicate leaves the caller's transaction usable
            return conn.execute(sql, params).fetchone()['id']
    except psycopg.errors.UniqueViolation:
        raise Conflict(KINDS[kind]['duplicate']) from None


def _audit(conn, project_id: UUID, author: dict, action: str, kind: str, item_id: UUID, details: dict) -> None:
    audit(
        conn,
        project_id,
        author.get('actor_id'),
        action,
        kind,
        item_id,
        {**details, 'agent_id': author.get('agent_id'), 'session_id': author.get('session_id')},
    )


def _create(conn, project_id: UUID, kind: str, values: dict, author: dict) -> dict:
    lock_project(conn, project_id)
    item = KINDS[kind]['rule'](values)
    columns = {**item, **_references(conn, project_id, kind, item)}
    if kind == 'server':
        columns['enabled'] = False  # SSH access is set up separately
    if kind in ('connection', 'relation'):
        columns |= {
            'created_by': author.get('actor_id'),
            'created_by_agent': author.get('agent_id'),
            'session_id': author.get('session_id'),
        }
    names = ['project_id', *columns]
    item_id = _write(
        conn,
        kind,
        f'INSERT INTO {KINDS[kind]["table"]}({",".join(names)}) VALUES ({",".join(["%s"] * len(names))}) RETURNING id',
        [project_id, *columns.values()],
    )
    _audit(conn, project_id, author, f'{kind}.created', kind, item_id, {'revision': 1, **item})
    return _get(conn, kind, item_id)


def _update(conn, project_id: UUID, kind: str, item_id: UUID, revision: int, changes: dict, author: dict) -> dict:
    """Changes are merged into the saved item; the whole item is validated again."""
    lock_project(conn, project_id)
    row = _locked(conn, kind, project_id, item_id, revision)
    saved = {field: row[field] for field in KINDS[kind]['fields']}
    if kind == 'connection' and 'to_deployment_id' in changes and 'port' not in changes:
        saved['port'] = None  # a new target brings its own port
    item = KINDS[kind]['rule']({**saved, **changes})
    columns = {**item, **_references(conn, project_id, kind, item)}
    sets = ','.join(f'{name}=%s' for name in columns)
    _write(
        conn,
        kind,
        f'UPDATE {KINDS[kind]["table"]} SET {sets},revision=revision+1 WHERE id=%s RETURNING id',
        [*columns.values(), item_id],
    )
    _audit(conn, project_id, author, f'{kind}.updated', kind, item_id, {'revision': revision + 1, **item})
    return _get(conn, kind, item_id)


def _refuse_with_deployments(conn, kind: str, item_id: UUID) -> None:
    count = conn.execute(f'SELECT count(*) AS n FROM ka.service_deployments WHERE {kind}_id=%s', (item_id,)).fetchone()[
        'n'
    ]
    if count:
        raise Conflict(f'This {kind} is used by {count} deployment(s). Remove those deployments first')


def _delete(conn, project_id: UUID, kind: str, item_id: UUID, revision: int, author: dict) -> dict:
    lock_project(conn, project_id)
    row = _locked(conn, kind, project_id, item_id, revision)
    details = {'revision': revision, **{field: row[field] for field in KINDS[kind]['fields']}}
    if kind in ('server', 'service', 'environment'):
        _refuse_with_deployments(conn, kind, item_id)
    if kind == 'server' and conn.execute('SELECT 1 FROM ka.jobs WHERE server_id=%s LIMIT 1', (item_id,)).fetchone():
        raise Conflict('This server has connection-test jobs or history and cannot be deleted')
    if kind == 'deployment':
        details['connection_ids'] = [
            r['id']
            for r in conn.execute(
                """DELETE FROM ka.connections WHERE from_deployment_id=%s OR to_deployment_id=%s
                RETURNING id""",
                (item_id, item_id),
            )
        ]
    conn.execute(f'DELETE FROM {KINDS[kind]["table"]} WHERE id=%s', (item_id,))
    if kind == 'server':
        conn.execute(  # the server's own key, unless another server uses it
            """DELETE FROM ka.credentials c WHERE id=%s AND kind='ssh_private_key'
            AND NOT EXISTS (SELECT 1 FROM ka.servers s WHERE s.credential_id=c.id)""",
            (row['credential_id'],),
        )
    _audit(conn, project_id, author, f'{kind}.deleted', kind, item_id, details)
    return {'id': item_id}


# Writes


def create_server(conn, project_id: UUID, values: dict, author: dict) -> dict:
    return _create(conn, project_id, 'server', values, author)


def update_server(conn, project_id: UUID, server_id: UUID, revision: int, changes: dict, author: dict) -> dict:
    return _update(conn, project_id, 'server', server_id, revision, changes, author)


def delete_server(conn, project_id: UUID, server_id: UUID, revision: int, author: dict) -> dict:
    """Refused while deployments use it or it has connection-test jobs; its own SSH key goes with it."""
    return _delete(conn, project_id, 'server', server_id, revision, author)


def create_service(conn, project_id: UUID, values: dict, author: dict) -> dict:
    return _create(conn, project_id, 'service', values, author)


def update_service(conn, project_id: UUID, service_id: UUID, revision: int, changes: dict, author: dict) -> dict:
    return _update(conn, project_id, 'service', service_id, revision, changes, author)


def delete_service(conn, project_id: UUID, service_id: UUID, revision: int, author: dict) -> dict:
    """Refused while deployments use it; its relations go with it."""
    return _delete(conn, project_id, 'service', service_id, revision, author)


def create_environment(conn, project_id: UUID, values: dict, author: dict) -> dict:
    return _create(conn, project_id, 'environment', values, author)


def update_environment(
    conn, project_id: UUID, environment_id: UUID, revision: int, changes: dict, author: dict
) -> dict:
    return _update(conn, project_id, 'environment', environment_id, revision, changes, author)


def delete_environment(conn, project_id: UUID, environment_id: UUID, revision: int, author: dict) -> dict:
    """Refused while deployments are in it."""
    return _delete(conn, project_id, 'environment', environment_id, revision, author)


def create_deployment(conn, project_id: UUID, values: dict, author: dict) -> dict:
    return _create(conn, project_id, 'deployment', values, author)


def update_deployment(conn, project_id: UUID, deployment_id: UUID, revision: int, changes: dict, author: dict) -> dict:
    return _update(conn, project_id, 'deployment', deployment_id, revision, changes, author)


def delete_deployment(conn, project_id: UUID, deployment_id: UUID, revision: int, author: dict) -> dict:
    """Removes its connections (both directions) too."""
    return _delete(conn, project_id, 'deployment', deployment_id, revision, author)


def create_connection(conn, project_id: UUID, values: dict, author: dict) -> dict:
    """Without a port, a connection to a deployment takes that deployment's port."""
    return _create(conn, project_id, 'connection', values, author)


def update_connection(conn, project_id: UUID, connection_id: UUID, revision: int, changes: dict, author: dict) -> dict:
    return _update(conn, project_id, 'connection', connection_id, revision, changes, author)


def delete_connection(conn, project_id: UUID, connection_id: UUID, revision: int, author: dict) -> dict:
    return _delete(conn, project_id, 'connection', connection_id, revision, author)


def create_relation(conn, project_id: UUID, values: dict, author: dict) -> dict:
    return _create(conn, project_id, 'relation', values, author)


def update_relation(conn, project_id: UUID, relation_id: UUID, revision: int, changes: dict, author: dict) -> dict:
    return _update(conn, project_id, 'relation', relation_id, revision, changes, author)


def delete_relation(conn, project_id: UUID, relation_id: UUID, revision: int, author: dict) -> dict:
    return _delete(conn, project_id, 'relation', relation_id, revision, author)
