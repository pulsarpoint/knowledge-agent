"""Agent tools that read and change the inventory, thin over inventory/store.py (the one write path) and
inventory/model.py. Replies are JSON; a refusal is {"refused": text} (a store error's message is the refusal).

Every agent write is a draft (store status 'draft'): a new entry is created as a draft, an existing draft is updated,
and a confirmed entry is refused (rules.confirmed_refusal); only a person confirms or discards a draft. Reads return
every entry with its status.

Access (each tool's pure check_<tool>): an item is usable when it is in the run's access object, or this session
created it (store.created_in_session); a deployment when its server, service or environment is usable (for a write:
all three, as for a new one); a connection when its from-deployment is; a relation when its from-service is. Reads need
the item usable. Writes need a session (its person, agent and session are the author) and every item they name usable
(a save_server environment_id and a save_connection to_service_id included); a new environment, server or service
needs only some inventory resource (an environment, a server or a service) in the access. save_inventory_draft names entries instead of ids: resolve_draft (pure) turns
its payload into a plan over the rows the run may name (_recorded), and apply_draft writes the plan in one transaction.
The write tools are offered only in a session turn (tools/catalog.INVENTORY_WRITES): never to a subagent or an
expert."""

import asyncio
import json
from dataclasses import dataclass
from functools import partial
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from knowledge_admin import access as access_rules
from knowledge_admin.capabilities.inventory import UNAVAILABLE, connect, project_of
from knowledge_admin.inventory import model, rules, store
from knowledge_admin.inventory.rules import RELATION_KINDS, SERVICE_ROLES, SERVICE_TYPES, Invalid
from knowledge_admin.tools.registry import tool

MAX_ITEMS = 100
NO_SESSION = 'Inventory changes need a session.'
NO_REVISION = 'revision is required: the revision you read'
EITHER = 'Give either {0} or {1}.'
INVENTORY_TYPES = ('environment', 'server', 'service')  # any of them in the access lets a run add new drafts
DRAFTED = (
    'These entries are drafts: a person confirms or discards them in the environment workspace. Used entries were '
    'named, not changed (a confirmed entry is changed only by people).'
)
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
    'environment': (store.create_environment, store.update_environment, store.delete_environment),
    'server': (store.create_server, store.update_server, store.delete_server),
    'service': (store.create_service, store.update_service, store.delete_service),
    'deployment': (store.create_deployment, store.update_deployment, store.delete_deployment),
    'connection': (store.create_connection, store.update_connection, store.delete_connection),
    'relation': (store.create_relation, store.update_relation, store.delete_relation),
}
Kind = Literal['environment', 'server', 'service', 'deployment', 'connection', 'relation']


def refuse(kind: str) -> str:
    return access_rules.NOT_AVAILABLE.format(type=WORDS[kind])


def usable(access: dict, kind: str, item_id, created=frozenset(), rows=None, strict: bool = False) -> bool:
    """In the access, created by this session, or (deployment, connection, relation) reached through its row: a
    deployment through any of its server, service and environment, or with strict (writes) through all three."""
    if (kind, str(item_id)) in created:
        return True
    if kind in access_rules.TYPES:
        return access_rules.can_use(access, kind, item_id)
    row = (rows or {}).get((kind, str(item_id)))
    if row is None:
        return False
    if kind == 'deployment':
        ends = [usable(access, end, row[f'{end}_id'], created, rows) for end in ('server', 'service', 'environment')]
        return all(ends) if strict else any(ends)
    if kind == 'connection':
        return usable(access, 'deployment', row['from_deployment_id'], created, rows, strict)
    return usable(access, 'service', row['from_service_id'], created, rows)  # a relation


def refs(kind: str | None, item_id, values: dict) -> list[tuple[str, Any]]:
    """The items a call names: the item itself (kind, id) and every reference among its values."""
    found = [] if item_id is None else [(kind, item_id)]
    return found + [(REFERENCES[k], v) for k, v in values.items() if k in REFERENCES and v is not None]


def first_refused(
    access: dict, named: list[tuple[str, Any]], created=frozenset(), rows=None, strict: bool = False
) -> str | None:
    for kind, item_id in named:
        if not usable(access, kind, item_id, created, rows, strict):
            return refuse(kind)
    return None


def _write(access: dict, kind: str, id, values: dict, created, rows, remove: bool = False) -> str | None:
    """A new environment, server or service needs an inventory resource (environment, server or service) in the
    access; every item named must be usable; an existing item must be a draft (a confirmed server may still join an
    environment: a draft membership)."""
    if id is None and kind in INVENTORY_TYPES and not may_create(access):
        return access_rules.refuse_none(*INVENTORY_TYPES)
    if refusal := first_refused(access, refs(kind, id, values), created, rows, strict=True):
        return refusal
    row = None if id is None else (rows or {}).get((kind, str(id)))
    joins_only = kind == 'server' and not remove and set(values) == {'environment_id'}
    if row is not None and row.get('status') == 'confirmed' and not joins_only:
        return rules.confirmed_refusal(kind)
    return None


def may_create(access: dict) -> bool:
    return any(access_rules.has_any(access, kind) for kind in INVENTORY_TYPES)


def _either(access: dict, values: dict, created, rows) -> str | None:
    given = {k: v for k, v in values.items() if v is not None}
    if len(given) != 1:
        return EITHER.format(*values)
    return first_refused(access, refs(None, None, given), created, rows)


# The checks: pure, over the run's access, the items this session created and the rows of the items named (with their
# status).


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


def check_save_environment(
    access: dict, id=None, revision=None, created=frozenset(), rows=None, **values
) -> str | None:
    return _write(access, 'environment', id, values, created, rows)


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
    return _write(access, kind, id, {}, created, rows, remove=True)


def check_save_inventory_draft(access: dict, **_) -> str | None:
    """Always allowed here: the entries are named, not given by id, so resolve_draft decides over the rows the run may
    name (in its access or created by this session); a new entry needs an environment, a server or a service in the
    access."""
    return None


# save_inventory_draft: the payload's names → a plan (pure)


@dataclass(frozen=True)
class Ref:
    """An entry this call creates, by its step's key: apply_draft puts in its id."""

    key: tuple


