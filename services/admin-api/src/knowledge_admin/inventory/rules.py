"""Pure validation of inventory items. Each function takes the item's fields as a dict (a new item, or the saved item
merged with the changes) and returns the full, normalized item, or raises Invalid with a message for the person or
agent that sent it. No database access: references (does that service exist in this project?) are the store's job."""

import ipaddress
import re
from uuid import UUID

SERVICE_TYPES = ('unclassified', 'custom', 'builtin', 'external')
SERVICE_ROLES = (
    'unclassified',
    'application',
    'proxy',
    'web_server',
    'database',
    'cache',
    'message_broker',
    'worker',
    'other',
)
RELATION_KINDS = ('depends_on', 'part_of', 'built_from')
PROTOCOL = re.compile(r'[a-z0-9.+-]{1,20}')
LABEL = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?')
SERVER_FIELDS = ('name', 'hostname', 'description', 'tags', 'ssh_allowed')
SERVICE_FIELDS = ('name', 'service_type', 'service_role', 'description', 'owner', 'address')
ENVIRONMENT_FIELDS = ('name', 'description')
DEPLOYMENT_FIELDS = (
    'service_id',
    'server_id',
    'environment_id',
    'runtime',
    'runtime_name',
    'port',
    'log_location',
    'deployed_version',
)
CONNECTION_FIELDS = (
    'from_deployment_id',
    'to_deployment_id',
    'to_host',
    'to_service_id',
    'port',
    'protocol',
    'description',
)
RELATION_FIELDS = ('from_service_id', 'kind', 'to_service_id', 'to_code_project_id', 'description')


NOT_EXTERNAL_TARGET = 'Connect to an external service, or to a deployment of this service.'
TARGETED_TYPE = 'This service is the target of connections; remove them or keep the type external.'


class Invalid(ValueError):
    pass


def _fields(values: dict, allowed: tuple[str, ...]) -> None:
    unknown = sorted(set(values) - set(allowed))
    if unknown:
        raise Invalid(f'Unknown field: {", ".join(unknown)}')


def _text(values: dict, key: str, limit: int, required: bool = False) -> str:
    value = values.get(key)
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise Invalid(f'{key} must be text')
    value = value.strip()
    if '\x00' in value:
        raise Invalid(f'{key} cannot contain null characters')
    if required and not value:
        raise Invalid(f'{key} is required')
    if len(value) > limit:
        raise Invalid(f'{key} must be at most {limit} characters')
    return value


def _id(values: dict, key: str, required: bool = True) -> UUID | None:
    value = values.get(key)
    if value is None:
        if required:
            raise Invalid(f'{key} is required')
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except ValueError:
        raise Invalid(f'{key} must be an id') from None


def _port(values: dict, key: str = 'port') -> int | None:
    value = values.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise Invalid(f'{key} must be a number from 1 to 65535')
    return value


def _choice(values: dict, key: str, choices: tuple[str, ...], default: str | None = None) -> str:
    value = values.get(key, default)
    if value not in choices:
        raise Invalid(f'{key} must be one of: {", ".join(choices)}')
    return value


def host(value) -> str:
    """An IP address or a hostname (no URL, port or credentials), lower-cased without a trailing dot."""
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 253:
        raise Invalid('Enter an IP address or hostname of 1–253 characters')
    value = value.strip()
    if '%' in value:  # an IPv6 zone id names an interface of one host, not an address others can reach
        raise Invalid('Enter an IP address without a zone id (%…)')
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        labels = value.removesuffix('.').split('.')
        if not all(LABEL.fullmatch(label) for label in labels):
            raise Invalid('Enter an IP address or hostname without a URL, port, or credentials') from None
        return value.removesuffix('.').lower()


def _tags(values: dict) -> list[str]:
    raw = values.get('tags') or []
    if not isinstance(raw, list) or len(raw) > 32:
        raise Invalid('tags must be a list of at most 32 tags')
    tags = []
    for value in raw:
        tag = value.strip().lower() if isinstance(value, str) else ''
        if not tag or len(tag) > 64 or ',' in tag or any(ord(char) < 32 or ord(char) == 127 for char in tag):
            raise Invalid('Tags must contain 1–64 characters without commas or control characters')
        if tag not in tags:
            tags.append(tag)
    return tags


def server(values: dict) -> dict:
    _fields(values, SERVER_FIELDS)
    ssh_allowed = values.get('ssh_allowed', False)
    if not isinstance(ssh_allowed, bool):
        raise Invalid('ssh_allowed must be true or false')
    return {
        'name': _text(values, 'name', 200, required=True),
        'hostname': host(values.get('hostname')),
        'description': _text(values, 'description', 4000),
        'tags': _tags(values),
        'ssh_allowed': ssh_allowed,
    }


def _host(values: dict, key: str) -> str | None:
    return None if values.get(key) in (None, '') else host(values[key])


