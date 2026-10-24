"""Administration → Output schemas API: create, read with versions, edit (a version only for a changed schema), check,
delete (refused while a handler uses it)."""

from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from test_api import _client
from test_repositories import HEADERS

SCHEMA = {
    'type': 'object',
    'properties': {'severity': {'type': 'string'}},
    'required': ['severity'],
    'additionalProperties': False,
}
CHANGED = {
    **SCHEMA,
    'properties': {'severity': {'type': 'string'}, 'tags': {'type': 'array', 'items': {'type': 'string'}}},
    'required': ['severity', 'tags'],
}


@pytest.fixture
def client(store, tmp_path):
    return _client(tmp_path, repository_store=store)


def created(client, name='Triage', schema=SCHEMA, **values) -> dict:
    response = client.post('/api/output-schemas', headers=HEADERS, json={'name': name, 'schema': schema, **values})
    assert response.status_code == 201, response.text
    return response.json()['schema']


def saved(client, entry, **changes):
    body = {
        'name': entry['name'],
        'description': entry['description'],
        'schema': entry['schema'],
        'revision': entry['revision'],
    }
    return client.put(f'/api/output-schemas/{entry["id"]}', headers=HEADERS, json={**body, **changes})


def message(response, status):
    assert response.status_code == status, response.text
    return response.json()['error']['message']


def use_in_handler(store, entry, name='Triage handler'):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        actor = store.actor(conn, 'admin-id', 'admin@example.test')
        conn.execute(
            """INSERT INTO ka.event_handlers(project_id,name,type_pattern,action,emit,created_by,output_schema_id)
            VALUES (%s,%s,'*','emit',%s,%s,%s)""",
            (project, name, Jsonb({'type': 'a.b'}), actor, entry['id']),
        )


def test_requires_the_admin_gateway(client):
    assert client.get('/api/output-schemas').status_code == 403
    assert client.post('/api/output-schemas/check', json={'schema': SCHEMA}).status_code == 403


def test_create_read_and_list(client, store):
    entry = created(client, description='What triage answers with.')
    assert (entry['name'], entry['current_version'], entry['revision'], entry['schema']) == ('Triage', 1, 1, SCHEMA)
    one = client.get(f'/api/output-schemas/{entry["id"]}', headers=HEADERS).json()['schema']
    assert one['schema'] == SCHEMA and one['description'] == 'What triage answers with.'
    assert [(v['version'], v['created_by_email']) for v in one['versions']] == [(1, 'admin@example.test')]
    listed = client.get('/api/output-schemas', headers=HEADERS).json()['schemas']
    assert [(s['name'], s['current_version'], s['used_by']) for s in listed] == [('Triage', 1, [])]
    assert 'schema' not in listed[0]
    with store.connect() as conn:
        action = conn.execute("SELECT action FROM ka.audit_events WHERE resource_type='output_schema'").fetchone()
    assert action['action'] == 'output_schema.created'


def test_a_schema_outside_the_subset_is_refused_with_its_problems(client):
    bad = {**SCHEMA, 'properties': {'severity': {'type': 'string', 'minLength': 1}}}
    response = client.post('/api/output-schemas', headers=HEADERS, json={'name': 'Bad', 'schema': bad})
    assert "$.properties.severity: 'minLength' is not supported" in message(response, 400)
    assert client.get('/api/output-schemas', headers=HEADERS).json()['schemas'] == []


def test_a_name_is_unique(client):
    created(client)
    response = client.post('/api/output-schemas', headers=HEADERS, json={'name': 'Triage', 'schema': SCHEMA})
    assert message(response, 409) == 'An output schema named Triage already exists.'


def test_a_changed_schema_adds_a_version_and_the_old_one_stays(client):
    entry = created(client)
    edited = saved(client, entry, schema=CHANGED)
    assert edited.status_code == 200, edited.text
    edited = edited.json()['schema']
    assert (edited['current_version'], edited['revision'], edited['schema']) == (2, 2, CHANGED)
    assert [v['version'] for v in edited['versions']] == [2, 1]
    first = client.get(f'/api/output-schemas/{entry["id"]}/versions/1', headers=HEADERS).json()['version']
    assert (first['version'], first['schema']) == (1, SCHEMA)
    assert client.get(f'/api/output-schemas/{entry["id"]}/versions/3', headers=HEADERS).status_code == 404