SERVER_VALUES = {'hostname': 'hostname', 'description': 'description', 'tags': 'tags'}
SERVICE_VALUES = {f: f for f in ('service_type', 'service_role', 'description', 'owner', 'address')}
DEPLOYMENT_VALUES = {
    'runtime': 'runtime',
    'runtime_name': 'runtime_name',
    'port': 'port',
    'version': 'deployed_version',
    'log_location': 'log_location',
}
CONNECTION_VALUES = {'port': 'port', 'protocol': 'protocol', 'description': 'description'}
RECORDED = ('environments', 'servers', 'services', 'deployments', 'connections', 'relations', 'code_projects')


def _picked(entry: dict, fields: dict[str, str]) -> dict:
    """The entry's given fields (not None) under their store names."""
    return {stored: entry[given] for given, stored in fields.items() if entry.get(given) is not None}


class _Plan:
    """The steps of one save_inventory_draft call, built part by part (resolve_draft). A step: {kind, action (create,
    update, use; a membership: add), where ("<part>[<index>]"), name (as a person reads it), key (what a later step's
    Ref names), id and revision (a recorded entry), values (ids or Refs)}."""

    def __init__(self, recorded: dict):
        self.recorded = {part: recorded.get(part) or [] for part in RECORDED}
        self.may_create = bool(recorded.get('may_create'))
        self.steps: list[dict] = []
        self.named: dict[str, dict] = {'server': {}, 'service': {}}  # this call's, by lower-cased name
        self.environment: dict | None = None  # {ref, name}
        self.deployments: list[dict] = []  # this call's: {key, ref, service, server, server_name, runtime_name, port}
        self.members: set = set()  # the servers in the environment, or joining it in this call
        self.identities: set = set()  # the relations and connections this call names

    def _step(self, kind: str, action: str, where: str, name: str, key=None, row=None, values=None) -> dict:
        step = {
            'kind': kind,
            'action': action,
            'where': where,
            'name': name,
            'key': key if key is not None else (kind, len(self.steps)),
            'id': None if row is None else row['id'],
            'revision': None if row is None else row.get('revision'),
            'values': values or {},
        }
        self.steps.append(step)
        return step

    def _entry(self, kind: str, where: str, name: str, key, row, values: dict, changes: dict):
        """A recorded confirmed row is used as it is, a recorded draft gets the changes given (none: used), no row:
        created from values. Returns its id, or a Ref to the step that creates it."""
        if row is None:
            step = self._step(kind, 'create', where, name, key, values=values)
            return Ref(step['key'])
        action = 'update' if row['status'] == 'draft' and changes else 'use'
        self._step(kind, action, where, name, key, row, changes if action == 'update' else {})
        return row['id']

    def _new(self, where: str) -> None:
        if not self.may_create:
            raise Invalid(f'{where}: {access_rules.refuse_none(*INVENTORY_TYPES)}')

    def _once(self, where: str, name: str, identity: tuple) -> None:
        """Refuses a relation or connection this call already names."""
        if identity in self.identities:
            raise Invalid(f'{where}: {name} is listed twice.')
        self.identities.add(identity)

    def _recorded(self, part: str, name: str) -> dict | None:
        return next((row for row in self.recorded[part] if row['name'].lower() == name.lower()), None)

    def _name(self, kind: str, name: str, where: str) -> dict:
        """A server or service named: this call's first, then a recorded one. {ref, name, type (a service's)}."""
        if (found := self.named[kind].get(name.lower())) is not None:
            return found
        if (row := self._recorded(f'{kind}s', name)) is not None:
            return {'ref': row['id'], 'name': row['name'], 'type': row.get('service_type'), 'row': row}
        mine = [entry['name'] for entry in self.named[kind].values()]
        known = mine + [r['name'] for r in self.recorded[f'{kind}s'] if r['name'].lower() not in self.named[kind]]
        raise Invalid(f'{where}: no {kind} named {name}. Known {kind}s: {", ".join(known) or "none"}.')

    def set_environment(self, name: str | None) -> None:
        if name is None:
            return
        row = self._recorded('environments', name)
        if row is None:
            self._new('environment')
        ref = self._entry('environment', 'environment', row['name'] if row else name, None, row, {'name': name}, {})
        self.environment = {'ref': ref, 'name': row['name'] if row else name}
        self.members = {s['id'] for s in self.recorded['servers'] if ref in (s.get('environment_ids') or [])}

    def _join(self, where: str, server_ref, server_name: str) -> None:
        """A draft membership of the server in the environment, unless it is a member or joins in this call."""
        if server_ref in self.members:
            return
        self.members.add(server_ref)
        values = {'environment_id': self.environment['ref'], 'server_id': server_ref}
        self._step('membership', 'add', where, f'{server_name} in {self.environment["name"]}', values=values)

    def add(self, kind: str, where: str, entry: dict) -> None:
        """A server or service of the payload; a server joins the environment (a draft membership) unless it is in it."""
        name = entry['name']
        if name.lower() in self.named[kind]:
            raise Invalid(f'{where}: {name} is listed twice.')
        row = self._recorded(f'{kind}s', name)
        if row is None:
            self._new(where)
        changes = _picked(entry, SERVER_VALUES if kind == 'server' else SERVICE_VALUES)
        key = (kind, name.lower())
        ref = self._entry(kind, where, row['name'] if row else name, key, row, {'name': name, **changes}, changes)
        recorded_type = None if row is None else row.get('service_type')
        kept = row is not None and row['status'] == 'confirmed'
        service_type = recorded_type if kept else changes.get('service_type', recorded_type or 'unclassified')
        self.named[kind][name.lower()] = {'ref': ref, 'name': row['name'] if row else name, 'type': service_type}
        if kind == 'server' and self.environment is not None:
            self._join(where, ref, self.named[kind][name.lower()]['name'])

    def deployment(self, where: str, entry: dict) -> None:
        """Matched to a recorded deployment of the service on the server in the environment: the one with the
        runtime_name given, or without one the only one there (several: refused, naming them)."""
        if self.environment is None:
            raise Invalid(f'{where}: give the environment the deployments are in.')
        service = self._name('service', entry['service'], where)
        server = self._name('server', entry['server'], where)
        given_name = entry.get('runtime_name')
        changes = _picked(entry, DEPLOYMENT_VALUES)
        ends = {'service_id': service['ref'], 'server_id': server['ref'], 'environment_id': self.environment['ref']}
        there = [
            d
            for d in self.recorded['deployments']
            if (d['service_id'], d['server_id'], d['environment_id']) == tuple(ends.values())
        ]
        if given_name is not None:
            there = [d for d in there if d['runtime_name'] == given_name.strip()]
        if len(there) > 1:
            names = ', '.join(d['runtime_name'] or '""' for d in there)
            raise Invalid(
                f'{where}: service {service["name"]} has several deployments on server {server["name"]} ({names}); '
                'give runtime_name.'
            )
        row = there[0] if there else None
        runtime_name = row['runtime_name'] if row else (given_name or '').strip()
        key = ('deployment', row['id']) if row else ('deployment', service['ref'], server['ref'], runtime_name)
        if any(d['key'] == key for d in self.deployments):
            raise Invalid(f'{where}: {service["name"]} on {server["name"]} is listed twice.')
        self._join(where, server['ref'], server['name'])
        name = f'{service["name"]} on {server["name"]} in {self.environment["name"]}'
        same = {f: v for f, v in changes.items() if f != 'runtime_name'}  # the runtime name is how it was matched
        ref = self._entry('deployment', where, name, key, row, {**ends, **changes}, same)
        port = changes.get('port', None if row is None else row['port'])
        self.deployments.append(
            {'key': key, 'ref': ref, 'service': service['ref'], 'server': server['ref'], 'server_name': server['name']}
            | {'runtime_name': runtime_name, 'port': port}
        )

    def _candidates(self) -> list[dict]:
        """The deployments in the environment a connection may start or end at: this call's, then the recorded ones."""
        listed = {d['ref'] for d in self.deployments}
        server_names = {row['id']: row['name'] for row in self.recorded['servers']}
        recorded = [
            {
                'ref': d['id'],
                'service': d['service_id'],
                'server': d['server_id'],
                'server_name': server_names.get(d['server_id'], str(d['server_id'])),
                'runtime_name': d['runtime_name'],
                'port': d['port'],
            }
            for d in self.recorded['deployments']
            if d['environment_id'] == self.environment['ref'] and d['id'] not in listed
        ]
        return self.deployments + recorded

    def _end(self, where: str, entry: dict, side: str) -> tuple[dict, dict]:
        """The service named on this side ('from' or 'to') and its one deployment in the environment, narrowed by
        <side>_server and <side>_runtime_name when given; an ambiguity names the field that resolves it."""
        service = self._name('service', entry['from_' if side == 'from' else 'to'], where)
        server_name, runtime_name = entry.get(f'{side}_server'), entry.get(f'{side}_runtime_name')
        if self.environment is None:
            raise Invalid(f'{where}: give the environment the connections are in.')
        found = [c for c in self._candidates() if c['service'] == service['ref']]
        on = ''
        if server_name is not None:
            server = self._name('server', server_name, where)
            found = [c for c in found if c['server'] == server['ref']]
            on = f' on {server["name"]}'
        if runtime_name is not None:
            found = [c for c in found if c['runtime_name'] == runtime_name.strip()]
            on += f' with runtime_name {runtime_name}'
        if not found:
            raise Invalid(f'{where}: {service["name"]} has no deployment{on} in {self.environment["name"]}.')
        if len(found) == 1:
            return service, found[0]
        found = sorted(found, key=lambda c: c['server_name'].lower())
        servers = {c['server'] for c in found}
        if len(servers) == len(found):
            names = ', '.join(c['server_name'] for c in found)
            raise Invalid(f'{where}: {service["name"]} runs on {names}; give {side}_server.')
        if len(servers) == 1:
            names = ', '.join(c['runtime_name'] or '""' for c in found)
            raise Invalid(
                f'{where}: service {service["name"]} has several deployments on server {found[0]["server_name"]} '
                f'({names}); give {side}_runtime_name.'
            )
        names = ', '.join(f'{c["server_name"]}/{c["runtime_name"]}' for c in found)
        raise Invalid(f'{where}: {service["name"]} runs on {names}; give {side}_server and {side}_runtime_name.')

    def connection(self, where: str, entry: dict) -> None:
        to, host = entry.get('to'), entry.get('to_host')
        if to is not None and host is not None:
            raise Invalid(f'{where}: give to (a service) or to_host, not both.')
        if to is None and host is None:
            raise Invalid(f'{where}: give to (a service) or to_host.')
        source, start = self._end(where, entry, 'from')
        values: dict[str, Any] = {'from_deployment_id': start['ref']}
        target_port = None
        if host is not None:
            values['to_host'], end = host, host
        else:
            target = self._name('service', to, where)
            if target['type'] == 'external':
                values['to_service_id'] = target['ref']
            else:
                deployment = self._end(where, entry, 'to')[1]
                values['to_deployment_id'], target_port = deployment['ref'], deployment['port']
            end = target['name']
        values |= _picked(entry, CONNECTION_VALUES)
        port, protocol = values.get('port', target_port), values.get('protocol', 'tcp')
        to_host = (values.get('to_host') or '').lower().removesuffix('.')
        identity = (values['from_deployment_id'], values.get('to_deployment_id'), values.get('to_service_id'))
        name = f'{source["name"]} → {end}'
        self._once(where, name, ('connection', *identity, to_host, port, protocol))
        row = next(
            (
                r
                for r in self.recorded['connections']
                if (r['from_deployment_id'], r['to_deployment_id'], r['to_service_id']) == identity
                and (r['to_host'] or '') == to_host
                and (r['port'], r['protocol']) == (port, protocol)
            ),
            None,
        )
        changes = _picked(entry, {'description': 'description'})
        self._entry('connection', where, name, None, row, values, changes)

    def relation(self, where: str, entry: dict) -> None:
        source = self._name('service', entry['from_'], where)
        kind = entry['kind']
        values: dict[str, Any] = {'from_service_id': source['ref'], 'kind': kind}
        if kind == 'built_from':
            project = next(
                (p for p in self.recorded['code_projects'] if p['name'].lower() == entry['to'].lower()), None
            )
            if project is None:
                known = ', '.join(p['name'] for p in self.recorded['code_projects']) or 'none'
                raise Invalid(f'{where}: no code project named {entry["to"]}. Known code projects: {known}.')
            values['to_code_project_id'], end = project['id'], project['name']
        else:
            target = self._name('service', entry['to'], where)
            values['to_service_id'], end = target['ref'], target['name']
        identity = (source['ref'], kind, values.get('to_service_id'), values.get('to_code_project_id'))
        name = f'{source["name"]} {kind} {end}'
        self._once(where, name, ('relation', *identity))
        changes = _picked(entry, {'description': 'description'})
        row = next(
            (
                r
                for r in self.recorded['relations']
                if (r['from_service_id'], r['kind'], r['to_service_id'], r['to_code_project_id']) == identity
            ),
            None,
        )
        self._entry('relation', where, name, None, row, values | changes, changes)


