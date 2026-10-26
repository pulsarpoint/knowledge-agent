"""The one write path for the inventory: servers, services, environments, deployments, connections, service
relations, code project dependencies and which servers are in which environment (membership). Every function works
in the caller's transaction (the caller commits), scoped to one project, and returns plain dicts. Writes lock the project row first (every
inventory writer takes that lock first, so concurrent writers serialize instead of deadlocking), validate with
rules.py, check that referenced items exist in the project, check the revision on update and delete, and record an
audit event with the person and, for agent runs, the agent and session.
A deployment's server is always a member of the deployment's environment: creating or moving a deployment adds the
membership when it is missing.
Every entry is a draft (an agent's write, status='draft') or confirmed (a person's, the default). Updating keeps the
status; an agent cannot update a confirmed entry. A confirmed entry never needs a draft: a person's write confirms the
drafts it refers to. confirm and discard (people only) follow rules.confirm_reach and rules.discard_reach.

author: {'actor_id': principal id or None, 'agent_id': agent id or None, 'session_id': agent session id or None}.
Errors: rules.Invalid (bad values or a missing reference), NotFound (the item is not in this project), Conflict (a
stale revision, which is Stale, a duplicate, or a delete refused while deployments use the item).

The one write path covers the inventory structure; a server's SSH settings and connection-test results are written by
the operations code (servers/operations_store.py, operations_api.py)."""

from uuid import UUID

import psycopg

from knowledge_admin.db import audit
from knowledge_admin.inventory import model, rules
from knowledge_admin.inventory.rules import Invalid


class NotFound(LookupError):
    pass


class Conflict(Exception):
    pass


class Stale(Conflict):
    """The item changed since the caller read it (the revision sent is not the current one)."""


# What a reader gets: public columns only (no SSH credentials); a server with the environments it is in.
SERVER = """SELECT x.id,x.project_id,x.name,x.hostname,x.description,x.tags,x.status,x.revision,x.created_at,x.updated_at,
    x.port AS ssh_port,x.username AS ssh_username,x.enabled AS access_enabled,x.allow_sudo,x.ssh_allowed,
    ARRAY(SELECT m.environment_id FROM ka.environment_servers m WHERE m.server_id=x.id ORDER BY m.environment_id)
    AS environment_ids
    FROM ka.servers x"""
SERVICE = """SELECT x.id,x.project_id,x.name,x.description,x.owner,x.service_type::text AS service_type,
    x.service_role::text AS service_role,x.address,x.status,x.revision,x.created_by,x.created_at,x.updated_at
    FROM ka.services x"""
ENVIRONMENT = """SELECT x.id,x.project_id,x.name,x.description,x.status,x.revision,x.created_by,x.created_at,x.updated_at
    FROM ka.environments x"""
DEPLOYMENT = """SELECT x.id,x.project_id,x.service_id,x.server_id,x.environment_id,x.runtime,x.runtime_name,x.port,
    x.log_location,x.deployed_version,x.status,x.revision FROM ka.service_deployments x"""
CONNECTION = 'SELECT x.* FROM ka.connections x'
RELATION = 'SELECT x.* FROM ka.service_relations x'
CODE_DEPENDENCY = 'SELECT x.* FROM ka.code_project_dependencies x'

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
    'code_dependency': {
        'table': 'ka.code_project_dependencies',
        'read': CODE_DEPENDENCY,
        'rule': rules.code_dependency,
        'fields': rules.CODE_DEPENDENCY_FIELDS,
        'duplicate': 'This dependency already exists',  # _references names the code project first
    },
}
AUTHORED = ('connection', 'relation', 'code_dependency')  # kinds that record who created them
STATUSES = ('draft', 'confirmed')


# Reads


