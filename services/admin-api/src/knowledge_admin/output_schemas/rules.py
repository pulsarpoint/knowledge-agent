"""The output schema rules, pure: which JSON Schemas are accepted (the strict subset structured output takes) and
whether a value matches one. $ref and $defs are refused: a schema is one self-contained tree."""

import json
from typing import Any

from jsonschema import Draft202012Validator

MAX_SCHEMA_BYTES = 64 * 1024
TYPES = ('object', 'array', 'string', 'number', 'integer', 'boolean', 'null')
KEYWORDS = ('type', 'description', 'enum', 'properties', 'required', 'additionalProperties', 'items')
ONLY_FOR_OBJECTS = ('properties', 'required', 'additionalProperties')


def check_schema(schema: Any) -> list[str]:
    """The problems that keep `schema` from being an output schema, each with its location ("$.properties.tags: ...")."""
    if not isinstance(schema, dict):
        return ['$: a schema must be a JSON object']
    if len(json.dumps(schema)) > MAX_SCHEMA_BYTES:
        return ['$: the schema is larger than 64 KB']
    problems = _node(schema, '$')
    if schema.get('type') != 'object':
        problems.append('$: the root must be an object')
    return problems


def validate(schema: dict, value: Any) -> list[str]:
    """The ways `value` does not match `schema` ("$.steps[0].title: 1 is not of type 'string'"); empty when it does."""
    errors = Draft202012Validator(schema).iter_errors(value)
    return [
        f'{_location(error.absolute_path)}: {error.message}'
        for error in sorted(errors, key=lambda e: list(map(str, e.absolute_path)))
    ]


def _location(path) -> str:
    return '$' + ''.join(f'[{part}]' if isinstance(part, int) else f'.{part}' for part in path)


def _node(node: Any, at: str) -> list[str]:
    if not isinstance(node, dict):
        return [f'{at}: must be an object']
    problems = [f"{at}: '{key}' is not supported" for key in node if key not in KEYWORDS]
    types = _types(node.get('type'), at, problems)
    if 'description' in node and not isinstance(node['description'], str):
        problems.append(f'{at}: description must be text')
    if 'enum' in node and not (isinstance(node['enum'], list) and node['enum']):
        problems.append(f'{at}: enum must be a non-empty list')
    if 'object' in types:
        problems += _object(node, at)
    else:
        problems += [f"{at}: '{key}' is only for objects" for key in ONLY_FOR_OBJECTS if key in node]
    if 'array' in types:
        if 'items' not in node:
            problems.append(f'{at}: an array needs items')
        else:
            problems += _node(node['items'], f'{at}.items')
    elif 'items' in node:
        problems.append(f"{at}: 'items' is only for arrays")
    return problems


def _types(declared: Any, at: str, problems: list[str]) -> list[str]:
    listed = declared if isinstance(declared, list) else [declared]
    if declared is None or declared == [] or not all(isinstance(t, str) for t in listed):
        problems.append(f'{at}: type is required')
        return []
    problems += [f"{at}: type '{t}' is not supported" for t in listed if t not in TYPES]
    return listed


def _object(node: dict, at: str) -> list[str]:
    problems = []
    if node.get('additionalProperties') is not False:
        problems.append(f'{at}: an object must set additionalProperties to false')
    properties = node.get('properties')
    if not isinstance(properties, dict):
        return problems + [f'{at}: an object needs properties']
    required = node.get('required')
    if not isinstance(required, list):
        problems.append(f'{at}: an object needs required')
        required = []
    problems += [
        f'{at}: required lists {json.dumps(name)}, which is not a property name'
        for name in required
        if not isinstance(name, str)
    ]
    required = [name for name in required if isinstance(name, str)]
    problems += [
        f"{at}: required lists '{name}', which is not a property" for name in required if name not in properties
    ]
    missing = sorted(name for name in properties if name not in required)
    if missing:
        problems.append(f'{at}: required must list every property (missing: {", ".join(missing)})')
    for name, child in properties.items():
        problems += _node(child, f'{at}.properties.{name}')
    return problems