def resolve_draft(payload: dict, recorded: dict) -> list[dict]:
    """The plan for a save_inventory_draft payload (the argument model's dump: parts None or lists of entries with
    every field, None when not given; a connection's and relation's from is from_). recorded: the rows the run may
    name, by part (environments, servers with environment_ids, services with service_type, deployments, connections,
    relations, code_projects {id, name}; each with id, status and revision) and may_create (an inventory resource in
    the access: new entries are allowed). Names are matched without case: this call's entries first, then the recorded
    ones. Raises Invalid "<part>[<index>]: <reason>" on the first entry it cannot resolve."""
    plan = _Plan(recorded)
    plan.set_environment(payload.get('environment'))
    for index, entry in enumerate(payload.get('servers') or []):
        plan.add('server', f'servers[{index}]', entry)
    services = payload.get('services') or []
    for index, entry in enumerate(services):
        plan.add('service', f'services[{index}]', entry)
    for index, entry in enumerate(services):
        for name in entry.get('part_of') or []:
            relation = {'from_': entry['name'], 'kind': 'part_of', 'to': name, 'description': None}
            plan.relation(f'services[{index}]', relation)
    for index, entry in enumerate(payload.get('deployments') or []):
        plan.deployment(f'deployments[{index}]', entry)
    for index, entry in enumerate(payload.get('connections') or []):
        plan.connection(f'connections[{index}]', entry)
    for index, entry in enumerate(payload.get('relations') or []):
        plan.relation(f'relations[{index}]', entry)
    return plan.steps