def service(values: dict) -> dict:
    """address: where an external service is reached (an IP address or hostname), optional."""
    _fields(values, SERVICE_FIELDS)
    return {
        'name': _text(values, 'name', 200, required=True),
        'service_type': _choice(values, 'service_type', SERVICE_TYPES, 'unclassified'),
        'service_role': _choice(values, 'service_role', SERVICE_ROLES, 'unclassified'),
        'description': _text(values, 'description', 4000),
        'owner': _text(values, 'owner', 200),
        'address': _host(values, 'address'),
    }


def check_target_service(service_type: str) -> str | None:
    """A connection's to_service_id names an external service; a service of another type is reached through one of
    its deployments."""
    return None if service_type == 'external' else NOT_EXTERNAL_TARGET


def check_type_change(old_type: str, new_type: str, targeted: bool) -> str | None:
    """An external service that connections point to keeps its type."""
    if targeted and old_type == 'external' and new_type != 'external':
        return TARGETED_TYPE
    return None


def environment(values: dict) -> dict:
    _fields(values, ENVIRONMENT_FIELDS)
    return {'name': _text(values, 'name', 100, required=True), 'description': _text(values, 'description', 4000)}


def deployment(values: dict) -> dict:
    _fields(values, DEPLOYMENT_FIELDS)
    return {
        'service_id': _id(values, 'service_id'),
        'server_id': _id(values, 'server_id'),
        'environment_id': _id(values, 'environment_id'),
        'runtime': _text(values, 'runtime', 80),
        'runtime_name': _text(values, 'runtime_name', 200),
        'port': _port(values),
        'log_location': _text(values, 'log_location', 1000),
        'deployed_version': _text(values, 'deployed_version', 200),
    }


def connection(values: dict) -> dict:
    """From a deployment to exactly one target: another deployment, an external service (its type is the store's
    check), or an external host (which needs a port). A connection to a deployment without a port takes the target's
    port in the store."""
    _fields(values, CONNECTION_FIELDS)
    source = _id(values, 'from_deployment_id')
    target = _id(values, 'to_deployment_id', required=False)
    to_service = _id(values, 'to_service_id', required=False)
    to_host = _host(values, 'to_host')
    if [target, to_host, to_service].count(None) != 2:
        raise Invalid('Connect to a deployment, an external service or an external host')
    if target == source:
        raise Invalid('A deployment cannot connect to itself')
    port = _port(values)
    if to_host is not None and port is None:
        raise Invalid('A connection to an external host needs a port')
    protocol = values.get('protocol', 'tcp')
    if not isinstance(protocol, str) or PROTOCOL.fullmatch(protocol) is None:
        raise Invalid('protocol must be 1–20 lowercase letters, digits or . + -')
    return {
        'from_deployment_id': source,
        'to_deployment_id': target,
        'to_host': to_host,
        'to_service_id': to_service,
        'port': port,
        'protocol': protocol,
        'description': _text(values, 'description', 4000),
    }


def relation(values: dict) -> dict:
    """depends_on and part_of point at another service; built_from at a code project."""
    _fields(values, RELATION_FIELDS)
    kind = _choice(values, 'kind', RELATION_KINDS)
    source = _id(values, 'from_service_id')
    to_service = _id(values, 'to_service_id', required=False)
    to_code_project = _id(values, 'to_code_project_id', required=False)
    if kind == 'built_from' and (to_code_project is None or to_service is not None):
        raise Invalid('built_from points at a code project')
    if kind != 'built_from' and (to_service is None or to_code_project is not None):
        raise Invalid(f'{kind} points at another service')
    if to_service == source:
        raise Invalid('A service cannot relate to itself')
    return {
        'from_service_id': source,
        'kind': kind,
        'to_service_id': to_service,
        'to_code_project_id': to_code_project,
        'description': _text(values, 'description', 4000),
    }


# Drafts. An agent writes drafts; a person confirms a draft (with every draft it needs) or discards it (with every draft
# that needs it). The functions below work over the project's rows keyed by kind, then id: environment, server,
# service, deployment, connection and relation by id, membership by membership_id(environment, server), and
# code_project (names only). Each row has its status ('draft' or 'confirmed') and the ids it refers to.

DRAFT_KINDS = ('environment', 'server', 'service', 'deployment', 'connection', 'relation', 'membership')


def confirmed_refusal(kind: str) -> str:
    """Why an agent cannot change a confirmed entry."""
    return (
        f'This {kind} is confirmed; only a person can change it. Add a new draft instead, or describe the change in '
        'your answer.'
    )


def membership_id(environment_id, server_id) -> str:
    return f'{environment_id}:{server_id}'