def test_an_unchanged_schema_adds_no_version(client):
    entry = created(client)
    reordered = {key: SCHEMA[key] for key in reversed(list(SCHEMA))}  # the same schema, written in another order
    edited = saved(client, entry, name='Triage result', description='Now described', schema=reordered).json()['schema']
    assert (edited['current_version'], edited['revision'], edited['name']) == (1, 2, 'Triage result')
    assert [v['version'] for v in edited['versions']] == [1]


def test_edit_checks_the_revision_the_schema_and_the_name(client):
    entry = created(client)
    created(client, name='Other')
    assert message(saved(client, entry, revision=5), 409) == 'This output schema changed since you opened it.'
    assert '$: the root must be an object' in message(saved(client, entry, schema={'properties': {}}), 400)
    assert message(saved(client, entry, name='Other'), 409) == 'An output schema named Other already exists.'
    missing = client.put(
        f'/api/output-schemas/{uuid4()}', headers=HEADERS, json={'name': 'x', 'schema': SCHEMA, 'revision': 1}
    )
    assert message(missing, 404) == 'Output schema not found.'


def test_delete_is_refused_while_a_handler_uses_it(client, store):
    entry = created(client)
    use_in_handler(store, entry, 'Triage handler')
    use_in_handler(store, entry, 'Another')
    response = client.delete(f'/api/output-schemas/{entry["id"]}?revision=1', headers=HEADERS)
    assert message(response, 409) == 'Used by: Another, Triage handler.'
    listed = client.get('/api/output-schemas', headers=HEADERS).json()['schemas']
    assert listed[0]['used_by'] == ['Another', 'Triage handler']
    assert client.get(f'/api/output-schemas/{entry["id"]}', headers=HEADERS).status_code == 200


def test_delete_an_unused_schema_with_its_versions(client, store):
    entry = created(client)
    entry = saved(client, entry, schema=CHANGED).json()['schema']
    assert (
        message(client.delete(f'/api/output-schemas/{entry["id"]}?revision=1', headers=HEADERS), 409)
        == 'This output schema changed since you opened it.'
    )
    assert client.delete(f'/api/output-schemas/{entry["id"]}?revision=2', headers=HEADERS).status_code == 204
    assert client.get(f'/api/output-schemas/{entry["id"]}', headers=HEADERS).status_code == 404
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.output_schema_versions').fetchone()['n'] == 0
        assert (
            conn.execute("SELECT count(*) AS n FROM ka.audit_events WHERE action='output_schema.deleted'").fetchone()[
                'n'
            ]
            == 1
        )


def test_check_reports_problems_and_the_sample(client):
    ok = client.post('/api/output-schemas/check', headers=HEADERS, json={'schema': SCHEMA}).json()
    assert ok == {'problems': [], 'sample_errors': None}
    sample = client.post(
        '/api/output-schemas/check', headers=HEADERS, json={'schema': SCHEMA, 'sample': {'severity': 'low'}}
    ).json()
    assert sample == {'problems': [], 'sample_errors': []}
    wrong = client.post(
        '/api/output-schemas/check', headers=HEADERS, json={'schema': SCHEMA, 'sample': {'severity': 3}}
    ).json()
    assert wrong['problems'] == [] and wrong['sample_errors'][0].startswith('$.severity: ')
    null = client.post('/api/output-schemas/check', headers=HEADERS, json={'schema': SCHEMA, 'sample': None}).json()
    assert null['sample_errors'] and null['sample_errors'][0].startswith('$: ')
    bad = client.post(
        '/api/output-schemas/check',
        headers=HEADERS,
        json={'schema': {**SCHEMA, 'additionalProperties': True}, 'sample': {}},
    ).json()
    assert bad['problems'] == ['$: an object must set additionalProperties to false'] and bad['sample_errors'] is None