def apply_draft(conn, project: UUID, plan: list[dict], author: dict) -> dict:
    """Writes the plan's steps in order as drafts, in the caller's transaction; a store error is raised as Invalid
    "<where>: <message>" (the caller rolls back). Returns {created, updated, used: [{kind, name, id}]}."""
    ids: dict[tuple, Any] = {}
    out: dict[str, list] = {'created': [], 'updated': [], 'used': []}
    listed = {'create': 'created', 'update': 'updated', 'use': 'used'}
    for step in plan:
        kind = step['kind']
        values = {f: ids[v.key] if isinstance(v, Ref) else v for f, v in step['values'].items()}
        try:
            if kind == 'membership':
                environment_id, server_id = UUID(str(values['environment_id'])), UUID(str(values['server_id']))
                added = store.add_server_to_environment(
                    conn, project, environment_id, server_id, author, status='draft'
                )
                entry_id = rules.membership_id(environment_id, server_id)
                out['created' if added else 'used'].append({'kind': kind, 'name': step['name'], 'id': entry_id})
                continue
            create, update, _ = WRITERS[kind]
            if step['action'] == 'create':
                ids[step['key']] = create(conn, project, values, author, status='draft')['id']
            elif step['action'] == 'update':
                update(conn, project, UUID(str(step['id'])), step['revision'], values, author, status='draft')
        except (Invalid, store.NotFound, store.Conflict) as error:
            raise Invalid(f'{step["where"]}: {error}') from None
        entry_id = ids[step['key']] if step['action'] == 'create' else step['id']
        out[listed[step['action']]].append({'kind': kind, 'name': step['name'], 'id': str(entry_id)})
    return out


def _recorded(conn, project: UUID, access: dict, created: set) -> dict:
    """What resolve_draft may name: the environments, servers and services in the access or created by this session,
    the deployments whose environment, server and service are among them, the connections and relations from those,
    the code projects in the access; may_create when the access has an environment, server or service."""

    def mine(kind: str, rows: list[dict]) -> list[dict]:
        return [row for row in rows if usable(access, kind, row['id'], created)]

    environments = mine('environment', store.list_environments(conn, project))
    servers = mine('server', store.list_servers(conn, project))
    services = mine('service', store.list_services(conn, project))
    ids = {kind: {row['id'] for row in rows} for kind, rows in (('e', environments), ('s', servers), ('v', services))}
    deployments = [
        d
        for d in store.list_deployments(conn, project)
        if d['environment_id'] in ids['e'] and d['server_id'] in ids['s'] and d['service_id'] in ids['v']
    ]
    deployment_ids = {d['id'] for d in deployments}
    code_projects = []
    for item in access_rules.entries(access, 'code_project'):
        try:
            code_projects.append({'id': UUID(str(item['id'])), 'name': item['name']})
        except ValueError:
            continue
    return {
        'may_create': may_create(access),
        'environments': environments,
        'servers': servers,
        'services': services,
        'deployments': deployments,
        'connections': [c for c in store.list_connections(conn, project) if c['from_deployment_id'] in deployment_ids],
        'relations': [r for r in store.list_relations(conn, project) if r['from_service_id'] in ids['v']],
        'code_projects': code_projects,
    }


# The tools' I/O


