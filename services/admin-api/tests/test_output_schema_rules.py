"""The strict output schema subset and sample validation: pure functions."""

import copy

import pytest
from knowledge_admin.output_schemas.rules import MAX_SCHEMA_BYTES, check_schema, validate

GOOD = {
    'type': 'object',
    'description': 'A triage result.',
    'properties': {
        'severity': {'type': 'string', 'enum': ['low', 'high']},
        'score': {'type': ['number', 'null']},
        'count': {'type': 'integer'},
        'done': {'type': 'boolean'},
        'tags': {'type': 'array', 'items': {'type': 'string'}},
        'owner': {
            'type': 'object',
            'properties': {'name': {'type': 'string'}},
            'required': ['name'],
            'additionalProperties': False,
        },
        'steps': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {'title': {'type': 'string'}},
                'required': ['title'],
                'additionalProperties': False,
            },
        },
    },
    'required': ['severity', 'score', 'count', 'done', 'tags', 'owner', 'steps'],
    'additionalProperties': False,
}
VALUE = {
    'severity': 'low',
    'score': None,
    'count': 2,
    'done': False,
    'tags': ['a'],
    'owner': {'name': 'Ana'},
    'steps': [{'title': 'Check'}],
}


def changed(path: list, value=...):
    """GOOD with the node at `path` replaced (or, without a value, removed)."""
    schema = copy.deepcopy(GOOD)
    node = schema
    for key in path[:-1]:
        node = node[key]
    if value is ...:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return schema


def test_the_subset_is_accepted():
    assert check_schema(GOOD) == []
    assert check_schema({'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}) == []


@pytest.mark.parametrize(
    ('schema', 'problem'),
    [
        (changed(['properties', 'tags', 'minItems'], 1), "$.properties.tags: 'minItems' is not supported"),
        (changed(['properties', 'count', 'minimum'], 0), "$.properties.count: 'minimum' is not supported"),
        (changed(['properties', 'severity', 'pattern'], 'x'), "$.properties.severity: 'pattern' is not supported"),
        (
            changed(['properties', 'tags', 'items', 'format'], 'uri'),
            "$.properties.tags.items: 'format' is not supported",
        ),
        (
            changed(['properties', 'score'], {'anyOf': [{'type': 'number'}]}),
            "$.properties.score: 'anyOf' is not supported",
        ),
        (changed(['$ref'], '#/$defs/a'), "$: '$ref' is not supported"),
        (changed(['$defs'], {}), "$: '$defs' is not supported"),
        (changed(['type'], 'array'), '$: the root must be an object'),
        (changed(['additionalProperties']), '$: an object must set additionalProperties to false'),
        (changed(['additionalProperties'], True), '$: an object must set additionalProperties to false'),
        (
            changed(['required'], ['severity']),
            '$: required must list every property (missing: count, done, owner, score, steps, tags)',
        ),
        (changed(['required'], GOOD['required'] + ['ghost']), "$: required lists 'ghost', which is not a property"),
        (
            changed(['properties', 'owner', 'additionalProperties'], True),
            '$.properties.owner: an object must set additionalProperties to false',
        ),
        (
            changed(['properties', 'steps', 'items', 'required'], []),
            '$.properties.steps.items: required must list every property (missing: title)',
        ),
        (changed(['properties', 'tags', 'items']), '$.properties.tags: an array needs items'),
        (changed(['properties', 'count', 'type']), '$.properties.count: type is required'),
        (changed(['properties', 'count', 'type'], 'float'), "$.properties.count: type 'float' is not supported"),
        (changed(['properties', 'count', 'type'], []), '$.properties.count: type is required'),
        (changed(['properties', 'severity', 'enum'], []), '$.properties.severity: enum must be a non-empty list'),
        (changed(['properties', 'severity', 'description'], 3), '$.properties.severity: description must be text'),
        (changed(['properties', 'count', 'properties'], {}), "$.properties.count: 'properties' is only for objects"),
        (
            changed(['properties', 'count', 'items'], {'type': 'string'}),
            "$.properties.count: 'items' is only for arrays",
        ),
        (changed(['properties', 'tags'], 'string'), '$.properties.tags: must be an object'),
    ],
)
def test_outside_the_subset_is_refused_with_its_path(schema, problem):
    assert problem in check_schema(schema)


def test_every_problem_is_listed():
    schema = changed(['properties', 'tags', 'minItems'], 1)
    schema['properties']['count']['minimum'] = 0
    assert len(check_schema(schema)) == 2


def test_a_schema_must_be_an_object():
    assert check_schema([]) == ['$: a schema must be a JSON object']
    assert check_schema(None) == ['$: a schema must be a JSON object']


def test_a_schema_over_64_kb_is_refused():
    big = changed(['description'], 'x' * MAX_SCHEMA_BYTES)
    assert check_schema(big) == ['$: the schema is larger than 64 KB']


def test_the_size_is_measured_in_bytes_of_utf8_json():
    accented = 'é' * (MAX_SCHEMA_BYTES // 2 - 1000)  # 2 bytes each, but 6 characters as a \\u escape
    assert check_schema(changed(['description'], accented)) == []
    assert check_schema(changed(['description'], accented + 'é' * 1000)) == ['$: the schema is larger than 64 KB']


def test_validate_accepts_matching_json():
    assert validate(GOOD, VALUE) == []


@pytest.mark.parametrize(
    ('value', 'problem'),
    [
        ({**VALUE, 'severity': 'medium'}, '$.severity'),
        ({**VALUE, 'count': 'two'}, '$.count'),
        ({**VALUE, 'count': True}, '$.count'),
        ({**VALUE, 'extra': 1}, '$'),
        ({'severity': 'low'}, '$'),
        ({**VALUE, 'steps': [{'title': 1}]}, '$.steps[0].title'),
        ('text', '$'),
    ],
)
def test_validate_lists_errors_with_their_location(value, problem):
    errors = validate(GOOD, value)
    assert errors and any(e.startswith(f'{problem}: ') for e in errors), errors


def test_required_holding_something_other_than_names_is_a_problem():
    assert check_schema(changed(['required'], [[1], 'severity'])) == [
        '$: required lists [1], which is not a property name',
        '$: required must list every property (missing: count, done, owner, score, steps, tags)',
    ]
