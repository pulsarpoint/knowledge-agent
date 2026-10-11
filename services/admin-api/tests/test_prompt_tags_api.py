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
    saved = client.put(f'{ENDPOINT}/{prompt}', headers=HEADERS,
                       json={'name': 'Runbook', 'tags': ['service', 'runbook'], 'content': 'Write a runbook.', 'revision': 1})
    assert saved.status_code == 200, saved.text
    detail = client.get(f'{ENDPOINT}/{prompt}', headers=HEADERS).json()
    assert detail['tags'] == ['service', 'runbook'] and len(detail['versions']) == 1


def test_bad_tags_and_input_kind_are_refused(client):
    assert message(client.post(ENDPOINT, headers=HEADERS, json={'name': 'X', 'tags': ['has space'], 'content': 'c'})) == (
        400, 'Tags use lowercase letters, digits and -, up to 40 characters.')
    assert message(client.post(ENDPOINT, headers=HEADERS, json={'name': 'X', 'tags': [f't{i}' for i in range(11)], 'content': 'c'})) == (
        400, 'Use at most 10 tags.')
    assert client.post(ENDPOINT, headers=HEADERS, json={'name': 'X', 'input_kind': 'code', 'content': 'c'}).status_code == 400


def test_filter_by_tag_and_tags_in_use(client):
    discovery = make(client, 'Discovery', ['server-discovery', 'ssh'])
    make(client, 'Another', ['ssh'])
    filtered = client.get(ENDPOINT, headers=HEADERS, params={'tag': 'server-discovery'}).json()['prompts']
    assert [row['id'] for row in filtered] == [discovery]
    tags = {row['tag']: row['count'] for row in client.get(f'{ENDPOINT}/tags', headers=HEADERS).json()['tags']}
    assert tags['ssh'] == 2 and tags['server-discovery'] == 1
    assert tags['documentation'] >= 1  # the seeded prompts of the test workspace
    ordered = [row['tag'] for row in client.get(f'{ENDPOINT}/tags', headers=HEADERS).json()['tags']]
    assert ordered == sorted(ordered)


def test_a_prompt_without_tags_gets_the_default(client):
    prompt = make(client, 'Plain', [])
    assert client.get(f'{ENDPOINT}/{prompt}', headers=HEADERS).json()['tags'] == ['documentation', 'mixed']
