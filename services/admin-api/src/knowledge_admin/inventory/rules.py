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
SERVICE_FIELDS = ('name', 'service_type', 'service_role', 'description', 'owner')
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
CONNECTION_FIELDS = ('from_deployment_id', 'to_deployment_id', 'to_host', 'port', 'protocol', 'description')
RELATION_FIELDS = ('from_service_id', 'kind', 'to_service_id', 'to_code_project_id', 'description')


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


def service(values: dict) -> dict:
    _fields(values, SERVICE_FIELDS)
    return {
        'name': _text(values, 'name', 200, required=True),
        'service_type': _choice(values, 'service_type', SERVICE_TYPES, 'unclassified'),
        'service_role': _choice(values, 'service_role', SERVICE_ROLES, 'unclassified'),
        'description': _text(values, 'description', 4000),
        'owner': _text(values, 'owner', 200),
    }


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
    """From a deployment to exactly one target: another deployment, or an external host (which needs a port). A
    connection to a deployment without a port takes the target's port in the store."""
    _fields(values, CONNECTION_FIELDS)
    source = _id(values, 'from_deployment_id')
    target = _id(values, 'to_deployment_id', required=False)
    to_host = None if values.get('to_host') in (None, '') else host(values['to_host'])
    if (target is None) == (to_host is None):
        raise Invalid('Connect to either a deployment or an external host')
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
