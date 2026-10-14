"""Administration → Prompts: tags on prompts, the tag filter and the tags in use."""

import pytest

from test_api import _client
from test_repositories import HEADERS

ENDPOINT = '/api/documentation/prompts'


@pytest.fixture
def client(store, tmp_path):
    return _client(tmp_path, repository_store=store)


def make(client, name, tags, content='Look around.'):
    response = client.post(ENDPOINT, headers=HEADERS, json={'name': name, 'tags': tags, 'content': content})
    assert response.status_code == 201, response.text
    return response.json()['id']


def message(response) -> tuple:
    return response.status_code, response.json()['error']['message']


def test_tags_are_normalised_saved_and_returned(client):
    prompt = make(client, 'Discovery', [' Server-Discovery ', 'server-discovery', 'SSH'])
    detail = client.get(f'{ENDPOINT}/{prompt}', headers=HEADERS).json()
    assert detail['tags'] == ['server-discovery', 'ssh'] and 'input_kind' not in detail
    listed = next(row for row in client.get(ENDPOINT, headers=HEADERS).json()['prompts'] if row['id'] == prompt)
    assert listed['tags'] == ['server-discovery', 'ssh'] and 'input_kind' not in listed


def test_changing_only_tags_adds_no_version(client):
    prompt = make(client, 'Runbook', ['service'], content='Write a runbook.')
    saved = client.put(
        f'{ENDPOINT}/{prompt}',
        headers=HEADERS,
        json={'name': 'Runbook', 'tags': ['service', 'runbook'], 'content': 'Write a runbook.', 'revision': 1},
    )
    assert saved.status_code == 200, saved.text
    detail = client.get(f'{ENDPOINT}/{prompt}', headers=HEADERS).json()
    assert detail['tags'] == ['service', 'runbook'] and len(detail['versions']) == 1


def test_bad_tags_and_input_kind_are_refused(client):
    assert message(
        client.post(ENDPOINT, headers=HEADERS, json={'name': 'X', 'tags': ['has space'], 'content': 'c'})
    ) == (400, 'Tags use lowercase letters, digits and -, up to 40 characters.')
    assert message(
        client.post(ENDPOINT, headers=HEADERS, json={'name': 'X', 'tags': [f't{i}' for i in range(11)], 'content': 'c'})
    ) == (400, 'Use at most 10 tags.')
    assert (
        client.post(ENDPOINT, headers=HEADERS, json={'name': 'X', 'input_kind': 'code', 'content': 'c'}).status_code
        == 400
    )


def test_filter_by_tag_and_tags_in_use(client):
    discovery = make(client, 'Filtered', ['alpha', 'beta'])
    make(client, 'Another', ['beta'])
    filtered = client.get(ENDPOINT, headers=HEADERS, params={'tag': 'alpha'}).json()['prompts']
    assert [row['id'] for row in filtered] == [discovery]
    tags = {row['tag']: row['count'] for row in client.get(f'{ENDPOINT}/tags', headers=HEADERS).json()['tags']}
    assert tags['beta'] == 2 and tags['alpha'] == 1
    assert tags['documentation'] >= 1  # the seeded prompts of the test workspace
    ordered = [row['tag'] for row in client.get(f'{ENDPOINT}/tags', headers=HEADERS).json()['tags']]
    assert ordered == sorted(ordered, key=lambda tag: tag.replace('-', ''))  # the database collation ignores hyphens


def test_a_prompt_without_tags_gets_the_default(client):
    prompt = make(client, 'Plain', [])
    assert client.get(f'{ENDPOINT}/{prompt}', headers=HEADERS).json()['tags'] == ['documentation', 'mixed']


def test_the_create_audit_logs_the_stored_tags(client, store):
    prompt = make(client, 'Audited', [])
    with store.connect() as conn:
        details = conn.execute(
            "SELECT details FROM ka.audit_events WHERE resource_id=%s AND action='documentation_prompt.saved'",
            (prompt,),
        ).fetchone()['details']
    assert details['tags'] == ['documentation', 'mixed']


def test_updating_to_empty_tags_stores_no_tags(client):
    prompt = make(client, 'Untag', ['ssh'])
    saved = client.put(
        f'{ENDPOINT}/{prompt}',
        headers=HEADERS,
        json={'name': 'Untag', 'tags': [], 'content': 'Look around.', 'revision': 1},
    )
    assert saved.status_code == 200, saved.text
    assert client.get(f'{ENDPOINT}/{prompt}', headers=HEADERS).json()['tags'] == []


def test_a_whitespace_only_tag_is_refused(client):
    assert message(client.post(ENDPOINT, headers=HEADERS, json={'name': 'X', 'tags': ['   '], 'content': 'c'})) == (
        400,
        'Tags use lowercase letters, digits and -, up to 40 characters.',
    )


def test_tags_in_use_count_only_this_projects_prompts(client, store):
    make(client, 'Mine', ['beta'])
    with store.connect() as conn:
        foreign = store.project(conn, 'foreign')
        conn.execute(
            "INSERT INTO ka.documentation_prompts(project_id,name,tags) VALUES (%s,'Theirs','{beta,foreign-only}')",
            (foreign,),
        )
    tags = {row['tag']: row['count'] for row in client.get(f'{ENDPOINT}/tags', headers=HEADERS).json()['tags']}
    assert tags['beta'] == 1 and 'foreign-only' not in tags
