"""Administration → Memory API: search, read with history, create, edit, restore, delete, re-embed."""

import pytest
from knowledge_admin.memory import store as memory
from test_api import _client
from test_repositories import HEADERS


@pytest.fixture
def client(store, tmp_path, monkeypatch):
    monkeypatch.setattr('knowledge_admin.memory.api.current_model', lambda conn, project_id: None)
    return _client(tmp_path, repository_store=store)


def created(client, name='payments/deploy', content='Use make release.', metadata=None) -> dict:
    response = client.post(
        '/api/memories', headers=HEADERS, json={'name': name, 'metadata': metadata or {}, 'content': content}
    )
    assert response.status_code == 201, response.text
    return response.json()['memory']


def test_requires_the_admin_gateway(client):
    assert client.get('/api/memories').status_code == 403


def test_create_list_and_read_with_history(client):
    entry = created(client, metadata={'service': 'payments'})
    assert entry['updated_by_email'] == 'admin@example.test'
    listed = client.get('/api/memories', headers=HEADERS).json()
    assert [m['name'] for m in listed['memories']] == ['payments/deploy']
    assert (listed['not_embedded'], listed['embedding_model'], listed['next_offset']) == (1, False, None)
    assert listed['total'] == 1
    filtered = client.get('/api/memories', headers=HEADERS, params={'metadata': '{"service": "billing"}'}).json()
    assert filtered['memories'] == [] and filtered['total'] == 0
    found = client.get('/api/memories', headers=HEADERS, params={'query': 'release'}).json()
    assert [m['name'] for m in found['memories']] == ['payments/deploy'] and found['total'] == 1
    one = client.get(f'/api/memories/{entry["id"]}', headers=HEADERS).json()
    assert one['memory']['content'] == 'Use make release.' and one['versions'] == []


def test_total_counts_every_match_across_pages(client):
    for number in range(3):
        created(client, name=f'notes/n{number}', content=f'Release step {number}.')
    created(client, name='notes/other', content='Unrelated.')
    page = client.get('/api/memories', headers=HEADERS, params={'limit': 2}).json()
    assert len(page['memories']) == 2 and page['next_offset'] == 2 and page['total'] == 4
    found = client.get('/api/memories', headers=HEADERS, params={'query': 'release', 'limit': 1}).json()
    assert len(found['memories']) == 1 and found['total'] == 3
    # the admin UI's page sizes go up to 100
    assert client.get('/api/memories', headers=HEADERS, params={'limit': 100}).status_code == 200


def test_bad_metadata_query_is_400(client):
    assert client.get('/api/memories', headers=HEADERS, params={'metadata': '[1]'}).status_code == 400
    assert client.get('/api/memories', headers=HEADERS, params={'metadata': '{'}).status_code == 400


def test_create_conflicts_and_rules(client):
    created(client)
    again = client.post(
        '/api/memories', headers=HEADERS, json={'name': 'payments/deploy', 'metadata': {}, 'content': 'x'}
    )
    assert (
        again.status_code == 409
        and again.json()['error']['message'] == 'An entry named payments/deploy already exists.'
    )
    bad = client.post('/api/memories', headers=HEADERS, json={'name': 'Bad', 'metadata': {}, 'content': 'x'})
    assert bad.status_code == 400 and bad.json()['error']['message'].startswith('A name is')