def list_servers(conn, project_id: UUID, environment_id: UUID | None = None) -> list[dict]:
    """Servers, or the servers in the environment; each with environment_ids, the environments it is in."""
    return conn.execute(
        f"""{SERVER} WHERE x.project_id=%(p)s AND (%(e)s::uuid IS NULL OR EXISTS
        (SELECT 1 FROM ka.environment_servers m WHERE m.server_id=x.id AND m.environment_id=%(e)s))
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
    """Environments, or the environments the server is in."""
    return conn.execute(
        f"""{ENVIRONMENT} WHERE x.project_id=%(p)s AND (%(s)s::uuid IS NULL OR EXISTS
        (SELECT 1 FROM ka.environment_servers m WHERE m.environment_id=x.id AND m.server_id=%(s)s))
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
    """Connections, or those from or to the deployment, or from or to any deployment of the service (or to the service
    itself, an external service)."""
    return conn.execute(
        f"""{CONNECTION} WHERE x.project_id=%(p)s
        AND (%(d)s::uuid IS NULL OR %(d)s IN (x.from_deployment_id,x.to_deployment_id))
        AND (%(s)s::uuid IS NULL OR x.to_service_id=%(s)s OR EXISTS (SELECT 1 FROM ka.service_deployments d
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


def list_code_dependencies(conn, project_id: UUID) -> list[dict]:
    return conn.execute(f'{CODE_DEPENDENCY} WHERE x.project_id=%s ORDER BY x.created_at,x.id', (project_id,)).fetchall()


def code_project_dependencies(conn, project_id: UUID, code_project_id: UUID) -> dict:
    """What the code project depends on and what uses it, archived code projects left out: {depends_on: [...],
    used_by: {code_projects: [...], services: [...]}}. A dependency is {id, revision, status, description,
    code_project: {id, name}} (the other end); used_by.services are the services built from the code projects that
    depend on it (direct dependents only), each {id, name, status, via: [{id, name, status}]}, where a via's status is
    that of the built_from link."""
    rows = conn.execute(
        """SELECT d.id,d.revision,d.status,d.description,d.from_code_project_id=%(c)s AS outgoing,r.id AS other_id,
        r.name AS other_name FROM ka.code_project_dependencies d
        JOIN ka.repositories r ON r.id=CASE WHEN d.from_code_project_id=%(c)s THEN d.to_code_project_id
            ELSE d.from_code_project_id END
        WHERE d.project_id=%(p)s AND %(c)s IN (d.from_code_project_id,d.to_code_project_id) AND r.archived_at IS NULL
        ORDER BY lower(r.name),r.id""",
        {'p': project_id, 'c': code_project_id},
    ).fetchall()

    def shown(row: dict) -> dict:
        fields = ('id', 'revision', 'status', 'description')
        return {**{f: row[f] for f in fields}, 'code_project': {'id': row['other_id'], 'name': row['other_name']}}

    dependents = [row for row in rows if not row['outgoing']]
    services: dict = {}
    for row in conn.execute(
        """SELECT s.id,s.name,s.status,l.status AS via_status,r.id AS via_id,r.name AS via_name FROM ka.service_relations l
        JOIN ka.services s ON s.id=l.from_service_id JOIN ka.repositories r ON r.id=l.to_code_project_id
        WHERE l.kind='built_from' AND l.to_code_project_id=ANY(%s) ORDER BY lower(s.name),s.id,lower(r.name),r.id""",
        ([row['other_id'] for row in dependents],),
    ):
        service = services.setdefault(
            row['id'], {'id': row['id'], 'name': row['name'], 'status': row['status'], 'via': []}
        )
        service['via'].append({'id': row['via_id'], 'name': row['via_name'], 'status': row['via_status']})
    return {
        'depends_on': [shown(row) for row in rows if row['outgoing']],
        'used_by': {'code_projects': [shown(row) for row in dependents], 'services': list(services.values())},
    }


def _project_rows(conn, project_id: UUID) -> dict:
    """The project's servers, services, deployments (with their environment's name), connections, relations, code
    projects (archived ones too, with archived_at) and memberships {environment_id, server_id}."""
    return {
        'servers': list_servers(conn, project_id),
        'services': list_services(conn, project_id),
        'deployments': conn.execute(
            f"""SELECT d.*,e.name AS environment_name FROM ({DEPLOYMENT} WHERE x.project_id=%s) d
            JOIN ka.environments e ON e.id=d.environment_id ORDER BY d.runtime_name,d.id""",
            (project_id,),
        ).fetchall(),
        'connections': list_connections(conn, project_id),
        'relations': list_relations(conn, project_id),
        'code_projects': conn.execute(
            """SELECT id,name,description,archived_at FROM ka.repositories WHERE project_id=%s
            ORDER BY lower(name),id""",
            (project_id,),
        ).fetchall(),
        'memberships': conn.execute(
            'SELECT environment_id,server_id,status FROM ka.environment_servers WHERE project_id=%s', (project_id,)
        ).fetchall(),
    }


def load_model_rows(conn, project_id: UUID, environment_id: UUID) -> dict:
    """The rows model.build_model takes: the environment (NotFound when it is not in the project) and the project's
    servers, services, deployments, connections, relations, code projects and memberships (each with its status)."""
    return {'environment': get(conn, project_id, 'environment', environment_id), **_project_rows(conn, project_id)}


def load_project_models(conn, project_id: UUID, drafts: bool = True) -> dict:
    """model.build_project over the project's rows ({models, unplaced}), plus its code projects that are not archived
    {id, name, description} and the live documents linked to each entry: {(entry kind, entry id): [{id, slug, title}]}
    (links to a deployment left out), and the code project dependencies (code_dependencies: {id, from_code_project_id,
    to_code_project_id, status}; an end may be archived). drafts=False leaves every draft out (a confirmed entry never
    needs a draft, so what is left is whole). One consistent read when the caller's transaction is REPEATABLE READ."""
    rows = _project_rows(conn, project_id)
    environments = list_environments(conn, project_id)
    dependencies = [
        {key: row[key] for key in ('id', 'from_code_project_id', 'to_code_project_id', 'status')}
        for row in list_code_dependencies(conn, project_id)
        if drafts or row['status'] == 'confirmed'
    ]
    if not drafts:
        environments = [row for row in environments if row['status'] == 'confirmed']
        rows = {
            key: [row for row in items if row.get('status', 'confirmed') == 'confirmed'] for key, items in rows.items()
        }
    documents: dict[tuple, list[dict]] = {}
    for row in conn.execute(
        """SELECT r.resource_type AS entry_kind,COALESCE(r.server_id,r.service_id,r.environment_id,r.code_project_id)
            AS entry_id,d.id,d.slug,d.title FROM ka.document_resources r JOIN ka.documents d ON d.id=r.document_id
            WHERE d.project_id=%s AND NOT d.is_draft AND r.removed_at IS NULL AND r.deployment_id IS NULL
            ORDER BY lower(d.title),d.id""",
        (project_id,),
    ):
        documents.setdefault((row['entry_kind'], row['entry_id']), []).append(
            {'id': row['id'], 'slug': row['slug'], 'title': row['title']}
        )
    return {
        **model.build_project(environments, **rows),
        'code_projects': [
            {'id': c['id'], 'name': c['name'], 'description': c['description']}
            for c in rows['code_projects']
            if c['archived_at'] is None
        ],
        'documents': documents,
        'code_dependencies': dependencies,
    }


def created_in_session(conn, project_id: UUID, session_id: UUID) -> set[tuple[str, str]]:
    """(kind, id) of the environments, servers, services, deployments, connections, relations and code dependencies an
    agent session created (the audit's '<kind>.created' events with that session_id)."""
    rows = conn.execute(
        """SELECT resource_type,resource_id FROM ka.audit_events WHERE project_id=%s AND details->>'session_id'=%s
        AND action IN ('environment.created','server.created','service.created','deployment.created',
        'connection.created','relation.created','code_dependency.created')""",
        (project_id, str(session_id)),
    ).fetchall()
    return {(row['resource_type'], str(row['resource_id'])) for row in rows}


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
        raise Stale(f'This {kind} changed since you read it (now revision {row["revision"]}). Read it again and retry')
    return row


def _require(conn, table: str, project_id: UUID, item_id: UUID | None, label: str) -> dict | None:
    if item_id is None:
        return None
    row = conn.execute(f'SELECT * FROM {table} WHERE id=%s AND project_id=%s', (item_id, project_id)).fetchone()
    if row is None:
        raise Invalid(f'Choose an existing {label} in this project')
    return row


def _references(conn, project_id: UUID, kind: str, item: dict, saved: dict | None = None) -> dict:
    """Check the item's references exist in the project; returns extra columns to write. A relation cannot newly point
    at an archived code project (one it already points at stays, so its description can still change)."""
    if kind == 'deployment':
        _require(conn, 'ka.services', project_id, item['service_id'], 'service')
        _require(conn, 'ka.servers', project_id, item['server_id'], 'server')
        _require(conn, 'ka.environments', project_id, item['environment_id'], 'environment')
        return {}
    if kind == 'connection':
        _require(conn, 'ka.service_deployments', project_id, item['from_deployment_id'], 'deployment to connect from')
        target = _require(
            conn, 'ka.service_deployments', project_id, item['to_deployment_id'], 'deployment to connect to'
        )
        service = _require(conn, 'ka.services', project_id, item['to_service_id'], 'service to connect to')
        if service is not None and (refused := rules.check_target_service(service['service_type'])):
            raise Invalid(refused)
        if target is not None and item['port'] is None:
            return {'port': target['port']}
    if kind == 'relation':
        _require(conn, 'ka.services', project_id, item['from_service_id'], 'service')
        _require(conn, 'ka.services', project_id, item['to_service_id'], 'service to relate to')
        code_project = _require(conn, 'ka.repositories', project_id, item['to_code_project_id'], 'code project')
        kept = saved is not None and saved['to_code_project_id'] == item['to_code_project_id']
        if code_project is not None and code_project['archived_at'] is not None and not kept:
            raise Invalid('Choose a code project that is not archived')
    if kind == 'code_dependency':
        _code_dependency_ends(conn, project_id, item, saved)
    return {}


def _code_dependency_ends(conn, project_id: UUID, item: dict, saved: dict | None) -> None:
    """Both code projects exist in the project and are not archived (an end it already has stays); the pair is new
    (Conflict naming the code project depended on; the project lock makes the check safe)."""
    for field, label in (('from_code_project_id', 'code project'), ('to_code_project_id', 'code project to depend on')):
        code_project = _require(conn, 'ka.repositories', project_id, item[field], label)
        kept = saved is not None and saved[field] == item[field]
        if code_project['archived_at'] is not None and not kept:
            raise Invalid('Choose a code project that is not archived')
    found = conn.execute(
        """SELECT r.name FROM ka.code_project_dependencies d JOIN ka.repositories r ON r.id=d.to_code_project_id
        WHERE d.from_code_project_id=%s AND d.to_code_project_id=%s AND d.id IS DISTINCT FROM %s""",
        (item['from_code_project_id'], item['to_code_project_id'], None if saved is None else saved['id']),
    ).fetchone()
    if found is not None:
        raise Conflict(rules.duplicate_dependency(found['name']))


def _write(conn, kind: str, sql: str, params: list) -> UUID:
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


def _check_status(status: str) -> None:
    if status not in STATUSES:
        raise Invalid(f'status must be one of: {", ".join(STATUSES)}')


def _create(conn, project_id: UUID, kind: str, values: dict, author: dict, status: str) -> dict:
    """A person's item is confirmed (with the drafts it needs); an agent's is a draft."""
    _check_status(status)
    lock_project(conn, project_id)
    item = KINDS[kind]['rule'](values)
    columns = {**item, **_references(conn, project_id, kind, item), 'status': status}
    if kind == 'server':
        columns['enabled'] = False  # SSH access is set up separately
    if kind in AUTHORED:
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
    _audit(conn, project_id, author, f'{kind}.created', kind, item_id, {'revision': 1, 'status': status, **item})
    if kind == 'deployment':
        _add_member(conn, project_id, item['environment_id'], item['server_id'], author, status)
    if status == 'confirmed':
        _confirm_needs(conn, project_id, kind, item_id, author)
    return _get(conn, kind, item_id)


def _update(
    conn, project_id: UUID, kind: str, item_id: UUID, revision: int, changes: dict, author: dict, status: str
) -> dict:
    """Changes are merged into the saved item; the whole item is validated again. The item keeps its status: a person
    (status 'confirmed') may change a draft or a confirmed item, an agent (status 'draft') only a draft."""
    _check_status(status)
    lock_project(conn, project_id)
    row = _locked(conn, kind, project_id, item_id, revision)
    if status == 'draft' and row['status'] == 'confirmed':
        raise Invalid(rules.confirmed_refusal(kind))
    saved = {field: row[field] for field in KINDS[kind]['fields']}
    if kind == 'connection' and 'to_deployment_id' in changes and 'port' not in changes:
        saved['port'] = None  # a new target brings its own port
    item = KINDS[kind]['rule']({**saved, **changes})
    if kind == 'service':
        targeted = conn.execute('SELECT 1 FROM ka.connections WHERE to_service_id=%s LIMIT 1', (item_id,)).fetchone()
        if refused := rules.check_type_change(saved['service_type'], item['service_type'], targeted is not None):
            raise Invalid(refused)
    columns = {**item, **_references(conn, project_id, kind, item, row)}
    sets = ','.join(f'{name}=%s' for name in columns)
    _write(
        conn,
        kind,
        f'UPDATE {KINDS[kind]["table"]} SET {sets},revision=revision+1 WHERE id=%s RETURNING id',
        [*columns.values(), item_id],
    )
    _audit(conn, project_id, author, f'{kind}.updated', kind, item_id, {'revision': revision + 1, **item})
    if kind == 'deployment':
        _add_member(conn, project_id, item['environment_id'], item['server_id'], author, row['status'])
    if row['status'] == 'confirmed':
        _confirm_needs(conn, project_id, kind, item_id, author)
    return _get(conn, kind, item_id)


def _refuse_with_jobs(conn, server_id: UUID) -> None:
    if conn.execute('SELECT 1 FROM ka.jobs WHERE server_id=%s LIMIT 1', (server_id,)).fetchone():
        raise Conflict('This server has connection-test jobs or history and cannot be deleted')


def _delete_own_key(conn, credential_id: UUID | None) -> None:
    """A deleted server's own SSH key, unless another server uses it."""
    conn.execute(
        """DELETE FROM ka.credentials c WHERE id=%s AND kind='ssh_private_key'
        AND NOT EXISTS (SELECT 1 FROM ka.servers s WHERE s.credential_id=c.id)""",
        (credential_id,),
    )


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
    if kind == 'server':
        _refuse_with_jobs(conn, item_id)
    if kind == 'deployment':
        details['connection_ids'] = [
            r['id']
            for r in conn.execute(
                """DELETE FROM ka.connections WHERE from_deployment_id=%s OR to_deployment_id=%s
                RETURNING id""",
                (item_id, item_id),
            )
        ]
    if kind == 'service':  # an external service's connections go with it
        details['connection_ids'] = [
            r['id'] for r in conn.execute('DELETE FROM ka.connections WHERE to_service_id=%s RETURNING id', (item_id,))
        ]
    conn.execute(f'DELETE FROM {KINDS[kind]["table"]} WHERE id=%s', (item_id,))
    if kind == 'server':
        _delete_own_key(conn, row['credential_id'])
    _audit(conn, project_id, author, f'{kind}.deleted', kind, item_id, details)
    return {'id': item_id}


def _add_member(conn, project_id: UUID, environment_id: UUID, server_id: UUID, author: dict, status: str) -> bool:
    """Adds the membership with this status when it is missing (a draft one is confirmed by _confirm_needs)."""
    added = conn.execute(
        """INSERT INTO ka.environment_servers(project_id,environment_id,server_id,status) VALUES (%s,%s,%s,%s)
        ON CONFLICT DO NOTHING RETURNING server_id""",
        (project_id, environment_id, server_id, status),
    ).fetchone()
    if added is not None:
        details = {'server_id': server_id, 'status': status}
        _audit(conn, project_id, author, 'membership.added', 'environment', environment_id, details)
    return added is not None


# Drafts: confirm and discard (rules.confirm_reach and rules.discard_reach say what they reach)

TABLES = {kind: spec['table'] for kind, spec in KINDS.items()}
DISCARD_ORDER = (
    'code_dependency',
    'connection',
    'relation',
    'deployment',
    'membership',
    'service',
    'server',
    'environment',
)


def _reach_rows(conn, project_id: UUID) -> dict:
    """The project's entries as rules' draft functions take them: by kind, then id (a membership by
    rules.membership_id), each with its status, name and the ids it refers to."""

    def by_id(sql: str) -> dict:
        return {row['id']: row for row in conn.execute(sql, (project_id,))}

    rows = {
        kind: by_id(f'SELECT id,name,status FROM {TABLES[kind]} WHERE project_id=%s')
        for kind in ('environment', 'server', 'service')
    }
    rows['deployment'] = by_id(
        'SELECT id,service_id,server_id,environment_id,status FROM ka.service_deployments WHERE project_id=%s'
    )
    rows['connection'] = by_id(
        """SELECT id,from_deployment_id,to_deployment_id,to_service_id,to_host,status FROM ka.connections
        WHERE project_id=%s"""
    )
    rows['relation'] = by_id(
        """SELECT id,from_service_id,kind,to_service_id,to_code_project_id,status FROM ka.service_relations
        WHERE project_id=%s"""
    )
    rows['code_dependency'] = by_id(
        'SELECT id,from_code_project_id,to_code_project_id,status FROM ka.code_project_dependencies WHERE project_id=%s'
    )
    rows['code_project'] = by_id('SELECT id,name FROM ka.repositories WHERE project_id=%s')
    rows['membership'] = {
        rules.membership_id(row['environment_id'], row['server_id']): row
        for row in conn.execute(
            'SELECT environment_id,server_id,status FROM ka.environment_servers WHERE project_id=%s', (project_id,)
        )
    }
    return rows


def _entry_key(kind: str, item_id):
    """The entry's key in the reach rows: its id, or for a membership '<environment id>:<server id>'."""
    if kind not in rules.DRAFT_KINDS:
        raise Invalid(f'kind must be one of: {", ".join(rules.DRAFT_KINDS)}')
    try:
        if kind == 'membership':
            environment_id, server_id = str(item_id).split(':')
            return rules.membership_id(UUID(environment_id), UUID(server_id))
        return item_id if isinstance(item_id, UUID) else UUID(str(item_id))
    except ValueError:
        raise Invalid('Choose an entry by its id (a membership by <environment id>:<server id>)') from None


def _lock_entry(conn, project_id: UUID, kind: str, key, revision: int | None) -> None:
    """The named entry, locked: NotFound when it is not in the project, Stale when the revision is not the current
    one. A membership has no revision."""
    if kind == 'membership':
        environment_id, server_id = key.split(':')
        found = conn.execute(
            """SELECT 1 FROM ka.environment_servers WHERE project_id=%s AND environment_id=%s AND server_id=%s
            FOR UPDATE""",
            (project_id, environment_id, server_id),
        ).fetchone()
        if found is None:
            raise NotFound('This server is not in this environment')
        return
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise Invalid('revision is required: the revision you read')
    _locked(conn, kind, project_id, key, revision)


def _listed(entries: list[tuple], rows: dict) -> list[dict]:
    return [{'kind': kind, 'id': key, 'name': rules.entry_name(kind, key, rows)} for kind, key in entries]


def _confirm_entries(conn, project_id: UUID, entries: list[tuple], rows: dict, author: dict) -> list[dict]:
    listed = _listed(entries, rows)
    for (kind, key), entry in zip(entries, listed, strict=True):
        if kind == 'membership':
            row = rows['membership'][key]
            conn.execute(
                "UPDATE ka.environment_servers SET status='confirmed' WHERE environment_id=%s AND server_id=%s",
                (row['environment_id'], row['server_id']),
            )
            details = {'server_id': row['server_id'], 'name': entry['name']}
            _audit(conn, project_id, author, 'membership.confirmed', 'environment', row['environment_id'], details)
            continue
        revision = conn.execute(
            f"UPDATE {TABLES[kind]} SET status='confirmed',revision=revision+1 WHERE id=%s RETURNING revision",
            (key,),
        ).fetchone()['revision']
        _audit(conn, project_id, author, f'{kind}.confirmed', kind, key, {'revision': revision, 'name': entry['name']})
    return listed


def _confirm_needs(conn, project_id: UUID, kind: str, item_id: UUID, author: dict) -> None:
    """A confirmed item never needs a draft: the drafts it needs are confirmed with it."""
    if kind in ('deployment', 'connection', 'relation'):
        rows = _reach_rows(conn, project_id)
        _confirm_entries(conn, project_id, rules.needs(kind, item_id, rows), rows, author)


def confirm(conn, project_id: UUID, kind: str, item_id, revision: int | None, author: dict) -> list[dict]:
    """Confirms the draft and every draft it needs; returns them [{kind, id, name}], the draft first. Invalid when it
    is confirmed already; NotFound, Stale: nothing changes."""
    key = _entry_key(kind, item_id)
    lock_project(conn, project_id)
    _lock_entry(conn, project_id, kind, key, revision)
    rows = _reach_rows(conn, project_id)
    return _confirm_entries(conn, project_id, rules.confirm_reach(kind, key, rows), rows, author)


def discard(conn, project_id: UUID, kind: str, item_id, revision: int | None, author: dict) -> list[dict]:
    """Deletes the draft and every draft that needs it; returns them [{kind, id, name}], the draft first. Invalid when
    it is confirmed or a confirmed entry needs it; NotFound, Stale: nothing changes."""
    key = _entry_key(kind, item_id)
    lock_project(conn, project_id)
    _lock_entry(conn, project_id, kind, key, revision)
    rows = _reach_rows(conn, project_id)
    entries = rules.discard_reach(kind, key, rows)
    listed = _listed(entries, rows)
    for entry_kind in DISCARD_ORDER:
        for entry in listed:
            if entry['kind'] != entry_kind:
                continue
            if entry_kind == 'membership':
                row = rows['membership'][entry['id']]
                conn.execute(
                    'DELETE FROM ka.environment_servers WHERE environment_id=%s AND server_id=%s',
                    (row['environment_id'], row['server_id']),
                )
                details = {'server_id': row['server_id'], 'name': entry['name']}
                _audit(conn, project_id, author, 'membership.discarded', 'environment', row['environment_id'], details)
                continue
            if entry_kind == 'server':
                _refuse_with_jobs(conn, entry['id'])
            deleted = conn.execute(f'DELETE FROM {TABLES[entry_kind]} WHERE id=%s RETURNING *', (entry['id'],))
            if entry_kind == 'server':
                _delete_own_key(conn, deleted.fetchone()['credential_id'])
            details = {'name': entry['name']}
            _audit(conn, project_id, author, f'{entry_kind}.discarded', entry_kind, entry['id'], details)
    return listed


# Writes


def add_server_to_environment(
    conn, project_id: UUID, environment_id: UUID, server_id: UUID, author: dict, status: str = 'confirmed'
) -> bool:
    """Puts the server in the environment; True when added (or, by a person, a draft membership confirmed), False when
    it was a member already (nothing written). A person's membership is confirmed with the drafts it needs."""
    _check_status(status)
    lock_project(conn, project_id)
    get(conn, project_id, 'environment', environment_id)
    get(conn, project_id, 'server', server_id)
    added = _add_member(conn, project_id, environment_id, server_id, author, status)
    if status == 'draft':
        return added
    rows = _reach_rows(conn, project_id)
    key = rules.membership_id(environment_id, server_id)
    was_draft = rows['membership'][key]['status'] == 'draft'
    entries = rules.confirm_reach('membership', key, rows) if was_draft else rules.needs('membership', key, rows)
    _confirm_entries(conn, project_id, entries, rows, author)
    return added or was_draft


def remove_server_from_environment(conn, project_id: UUID, environment_id: UUID, server_id: UUID, author: dict) -> None:
    """Refused (Invalid) while the server has deployments in the environment; NotFound when it is not a member."""
    lock_project(conn, project_id)
    get(conn, project_id, 'environment', environment_id)
    get(conn, project_id, 'server', server_id)
    if conn.execute(
        'SELECT 1 FROM ka.service_deployments WHERE environment_id=%s AND server_id=%s LIMIT 1',
        (environment_id, server_id),
    ).fetchone():
        raise Invalid('Remove the deployments on this server in this environment first.')
    removed = conn.execute(
        'DELETE FROM ka.environment_servers WHERE environment_id=%s AND server_id=%s RETURNING server_id',
        (environment_id, server_id),
    ).fetchone()
    if removed is None:
        raise NotFound('This server is not in this environment')
    _audit(conn, project_id, author, 'membership.removed', 'environment', environment_id, {'server_id': server_id})


def create_server(conn, project_id: UUID, values: dict, author: dict, status: str = 'confirmed') -> dict:
    return _create(conn, project_id, 'server', values, author, status)


def update_server(
    conn, project_id: UUID, server_id: UUID, revision: int, changes: dict, author: dict, status: str = 'confirmed'
) -> dict:
    return _update(conn, project_id, 'server', server_id, revision, changes, author, status)


def delete_server(conn, project_id: UUID, server_id: UUID, revision: int, author: dict) -> dict:
    """Refused while deployments use it or it has connection-test jobs; its own SSH key goes with it."""
    return _delete(conn, project_id, 'server', server_id, revision, author)


def create_service(conn, project_id: UUID, values: dict, author: dict, status: str = 'confirmed') -> dict:
    return _create(conn, project_id, 'service', values, author, status)


def update_service(
    conn, project_id: UUID, service_id: UUID, revision: int, changes: dict, author: dict, status: str = 'confirmed'
) -> dict:
    return _update(conn, project_id, 'service', service_id, revision, changes, author, status)


def delete_service(conn, project_id: UUID, service_id: UUID, revision: int, author: dict) -> dict:
    """Refused while deployments use it; its relations and the connections to it (an external service) go with it."""
    return _delete(conn, project_id, 'service', service_id, revision, author)


def create_environment(conn, project_id: UUID, values: dict, author: dict, status: str = 'confirmed') -> dict:
    return _create(conn, project_id, 'environment', values, author, status)


def update_environment(
    conn, project_id: UUID, environment_id: UUID, revision: int, changes: dict, author: dict, status: str = 'confirmed'
) -> dict:
    return _update(conn, project_id, 'environment', environment_id, revision, changes, author, status)


def delete_environment(conn, project_id: UUID, environment_id: UUID, revision: int, author: dict) -> dict:
    """Refused while deployments are in it."""
    return _delete(conn, project_id, 'environment', environment_id, revision, author)


def create_deployment(conn, project_id: UUID, values: dict, author: dict, status: str = 'confirmed') -> dict:
    """Puts the server in the environment when it is not there yet."""
    return _create(conn, project_id, 'deployment', values, author, status)


def update_deployment(
    conn, project_id: UUID, deployment_id: UUID, revision: int, changes: dict, author: dict, status: str = 'confirmed'
) -> dict:
    """Moved to another environment or server: that server is put in that environment when it is not there yet."""
    return _update(conn, project_id, 'deployment', deployment_id, revision, changes, author, status)


def delete_deployment(conn, project_id: UUID, deployment_id: UUID, revision: int, author: dict) -> dict:
    """Removes its connections (both directions) too."""
    return _delete(conn, project_id, 'deployment', deployment_id, revision, author)


def create_connection(conn, project_id: UUID, values: dict, author: dict, status: str = 'confirmed') -> dict:
    """Without a port, a connection to a deployment takes that deployment's port."""
    return _create(conn, project_id, 'connection', values, author, status)


def update_connection(
    conn, project_id: UUID, connection_id: UUID, revision: int, changes: dict, author: dict, status: str = 'confirmed'
) -> dict:
    return _update(conn, project_id, 'connection', connection_id, revision, changes, author, status)


def delete_connection(conn, project_id: UUID, connection_id: UUID, revision: int, author: dict) -> dict:
    return _delete(conn, project_id, 'connection', connection_id, revision, author)


def create_relation(conn, project_id: UUID, values: dict, author: dict, status: str = 'confirmed') -> dict:
    return _create(conn, project_id, 'relation', values, author, status)


def update_relation(
    conn, project_id: UUID, relation_id: UUID, revision: int, changes: dict, author: dict, status: str = 'confirmed'
) -> dict:
    return _update(conn, project_id, 'relation', relation_id, revision, changes, author, status)


def delete_relation(conn, project_id: UUID, relation_id: UUID, revision: int, author: dict) -> dict:
    return _delete(conn, project_id, 'relation', relation_id, revision, author)


def create_code_dependency(conn, project_id: UUID, values: dict, author: dict, status: str = 'confirmed') -> dict:
    """Refused (Conflict) when the code project already depends on that one."""
    return _create(conn, project_id, 'code_dependency', values, author, status)


def update_code_dependency(
    conn, project_id: UUID, dependency_id: UUID, revision: int, changes: dict, author: dict, status: str = 'confirmed'
) -> dict:
    return _update(conn, project_id, 'code_dependency', dependency_id, revision, changes, author, status)


def delete_code_dependency(conn, project_id: UUID, dependency_id: UUID, revision: int, author: dict) -> dict:
    return _delete(conn, project_id, 'code_dependency', dependency_id, revision, author)