def _reply(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _shown(kind: str, row: dict) -> dict:
    """A saved item as a tool shows it: a server without its SSH settings (the model's fields), with its environments."""
    if kind != 'server':
        return row
    return {**{f: row[f] for f in model.SERVER_FIELDS}, 'environment_ids': row['environment_ids']}


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
    """What the checks decide on: the items this session created, and the rows (with their status) of the named
    items (a connection's from-deployment too); an item not in the project has no row."""
    created = store.created_in_session(conn, project, session) if session else set()
    rows = {}
    pending = [(k, v) for k, v in named if k in store.KINDS]
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


def _author(conn, session) -> dict | None:
    """The session's person, agent and session (None: no such session)."""
    started = conn.execute('SELECT actor_id,agent_id FROM ka.agent_sessions WHERE id=%s', (str(session),)).fetchone()
    if started is None:
        return None
    return {'actor_id': started['actor_id'], 'agent_id': started['agent_id'], 'session_id': str(session)}


def _change(config: dict[str, Any], kind: str, item_id, revision, values: dict, check, remove: bool = False) -> str:
    """Create a draft (no id), update a draft (id and revision) or remove a draft, as the session's person, agent and
    session."""
    access = config['access']
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
            author = _author(conn, session)
            if author is None:
                return {'refused': NO_SESSION}
            store.lock_project(conn, project)
            created, rows = _facts(conn, project, session, refs(kind, found_id, found))
            if refusal := check(access, id=found_id, created=created, rows=rows, **found):
                return {'refused': refusal}
            if remove:
                return {'removed': delete(conn, project, UUID(found_id), revision, author)['id']}
            environment = found.pop('environment_id', None) if kind == 'server' else None  # a membership, not a field
            if found_id is None:
                saved = create(conn, project, found, author, status='draft')
            elif found or environment is None:  # only a membership: the server row stays as it is
                saved = update(conn, project, UUID(found_id), revision, found, author, status='draft')
            else:
                saved = {'id': UUID(found_id)}
            if environment is not None:
                store.add_server_to_environment(conn, project, UUID(environment), saved['id'], author, status='draft')
                saved = store.get(conn, project, 'server', saved['id'])
            return _shown(kind, saved)

    return _guarded(run)


def _draft(config: dict[str, Any], payload: dict) -> str:
    """save_inventory_draft: resolve the payload over what the run may name, then write it, in one transaction."""
    session = config.get('session_id')
    if not session:
        return _reply({'refused': NO_SESSION})

    def run():
        with connect() as conn:
            project = project_of(conn, config)
            author = _author(conn, session)
            if author is None:
                return {'refused': NO_SESSION}
            store.lock_project(conn, project)
            created = store.created_in_session(conn, project, session)
            plan = resolve_draft(payload, _recorded(conn, project, config['access'], created))
            return {**apply_draft(conn, project, plan, author), 'message': DRAFTED}

    return _guarded(run)


def _given(**values) -> dict:
    """The values the call gave (None: not given; an update keeps the saved value)."""
    return {k: v for k, v in values.items() if v is not None}


# The usage texts: what each tool is for, that writes are drafts, every field's format and an example.

DRAFTS = (
    'Every write is a draft: it is added to the inventory as a draft that a person confirms or discards in the '
    "environment workspace. You may add drafts and change or remove drafts (yours or another agent's); a confirmed "
    'entry is changed only by people, so the tool refuses it: add a new draft instead, or describe the change in your '
    'answer. Read first (get_environment_model, list_servers, get_resource): every entry carries its status (draft or '
    'confirmed), so you do not add what is already there.'
)
SAVE = (
    'Without id it creates a draft; with id and the revision you read it updates that draft (omitted fields keep '
    'their values). Ids come from the reads or an earlier save. Returns the saved entry as JSON, with its status.'
)
NAMES = 'Names are free text, unique per kind in the workspace (case does not matter).'
HOSTNAME = 'An IP address or hostname without scheme, port, path or credentials (10.0.0.5, db-1.internal).'
SERVICE_TYPE_TEXT = (
    'unclassified (default), custom (your own code), builtin (an off-the-shelf product such as PostgreSQL, nginx or '
    'ClickHouse) or external (outside your servers, reached at an address: a SaaS API, object storage)'
)
SERVICE_ROLE_TEXT = (
    'unclassified (default), application, proxy, web_server, database, cache, message_broker, worker or other'
)
RUNTIME_TEXT = 'Free text: how it runs, such as systemd, docker, docker compose or k8s.'
PROTOCOL_TEXT = 'Lowercase letters, digits or . + - (1–20), such as tcp (default), http, https, postgres or smtp.'
RELATION_TEXT = (
    'depends_on (needs another service to work), part_of (a component of another service) or built_from (built from '
    'a code project)'
)
WHEN = 'Use save_inventory_draft to draft several entries that refer to each other in one call.'
NEW_ENTRY = 'a new {0} needs an environment, server or service in this run'


def _usage(purpose: str, fields: str, example: str) -> str:
    return f'{purpose}\n\n{DRAFTS}\n\n{fields}\n\nExample: {example}'


Id = Annotated[str, Field(min_length=1, max_length=64)]
OptionalId = Annotated[str | None, Field(max_length=64)]
ItemId = Annotated[str | None, Field(max_length=64, description='The draft to update; omit to create one.')]
Revision = Annotated[
    int | None, Field(ge=1, description='With id: the revision you read (from the model, a list or a save).')
]
Text = Annotated[str | None, Field(max_length=4000, description='Free text, up to 4000 characters.')]
Port = Annotated[int | None, Field(ge=1, le=65535, description='A port number, 1–65535.')]
Name = Annotated[str | None, Field(max_length=200, description=f'Required to create. {NAMES}')]


@tool
async def get_environment_model(
    context, environment_id: Annotated[Id, Field(description='The environment id.')]
) -> str:
    """One environment as JSON: its servers (also those with no deployment) with their deployments (service, runtime,
    port, version), its services, the connections touching it (an end in another environment under outside),
    external services (id, name, address, ports used), external hosts with their ports, and the relations of its
    services. Every item carries its id, its revision (you need it to change a draft) and its status: draft (added by
    an agent, waiting for a person to confirm it) or confirmed; a server also has its membership_status in this
    environment.

    Example: {"environment_id": "<environment id>"}"""
    return await asyncio.to_thread(
        _read,
        context['config'],
        check_get_environment_model,
        {'environment_id': environment_id},
        lambda conn, project, ids: model.build_model(**store.load_model_rows(conn, project, ids['environment_id'])),
    )


@tool
async def list_servers(context, environment_id: Annotated[Id, Field(description='The environment id.')]) -> str:
    """The servers in the environment (with or without deployments), as JSON (id, name, hostname, description, tags,
    status (draft or confirmed), revision, environment_ids).

    Example: {"environment_id": "<environment id>"}"""
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
    version, status (draft or confirmed) and revision.

    Example: {"server_id": "<server id>", "service_id": null}"""
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
    deployment, external service or host, port, protocol, description, status (draft or confirmed) and revision.

    Example: {"service_id": "<service id>", "deployment_id": null}"""
    return await asyncio.to_thread(
        _read,
        context['config'],
        check_list_connections,
        {'service_id': service_id, 'deployment_id': deployment_id},
        lambda conn, project, ids: _listed('connections', store.list_connections(conn, project, **ids)),
    )


@tool(
    usage=_usage(
        'Draft an environment (a set of servers and the services deployed on them, such as prod or staging), or update '
        f'a draft environment. {SAVE} Put servers in it with save_server(environment_id) and deploy services with '
        f'save_deployment. {WHEN}',
        f'Fields: name (required to create; up to 100 characters; {NAMES}), description (free text); '
        f'{NEW_ENTRY.format("environment")}.',
        '{"name": "staging", "description": "Pre-production copy of prod"}',
    )
)
async def save_environment(
    context,
    id: ItemId = None,
    revision: Revision = None,
    name: Annotated[str | None, Field(max_length=100, description=f'Required to create. {NAMES}')] = None,
    description: Text = None,
) -> str:
    values = _given(name=name, description=description)
    return await asyncio.to_thread(
        _change, context['config'], 'environment', id, revision, values, check_save_environment
    )


@tool(
    usage=_usage(
        'Draft a server (a machine or VM that services run on), or update a draft server. '
        f'{SAVE} With environment_id the server joins that environment as a draft membership, with or without '
        'deployments (a confirmed server may join too; it is never removed from one here). Then deploy services on it '
        f'with save_deployment. SSH access is set up by a person, not here. {WHEN}',
        f'Fields: name (required to create; {NAMES}), hostname (required to create; {HOSTNAME}), description (free '
        'text), tags (up to 32 lowercase tags of 1–64 characters without commas), environment_id (an environment in '
        'this run or drafted by this session); ' + NEW_ENTRY.format('server') + '.',
        '{"name": "app-1", "hostname": "10.0.0.5", "description": "Docker host", "tags": ["docker"], '
        '"environment_id": "<environment id>"}',
    )
)
async def save_server(
    context,
    id: ItemId = None,
    revision: Revision = None,
    name: Name = None,
    hostname: Annotated[str | None, Field(max_length=253, description=f'Required to create. {HOSTNAME}')] = None,
    description: Text = None,
    tags: Annotated[
        list[str] | None, Field(max_length=32, description='Up to 32 lowercase tags of 1–64 characters, no commas.')
    ] = None,
    environment_id: Annotated[
        OptionalId,
        Field(description='Put the server in this environment (in this run or drafted by this session).'),
    ] = None,
) -> str:
    values = _given(name=name, hostname=hostname, description=description, tags=tags, environment_id=environment_id)
    return await asyncio.to_thread(_change, context['config'], 'server', id, revision, values, check_save_server)


@tool(
    usage=_usage(
        'Draft a service (software that runs on servers, or an external service reached at an address), or update a '
        f'draft service. {SAVE} Record where it runs with save_deployment; an external service is the target of '
        f'connections (save_connection to_service_id). {WHEN}',
        f'Fields: name (required to create; {NAMES}), service_type: {SERVICE_TYPE_TEXT}; service_role: '
        f'{SERVICE_ROLE_TEXT}; description (free text), owner (a team or person, free text), address (where an '
        f'external service is reached; {HOSTNAME}); {NEW_ENTRY.format("service")}.',
        '{"name": "clickhouse", "service_type": "builtin", "service_role": "database", "description": "Analytics '
        'store"}',
    )
)
async def save_service(
    context,
    id: ItemId = None,
    revision: Revision = None,
    name: Name = None,
    service_type: Annotated[Literal[SERVICE_TYPES] | None, Field(description=f'One of: {SERVICE_TYPE_TEXT}.')] = None,
    service_role: Annotated[Literal[SERVICE_ROLES] | None, Field(description=f'One of: {SERVICE_ROLE_TEXT}.')] = None,
    description: Text = None,
    owner: Annotated[str | None, Field(max_length=200, description='The team or person who owns it.')] = None,
    address: Annotated[
        str | None, Field(max_length=253, description=f'Where an external service is reached. {HOSTNAME}')
    ] = None,
) -> str:
    values = _given(
        name=name,
        service_type=service_type,
        service_role=service_role,
        description=description,
        owner=owner,
        address=address,
    )
    return await asyncio.to_thread(_change, context['config'], 'service', id, revision, values, check_save_service)


@tool(
    usage=_usage(
        'Draft a deployment: a service running on a server in an environment; or update a draft deployment. '
        f'{SAVE} The server joins the environment (a draft membership) when it is not in it yet. Connect deployments '
        f'with save_connection. {WHEN}',
        'Fields: service_id, server_id, environment_id (required to create; each in this run or drafted by this '
        f'session), runtime ({RUNTIME_TEXT}), runtime_name (the unit, container or pod name; a service runs once per '
        'runtime_name on a server in an environment), port (the port it listens on, 1–65535), log_location (a path '
        'or command), deployed_version (an image tag or version, free text).',
        '{"service_id": "<service id>", "server_id": "<server id>", "environment_id": "<environment id>", '
        '"runtime": "docker compose", "runtime_name": "clickhouse", "port": 9000, '
        '"deployed_version": "clickhouse/clickhouse-server:24.8"}',
    )
)
async def save_deployment(
    context,
    id: ItemId = None,
    revision: Revision = None,
    service_id: Annotated[OptionalId, Field(description='The service deployed; required to create.')] = None,
    server_id: Annotated[OptionalId, Field(description='The server it runs on; required to create.')] = None,
    environment_id: Annotated[OptionalId, Field(description='Its environment; required to create.')] = None,
    runtime: Annotated[str | None, Field(max_length=80, description=RUNTIME_TEXT)] = None,
    runtime_name: Annotated[str | None, Field(max_length=200, description='The unit, container or pod name.')] = None,
    port: Annotated[Port, Field(description='The port it listens on, 1–65535.')] = None,
    log_location: Annotated[
        str | None, Field(max_length=1000, description='Where its logs are: a path or command.')
    ] = None,
    deployed_version: Annotated[
        str | None, Field(max_length=200, description='The version or image tag running, free text.')
    ] = None,
) -> str:
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


@tool(
    usage=_usage(
        'Draft a connection: a deployment connects to another deployment, an external service or an external host on '
        f'a port; or update a draft connection. {SAVE} {WHEN}',
        'Fields: from_deployment_id (required to create; the deployment that opens the connection), then one target: '
        'to_deployment_id (another deployment), to_service_id (a service of type external) or to_host (an external '
        f"host; {HOSTNAME}); port (1–65535; required for a host, else the target deployment's port), protocol "
        f'({PROTOCOL_TEXT}), description (free text). Every id must be in this run or drafted by this session.',
        '{"from_deployment_id": "<deployment id>", "to_host": "smtp.example.com", "port": 25, "protocol": "smtp"}',
    )
)
async def save_connection(
    context,
    id: ItemId = None,
    revision: Revision = None,
    from_deployment_id: Annotated[
        OptionalId, Field(description='The deployment that connects; required to create.')
    ] = None,
    to_deployment_id: Annotated[
        OptionalId, Field(description='The deployment it connects to (or to_service_id, or to_host).')
    ] = None,
    to_host: Annotated[
        str | None, Field(max_length=253, description=f'An external host it connects to. {HOSTNAME}')
    ] = None,
    to_service_id: Annotated[
        OptionalId, Field(description='An external service (type external) it connects to.')
    ] = None,
    port: Annotated[
        Port, Field(description="1–65535; needed for a host, a deployment's own port when omitted.")
    ] = None,
    protocol: Annotated[str | None, Field(max_length=20, description=PROTOCOL_TEXT)] = None,
    description: Text = None,
) -> str:
    values = _given(
        from_deployment_id=from_deployment_id,
        to_deployment_id=to_deployment_id,
        to_host=to_host,
        to_service_id=to_service_id,
        port=port,
        protocol=protocol,
        description=description,
    )
    return await asyncio.to_thread(
        _change, context['config'], 'connection', id, revision, values, check_save_connection
    )


@tool(
    usage=_usage(
        f'Draft how a service relates to another service or a code project, or update a draft relation. {SAVE} {WHEN}',
        f'Fields: from_service_id (required to create), kind (required to create): {RELATION_TEXT}; to_service_id '
        '(for depends_on and part_of) or to_code_project_id (for built_from), description (free text). Every id must '
        'be in this run or drafted by this session.',
        '{"from_service_id": "<clickhouse-backup id>", "kind": "depends_on", "to_service_id": "<clickhouse id>"}',
    )
)
async def save_relation(
    context,
    id: ItemId = None,
    revision: Revision = None,
    from_service_id: Annotated[OptionalId, Field(description='The service; required to create.')] = None,
    kind: Annotated[Literal[RELATION_KINDS] | None, Field(description=f'One of: {RELATION_TEXT}.')] = None,
    to_service_id: Annotated[OptionalId, Field(description='The other service (depends_on, part_of).')] = None,
    to_code_project_id: Annotated[OptionalId, Field(description='The code project (built_from).')] = None,
    description: Text = None,
) -> str:
    values = _given(
        from_service_id=from_service_id,
        kind=kind,
        to_service_id=to_service_id,
        to_code_project_id=to_code_project_id,
        description=description,
    )
    return await asyncio.to_thread(_change, context['config'], 'relation', id, revision, values, check_save_relation)


@tool(
    usage=_usage(
        'Remove a draft environment, server, service, deployment, connection or relation (a mistake, or one that is '
        'not true). A deployment takes its connections with it; an environment, server or service is refused while '
        'deployments use it. A confirmed entry is never removed by an agent. Returns {"removed": id}.',
        'Fields: kind (environment, server, service, deployment, connection or relation), id, revision (the revision '
        'you read).',
        '{"kind": "connection", "id": "<connection id>", "revision": 1}',
    )
)
async def remove_inventory_item(
    context,
    kind: Annotated[Kind, Field(description='What to remove.')],
    id: Annotated[Id, Field(description='The draft to remove.')],
    revision: Annotated[int, Field(ge=1, description='The revision you read.')],
) -> str:
    check = partial(check_remove_inventory_item, kind=kind)
    return await asyncio.to_thread(_change, context['config'], kind, id, revision, {}, check, True)


# save_inventory_draft's payload


class _Entry(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)


class ServerDraft(_Entry):
    name: str = Field(min_length=1, max_length=200, description=f'The server. {NAMES}')
    hostname: str | None = Field(default=None, max_length=253, description=f'Required for a new server. {HOSTNAME}')
    description: str | None = Field(default=None, max_length=4000, description='Free text.')
    tags: list[str] | None = Field(
        default=None, max_length=32, description='Up to 32 lowercase tags of 1–64 characters, no commas.'
    )


class ServiceDraft(_Entry):
    name: str = Field(min_length=1, max_length=200, description=f'The service. {NAMES}')
    service_type: Literal[SERVICE_TYPES] | None = Field(default=None, description=f'One of: {SERVICE_TYPE_TEXT}.')
    service_role: Literal[SERVICE_ROLES] | None = Field(default=None, description=f'One of: {SERVICE_ROLE_TEXT}.')
    description: str | None = Field(default=None, max_length=4000, description='Free text.')
    owner: str | None = Field(default=None, max_length=200, description='The team or person who owns it.')
    address: str | None = Field(
        default=None, max_length=253, description=f'Where an external service is reached. {HOSTNAME}'
    )
    part_of: list[Annotated[str, Field(min_length=1, max_length=200)]] | None = Field(
        default=None, max_length=20, description='The services this one is a component of, by name (part_of relations).'
    )


class DeploymentDraft(_Entry):
    service: str = Field(min_length=1, max_length=200, description='The service deployed, by name.')
    server: str = Field(min_length=1, max_length=200, description='The server it runs on, by name.')
    runtime: str | None = Field(default=None, max_length=80, description=RUNTIME_TEXT)
    runtime_name: str | None = Field(
        default=None,
        max_length=200,
        description='The unit, container or pod name. Matches the recorded deployment with this runtime name; '
        'without it, the only deployment of the service on the server in the environment (give it when there are '
        'several).',
    )
    port: int | None = Field(default=None, ge=1, le=65535, description='The port it listens on, 1–65535.')
    version: str | None = Field(default=None, max_length=200, description='The version or image tag, free text.')
    log_location: str | None = Field(default=None, max_length=1000, description='Where its logs are.')


class ConnectionDraft(_Entry):
    from_: str = Field(alias='from', min_length=1, max_length=200, description='The service that connects, by name.')
    from_server: str | None = Field(
        default=None, max_length=200, description='Its server, by name: needed only when it runs on several servers.'
    )
    from_runtime_name: str | None = Field(
        default=None,
        max_length=200,
        description="Its deployment's runtime name: needed only when it runs more than once on that server.",
    )
    to: str | None = Field(
        default=None,
        max_length=200,
        description='The service it connects to, by name (an external service, or a service deployed here); or to_host.',
    )
    to_server: str | None = Field(
        default=None, max_length=200, description="The target's server: needed only when it runs on several servers."
    )
    to_runtime_name: str | None = Field(
        default=None,
        max_length=200,
        description="The target deployment's runtime name: needed only when it runs more than once on that server.",
    )
    to_host: str | None = Field(default=None, max_length=253, description=f'An external host; or to. {HOSTNAME}')
    port: int | None = Field(
        default=None, ge=1, le=65535, description="1–65535; needed for a host, else the target deployment's port."
    )
    protocol: str | None = Field(default=None, max_length=20, description=PROTOCOL_TEXT)
    description: str | None = Field(default=None, max_length=4000, description='Free text.')


class RelationDraft(_Entry):
    from_: str = Field(alias='from', min_length=1, max_length=200, description='The service, by name.')
    kind: Literal[RELATION_KINDS] = Field(description=f'One of: {RELATION_TEXT}.')
    to: str = Field(
        min_length=1, max_length=200, description='The other service by name; for built_from, a code project by name.'
    )
    description: str | None = Field(default=None, max_length=4000, description='Free text.')


INVENTORY_DRAFT_EXAMPLE = """{
  "environment": "prod",
  "servers": [{"name": "app-1", "hostname": "10.0.0.5", "description": "Docker host"}],
  "services": [
    {"name": "clickhouse", "service_type": "builtin", "service_role": "database", "description": "Analytics store"},
    {"name": "clickhouse-backup", "service_type": "builtin", "service_role": "worker", "part_of": ["clickhouse"]},
    {"name": "backblaze-b2", "service_type": "external", "address": "s3.us-east-005.backblazeb2.com"}
  ],
  "deployments": [
    {"service": "clickhouse", "server": "app-1", "runtime": "docker compose", "runtime_name": "clickhouse",
     "port": 9000, "version": "clickhouse/clickhouse-server:24.8"},
    {"service": "clickhouse-backup", "server": "app-1", "runtime": "docker compose",
     "runtime_name": "clickhouse-backup", "version": "altinity/clickhouse-backup:2.7.4"}
  ],
  "connections": [
    {"from": "clickhouse-backup", "from_server": "app-1", "to": "clickhouse", "to_server": "app-1",
     "port": 9000, "protocol": "tcp"},
    {"from": "clickhouse-backup", "to": "backblaze-b2", "port": 443, "protocol": "https"},
    {"from": "clickhouse", "to_host": "smtp.example.com", "port": 25, "protocol": "smtp"}
  ],
  "relations": [{"from": "clickhouse-backup", "kind": "depends_on", "to": "clickhouse"}]
}"""


@tool(
    usage=_usage(
        'Draft a whole picture in one call: an environment, its servers, the services and where they are deployed, '
        'the connections between them and their relations. Entries refer to each other by name, not id. Every part '
        'is optional.',
        'How names resolve: a name refers to an entry in this call first, then to a recorded entry (confirmed or '
        'draft) in this run or drafted by this session. A recorded confirmed entry named here is used as it is, never '
        'changed (its fields here are ignored, but its part_of still adds draft relations); a recorded draft is '
        'updated with the fields given; anything else is created as a draft (a new entry needs an environment, server '
        'or service in this run). environment names an existing environment or drafts a new one; every server listed '
        'and every server a deployment runs on joins it (a draft membership), and deployments and connections are in '
        'it.\n'
        f'- servers: name, hostname ({HOSTNAME}), description, tags.\n'
        f'- services: name, service_type: {SERVICE_TYPE_TEXT}; service_role: {SERVICE_ROLE_TEXT}; description, owner, '
        'address (an external service), part_of (names of the services it is a component of).\n'
        f'- deployments: service and server (names), runtime ({RUNTIME_TEXT}), runtime_name (unit, container or pod '
        'name), port (1–65535), version, log_location. A deployment is the recorded one of that service on that '
        'server in the environment: with runtime_name the one with that runtime name, without it the only one there '
        '(when there are several, give runtime_name); else it is created.\n'
        '- connections: from (a service name), to (a service name: an external service, or a service deployed in '
        f'the environment) or to_host ({HOSTNAME}), port (1–65535; needed for a host), protocol ({PROTOCOL_TEXT}), '
        "description. A connection starts and ends at that service's deployment in the environment: from_server and "
        'to_server name the server only when the service runs on more than one, from_runtime_name and '
        'to_runtime_name the runtime name only when it runs more than once on that server.\n'
        f'- relations: from (a service name), kind: {RELATION_TEXT}; to (a service name, or a code project name for '
        'built_from), description.\n'
        'All or nothing: on any error nothing is written and the reply names the entry and the reason, such as '
        '"connections[0]: no service named clickhous. Known services: clickhouse, clickhouse-backup." The reply '
        'lists what was created, updated and used, each with kind, name and id.',
        INVENTORY_DRAFT_EXAMPLE,
    )
)
async def save_inventory_draft(
    context,
    environment: Annotated[
        str | None,
        Field(min_length=1, max_length=100, description='The environment, by name: an existing one or a new draft.'),
    ] = None,
    servers: Annotated[list[ServerDraft] | None, Field(max_length=100, description='Servers, by name.')] = None,
    services: Annotated[list[ServiceDraft] | None, Field(max_length=100, description='Services, by name.')] = None,
    deployments: Annotated[
        list[DeploymentDraft] | None, Field(max_length=200, description='Where each service runs in the environment.')
    ] = None,
    connections: Annotated[
        list[ConnectionDraft] | None, Field(max_length=200, description='Who connects to whom, on which port.')
    ] = None,
    relations: Annotated[
        list[RelationDraft] | None, Field(max_length=200, description='How services relate to each other.')
    ] = None,
) -> str:
    payload = {
        'environment': environment,
        'servers': servers,
        'services': services,
        'deployments': deployments,
        'connections': connections,
        'relations': relations,
    }
    return await asyncio.to_thread(_draft, context['config'], payload)