def _needs(kind: str, row: dict) -> list[tuple[str, object]]:
    """The entries this one cannot exist without."""
    if kind == 'membership':
        return [('server', row['server_id']), ('environment', row['environment_id'])]
    if kind == 'deployment':
        return [
            ('server', row['server_id']),
            ('service', row['service_id']),
            ('membership', membership_id(row['environment_id'], row['server_id'])),
            ('environment', row['environment_id']),
        ]
    if kind == 'connection':
        ends = [('deployment', row['from_deployment_id']), ('deployment', row['to_deployment_id'])]
        return [end for end in [*ends, ('service', row['to_service_id'])] if end[1] is not None]
    if kind == 'relation':  # a code project is never a draft
        ends = [('service', row['from_service_id']), ('service', row['to_service_id'])]
        return [end for end in ends if end[1] is not None]
    return []


def _dependants(kind: str, key, rows: dict) -> list[tuple[str, object]]:
    """The entries that cannot exist without this one."""
    found: list[tuple[str, object]] = []
    if kind in ('environment', 'server'):
        found += [('membership', k) for k, m in rows['membership'].items() if m[f'{kind}_id'] == key]
        found += [('deployment', k) for k, d in rows['deployment'].items() if d[f'{kind}_id'] == key]
    if kind == 'membership':
        found += [
            ('deployment', k)
            for k, d in rows['deployment'].items()
            if membership_id(d['environment_id'], d['server_id']) == key
        ]
    if kind == 'service':
        found += [('deployment', k) for k, d in rows['deployment'].items() if d['service_id'] == key]
        found += [('connection', k) for k, c in rows['connection'].items() if c['to_service_id'] == key]
        found += [
            ('relation', k) for k, r in rows['relation'].items() if key in (r['from_service_id'], r['to_service_id'])
        ]
    if kind == 'deployment':
        found += [
            ('connection', k)
            for k, c in rows['connection'].items()
            if key in (c['from_deployment_id'], c['to_deployment_id'])
        ]
    return found


def _walk(start: tuple[str, object], step) -> list[tuple[str, object]]:
    """Every entry reached from start by repeating step (breadth first), start left out."""
    seen, queue, found = {start}, [start], []
    while queue:
        for entry in step(*queue.pop(0)):
            if entry not in seen:
                seen.add(entry)
                queue.append(entry)
                found.append(entry)
    return found


def _is_draft(rows: dict, entry: tuple[str, object]) -> bool:
    return rows[entry[0]][entry[1]]['status'] == 'draft'


def needs(kind: str, key, rows: dict) -> list[tuple[str, object]]:
    """The drafts this entry needs, directly or through other entries (it left out)."""
    found = _walk((kind, key), lambda k, i: _needs(k, rows[k][i]))
    return [entry for entry in found if _is_draft(rows, entry)]


def confirm_reach(kind: str, key, rows: dict) -> list[tuple[str, object]]:
    """What confirming this draft confirms: the draft, then every draft it needs."""
    if not _is_draft(rows, (kind, key)):
        raise Invalid(f'This {kind} is already confirmed.')
    return [(kind, key), *needs(kind, key, rows)]


def discard_reach(kind: str, key, rows: dict) -> list[tuple[str, object]]:
    """What discarding this draft removes: the draft, then every entry that needs it. Refused when one of those is
    confirmed (a confirmed entry is never removed by a discard)."""
    if not _is_draft(rows, (kind, key)):
        raise Invalid('Only a draft can be discarded.')
    found = _walk((kind, key), lambda k, i: _dependants(k, i, rows))
    for entry in found:
        if not _is_draft(rows, entry):
            raise Invalid(
                f'A confirmed {entry[0]} ({entry_name(*entry, rows)}) needs this draft. Remove that {entry[0]} first, '
                'or confirm the draft.'
            )
    return [(kind, key), *found]


def entry_name(kind: str, key, rows: dict) -> str:
    """How a person reads the entry in a list: its name, or what it joins."""
    row = rows[kind][key]

    def name(of: str, item_id) -> str:
        return rows[of][item_id]['name']

    if kind == 'membership':
        return f'{name("server", row["server_id"])} in {name("environment", row["environment_id"])}'
    if kind == 'deployment':
        where = f'{name("service", row["service_id"])} on {name("server", row["server_id"])}'
        return f'{where} in {name("environment", row["environment_id"])}'
    if kind == 'connection':
        start = rows['deployment'][row['from_deployment_id']]['service_id']
        if row['to_deployment_id'] is not None:
            end = name('service', rows['deployment'][row['to_deployment_id']]['service_id'])
        elif row['to_service_id'] is not None:
            end = name('service', row['to_service_id'])
        else:
            end = row['to_host']
        return f'{name("service", start)} → {end}'
    if kind == 'relation':
        end = row['to_service_id']
        target = name('service', end) if end is not None else name('code_project', row['to_code_project_id'])
        return f'{name("service", row["from_service_id"])} {row["kind"]} {target}'
    return row['name']
