"""Skill lifecycle and authorization against a disposable PostgreSQL database."""

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest

from test_api import _client

HEADERS = {'X-Forwarded-Groups': 'docs-admins', 'X-Forwarded-User': 'admin', 'X-Forwarded-Email': 'admin@example.test'}
VALUE = {
    'name': 'inspect-service-logs',
    'description': 'Use when investigating a service failure.',
    'instructions': '# Inspect logs\n\n1. Identify the service.\n2. Read recent errors.\n',
}


def test_skill_lifecycle_preserves_markdown_and_audits_changes(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    response = client.post('/api/skills', headers=HEADERS, json=VALUE)
    assert response.status_code == 201, response.text
    original = response.json()
    assert original['enabled'] is True and original['revision'] == 1
    assert original['instructions'] == VALUE['instructions']
    endpoint = f'/api/skills/{original["id"]}'
    assert client.get(endpoint, headers=HEADERS).json() == original
    listing = client.get('/api/skills', headers=HEADERS).json()['skills']
    assert [row['name'] for row in listing] == ['api-analysis', VALUE['name']]
    assert 'instructions' not in listing[0]
    disabled = client.patch(endpoint + '/status', headers=HEADERS, json={'enabled': False, 'revision': 1})
    assert disabled.status_code == 200 and disabled.json()['revision'] == 2
    assert [row['name'] for row in client.get('/api/skills?enabled=true', headers=HEADERS).json()['skills']] == [
        'api-analysis'
    ]
    assert len(client.get('/api/skills?enabled=false', headers=HEADERS).json()['skills']) == 1
    assert client.get(endpoint, headers=HEADERS).json()['instructions'] == VALUE['instructions']
    updated = client.put(
        endpoint,
        headers=HEADERS,
        json={**VALUE, 'description': 'Updated scope', 'instructions': '  indented Markdown\n\n', 'revision': 2},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()['enabled'] is False  # Editing text must not implicitly re-enable it.
    assert updated.json()['revision'] == 3 and updated.json()['instructions'] == '  indented Markdown\n\n'
    assert updated.json()['created_at'] == original['created_at']
    assert updated.json()['updated_at'] > original['updated_at']
    enabled = client.patch(endpoint + '/status', headers=HEADERS, json={'enabled': True, 'revision': 3})
    assert enabled.status_code == 200 and enabled.json()['revision'] == 4
    assert (
        client.patch(endpoint + '/status', headers=HEADERS, json={'enabled': True, 'revision': 4}).json()
        == enabled.json()
    )
    with store.connect() as conn:
        events = conn.execute(
            'SELECT action,details FROM ka.audit_events WHERE resource_id=%s ORDER BY id', (UUID(original['id']),)
        ).fetchall()
        assert conn.execute('SELECT count(*) AS n FROM ka.jobs').fetchone()['n'] == 0
    assert [event['action'] for event in events] == [
        'skill.created',
        'skill.disabled',
        'skill.updated',
        'skill.enabled',
    ]
    assert 'instructions' not in json.dumps(events)
    assert client.delete(endpoint, headers=HEADERS).status_code == 405


def test_duplicate_and_stale_edits_are_atomic(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    first = client.post('/api/skills', headers=HEADERS, json=VALUE).json()
    second = client.post('/api/skills', headers=HEADERS, json={**VALUE, 'name': 'review-health'}).json()
    assert client.post('/api/skills', headers=HEADERS, json=VALUE).status_code == 409
    assert client.put('/api/skills/' + second['id'], headers=HEADERS, json={**VALUE, 'revision': 1}).status_code == 409
    assert client.get('/api/skills/' + second['id'], headers=HEADERS).json() == second
    endpoint = '/api/skills/' + first['id']
    assert client.put(endpoint, headers=HEADERS, json=VALUE).status_code == 409
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: client.patch(endpoint + '/status', headers=HEADERS, json={'enabled': False, 'revision': 1}),
                range(2),
            )
        )
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert client.put(endpoint, headers=HEADERS, json={**VALUE, 'revision': 1}).status_code == 409
    saved = client.get(endpoint, headers=HEADERS).json()
    assert saved['enabled'] is False and saved['revision'] == 2 and saved['instructions'] == VALUE['instructions']


@pytest.mark.parametrize(
    'changes',
    [
        {'name': ''},
        {'name': '../escape'},
        {'name': 'Bad Name'},
        {'name': 'name--dash'},
        {'name': 'a' * 65},
        {'description': '   '},
        {'description': 'a' * 1025},
        {'description': 'null\x00byte'},
        {'instructions': '\n\t '},
        {'instructions': 'a' * 100001},
        {'instructions': 'null\x00byte'},
        {'enabled': 'false'},
        {'enabled': 1},
        {'revision': True},
        {'revision': 1},
        {'unsupported': True},
    ],
)
def test_invalid_skills_are_rejected_before_persistence(store, tmp_path, changes):
    client = _client(tmp_path, repository_store=store)
    response = client.post('/api/skills', headers=HEADERS, json={**VALUE, **changes})
    assert response.status_code == 400, response.text
    assert [row['name'] for row in client.get('/api/skills', headers=HEADERS).json()['skills']] == ['api-analysis']


@pytest.mark.parametrize('groups', ['', 'readers', 'docs-admins-other'])
def test_skill_routes_require_exact_admin_group(tmp_path, groups):
    client = _client(tmp_path)
    endpoint = '/api/skills/' + str(uuid4())
    headers = {**HEADERS, 'X-Forwarded-Groups': groups}
    for method, path, value in [
        ('GET', '/api/skills', None),
        ('GET', endpoint, None),
        ('POST', '/api/skills', VALUE),
        ('PUT', endpoint, {**VALUE, 'revision': 1}),
        ('PATCH', endpoint + '/status', {'revision': 1, 'enabled': False}),
    ]:
        assert client.request(method, path, headers=headers, json=value).status_code == 403


def test_identity_storage_and_workspace_boundaries(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    assert client.post('/api/skills', headers={'X-Forwarded-Groups': 'docs-admins'}, json=VALUE).status_code == 403
    assert _client(tmp_path).get('/api/skills', headers=HEADERS).status_code == 503
    skill = client.post('/api/skills', headers=HEADERS, json=VALUE).json()
    endpoint = '/api/skills/' + skill['id']
    with store.connect() as conn:
        other = store.project(conn, 'other-workspace')
        conn.execute('UPDATE ka.agent_skills SET project_id=%s WHERE id=%s', (other, UUID(skill['id'])))
    assert [row['name'] for row in client.get('/api/skills', headers=HEADERS).json()['skills']] == ['api-analysis']
    assert client.get(endpoint, headers=HEADERS).status_code == 404
    assert client.put(endpoint, headers=HEADERS, json={**VALUE, 'revision': 1}).status_code == 404
    assert (
        client.patch(endpoint + '/status', headers=HEADERS, json={'enabled': False, 'revision': 1}).status_code == 404
    )


@pytest.mark.parametrize(
    'value',
    [
        {'enabled': 'false', 'revision': 1},
        {'enabled': False},
        {'enabled': False, 'revision': True},
        {'enabled': False, 'revision': 1, 'instructions': 'overwrite'},
    ],
)
def test_status_is_a_strict_revision_checked_operation(store, tmp_path, value):
    client = _client(tmp_path, repository_store=store)
    skill = client.post('/api/skills', headers=HEADERS, json=VALUE).json()
    endpoint = '/api/skills/' + skill['id']
    assert client.patch(endpoint + '/status', headers=HEADERS, json=value).status_code == 400
    assert client.get(endpoint, headers=HEADERS).json() == skill