def test_edit_rename_restore_and_delete(client):
    entry = created(client)
    url = f'/api/memories/{entry["id"]}'
    edited = client.put(
        url, headers=HEADERS, json={'name': 'payments/release', 'metadata': {}, 'content': 'New.', 'revision': 1}
    )
    assert edited.status_code == 200 and edited.json()['memory']['revision'] == 2
    stale = client.put(
        url, headers=HEADERS, json={'name': 'payments/release', 'metadata': {}, 'content': 'x', 'revision': 1}
    )
    assert stale.status_code == 409 and stale.json()['error']['message'] == 'This entry changed since you opened it.'
    restored = client.post(f'{url}/restore', headers=HEADERS, json={'revision': 1, 'current_revision': 2})
    assert restored.json()['memory']['name'] == 'payments/deploy' and restored.json()['memory']['revision'] == 3
    assert [v['revision'] for v in client.get(url, headers=HEADERS).json()['versions']] == [2, 1]
    assert client.delete(url, headers=HEADERS, params={'revision': 2}).status_code == 409
    assert client.delete(url, headers=HEADERS, params={'revision': 3}).status_code == 204
    assert client.get(url, headers=HEADERS).status_code == 404


def test_reembed_needs_a_model(client):
    created(client)
    response = client.post('/api/memories/re-embed', headers=HEADERS)
    assert (
        response.status_code == 409
        and response.json()['error']['message'] == 'Select an embedding model on the Memory page.'
    )


def test_ui_changes_record_events_with_the_person(client, store):
    created(client)
    with store.connect() as conn:
        event = conn.execute("SELECT payload FROM ka.events WHERE source='memory'").fetchone()
        audit = conn.execute("SELECT action FROM ka.audit_events WHERE resource_type='memory'").fetchone()
    assert event['payload']['actor'] is not None and event['payload']['agent_id'] is None
    assert audit['action'] == 'memory.created'


def test_metadata_values_must_be_text(client):
    body = {'name': 'a/b', 'metadata': {'n': 1}, 'content': 'x'}
    assert client.post('/api/memories', headers=HEADERS, json=body).status_code == 400
    body['metadata'] = [1]
    assert client.post('/api/memories', headers=HEADERS, json=body).status_code == 400


def test_current_profile_is_reported_for_the_embedded_badge(client, store, monkeypatch):
    entry = created(client)
    with store.connect() as conn:
        conn.execute("UPDATE ka.memories SET embedding=%s::public.vector,embedding_profile='old'", ('[1,0,0]',))
    assert client.get('/api/memories', headers=HEADERS).json()['current_profile'] is None
    monkeypatch.setattr('knowledge_admin.memory.api.current_model', lambda conn, project_id: object())
    monkeypatch.setattr('knowledge_admin.memory.api.embeddings.embedding_profile', lambda model: 'new')
    listed = client.get('/api/memories', headers=HEADERS).json()
    assert (listed['current_profile'], listed['not_embedded']) == ('new', 1)
    assert listed['memories'][0]['embedding_profile'] == 'old'
    one = client.get(f'/api/memories/{entry["id"]}', headers=HEADERS).json()
    assert (one['current_profile'], one['memory']['embedding_profile']) == ('new', 'old')


def test_settings_choose_the_embedding_model(store, tmp_path):
    from test_embedding_models import EMBEDDING
    from test_llm import create

    client = _client(tmp_path, repository_store=store)
    model = create(client, EMBEDDING)
    assert client.get('/api/memory/settings', headers=HEADERS).json() == {'embedding_model_id': None}
    saved = client.put('/api/memory/settings', headers=HEADERS, json={'embedding_model_id': model['id']})
    assert saved.status_code == 200 and saved.json() == {'embedding_model_id': model['id']}
    assert client.get('/api/memory/settings', headers=HEADERS).json() == {'embedding_model_id': model['id']}
    assert client.put('/api/memory/settings', headers=HEADERS, json={'embedding_model_id': None}).json() == {
        'embedding_model_id': None
    }


def test_settings_accept_only_embedding_models(store, tmp_path):
    from test_llm import OPENAI, create

    client = _client(tmp_path, repository_store=store)
    chat = create(client, OPENAI)
    response = client.put('/api/memory/settings', headers=HEADERS, json={'embedding_model_id': chat['id']})
    assert response.status_code == 400 and response.json()['error']['message'] == 'Select an embedding model.'
    assert client.get('/api/memory/settings').status_code == 403
