from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from knowledge_llm.prompts import DEFAULT_PROMPTS, active_prompt
from test_api import _client
from test_llm import HEADERS, use_database

BASE = '/api/llm/prompts'


def draft(client, content='Answer briefly.', agent='search'):
    response = client.post(f'{BASE}/{agent}/versions', headers=HEADERS,
                           json={'title': 'Short answers', 'content': content})
    assert response.status_code == 201, response.text
    return response.json()


def test_draft_publish_history_and_runtime_selection(store, tmp_path, monkeypatch):
    client = _client(tmp_path, repository_store=store)
    use_database(monkeypatch, store)
    initial = client.get(BASE, headers=HEADERS).json()
    assert len(initial['agents']) == 4
    assert all(row['status'] == 'active' for row in initial['versions'])
    assert active_prompt('search') == DEFAULT_PROMPTS['search']
    assert active_prompt('code') == DEFAULT_PROMPTS['code']
    original = next(row for row in initial['versions'] if row['agent'] == 'search')
    saved = draft(client)
    assert saved['status'] == 'draft' and saved['version'] == 2
    assert active_prompt('search') == original['content']
    path = f"{BASE}/search/versions/{saved['id']}"
    changed = client.put(path, headers=HEADERS, json={'title': 'Direct answers', 'content': 'Start with the answer.', 'revision': 1})
    assert changed.status_code == 200
    assert active_prompt('search') == original['content']
    stale = client.post(path + '/activate', headers=HEADERS, json={'revision': 1, 'prompt_revision': 1})
    assert stale.status_code == 409
    activated = client.post(path + '/activate', headers=HEADERS, json={'revision': 2, 'prompt_revision': 1})
    assert activated.status_code == 200 and activated.json()['status'] == 'active'
    assert active_prompt('search') == 'Start with the answer.'
    assert active_prompt('code') == DEFAULT_PROMPTS['code']
    immutable = client.put(path, headers=HEADERS, json={'title': 'Change live prompt', 'content': 'Wrong', 'revision': 2})
    assert immutable.status_code == 409
    with store.connect() as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute('UPDATE ka.agent_prompt_versions SET content=%s WHERE id=%s', ('Wrong', saved['id']))
    rollback = client.post(f"{BASE}/search/versions/{original['id']}/activate", headers=HEADERS,
                           json={'revision': original['revision'], 'prompt_revision': 2})
    assert rollback.status_code == 200
    assert active_prompt('search') == original['content']
    versions = client.get(BASE, headers=HEADERS).json()['versions']
    assert next(row for row in versions if row['id'] == saved['id'])['status'] == 'archived'
    with store.connect() as conn:
        events = conn.execute("SELECT action,details FROM ka.audit_events WHERE resource_type='agent_prompt'").fetchall()
    assert len(events) == 4
    assert all('content' not in event['details'] for event in events)


def test_activation_is_atomic_and_checks_agent_and_concurrent_revision(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    drafts = [draft(client, text) for text in ['First instructions', 'Second instructions']]
    wrong = client.post(f"{BASE}/code/versions/{drafts[0]['id']}/activate", headers=HEADERS,
                        json={'revision': 1, 'prompt_revision': 1})
    assert wrong.status_code == 404

    def activate(row):
        return client.post(f"{BASE}/search/versions/{row['id']}/activate", headers=HEADERS,
                           json={'revision': 1, 'prompt_revision': 1}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(activate, drafts)) == [200, 409]
    versions = client.get(BASE, headers=HEADERS).json()['versions']
    assert len([v for v in versions if v['agent'] == 'search' and v['status'] == 'active']) == 1


@pytest.mark.parametrize('groups', ['', 'readers', 'docs-admins-other'])
def test_prompt_administration_requires_admin(store, tmp_path, groups):
    client = _client(tmp_path, repository_store=store)
    headers = {**HEADERS, 'X-Forwarded-Groups': groups}
    assert client.get(BASE, headers=headers).status_code == 403
    assert client.post(BASE + '/search/versions', headers=headers, json={'title': 'Draft', 'content': 'Text'}).status_code == 403
    assert client.request('DELETE', BASE + '/search/versions/4c1e1d5a-509a-4da4-ab90-a28c4d686501',
                          headers=headers, json={'revision': 1, 'prompt_revision': 1, 'confirmed': True}).status_code == 403


def test_stale_and_empty_drafts_cannot_replace_saved_content(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    saved = draft(client)
    path = f"{BASE}/search/versions/{saved['id']}"
    assert client.put(path, headers=HEADERS, json={'revision': 2, 'title': 'New', 'content': 'New'}).status_code == 409
    assert client.put(path, headers=HEADERS, json={'revision': 1, 'title': 'New', 'content': '  '}).status_code == 400
    assert client.post(BASE + '/search/versions', headers=HEADERS, json={'title': 'Bad', 'content': 'Text', 'status': 'active'}).status_code == 400


def test_delete_draft_and_archived_versions_preserves_active_prompt_and_numbering(store, tmp_path, monkeypatch):
    client = _client(tmp_path, repository_store=store)
    use_database(monkeypatch, store)
    saved = draft(client)
    path = f"{BASE}/search/versions/{saved['id']}"
    assert client.request('DELETE', path, headers=HEADERS,
                          json={'revision': 1, 'prompt_revision': 1, 'confirmed': True}).status_code == 204
    assert active_prompt('search') == DEFAULT_PROMPTS['search']
    next_draft = draft(client, 'New active prompt.')
    assert next_draft['version'] == 3
    assert client.post(f"{BASE}/search/versions/{next_draft['id']}/activate", headers=HEADERS,
                       json={'revision': 1, 'prompt_revision': 1}).status_code == 200
    archived = next(row for row in client.get(BASE, headers=HEADERS).json()['versions']
                    if row['agent'] == 'search' and row['status'] == 'archived')
    archived_path = f"{BASE}/search/versions/{archived['id']}"
    assert client.request('DELETE', archived_path, headers=HEADERS,
                          json={'revision': 1, 'prompt_revision': 2, 'confirmed': True}).status_code == 204
    assert active_prompt('search') == 'New active prompt.'
    assert active_prompt('code') == DEFAULT_PROMPTS['code']
    assert client.post(archived_path + '/activate', headers=HEADERS,
                       json={'revision': 1, 'prompt_revision': 2}).status_code == 404
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS total FROM ka.agent_prompt_versions').fetchone()['total'] == 4
        events = conn.execute("SELECT details FROM ka.audit_events WHERE action='prompt.deleted'").fetchall()
        assert {event['details']['version'] for event in events} == {1, 2}
        assert all('content' not in event['details'] for event in events)


def test_delete_requires_confirmation_current_revisions_and_matching_agent(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    saved = draft(client)
    path = f"{BASE}/search/versions/{saved['id']}"
    payload = {'revision': 1, 'prompt_revision': 1, 'confirmed': True}
    for changed, status in [({'confirmed': False}, 400), ({'confirmed': 'true'}, 400),
                            ({'revision': 2}, 409), ({'prompt_revision': 2}, 409)]:
        assert client.request('DELETE', path, headers=HEADERS, json={**payload, **changed}).status_code == status
    assert client.request('DELETE', path, headers=HEADERS, json={'revision': 1, 'prompt_revision': 1}).status_code == 400
    assert client.request('DELETE', path.replace('/search/', '/code/'), headers=HEADERS, json=payload).status_code == 404
    assert any(row['id'] == saved['id'] for row in client.get(BASE, headers=HEADERS).json()['versions'])


def test_active_version_is_protected_by_api_and_database(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    for row in client.get(BASE, headers=HEADERS).json()['versions']:
        assert client.request('DELETE', f"{BASE}/{row['agent']}/versions/{row['id']}", headers=HEADERS,
                              json={'revision': 1, 'prompt_revision': 1, 'confirmed': True}).status_code == 409
        with store.connect() as conn:
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute('DELETE FROM ka.agent_prompt_versions WHERE id=%s', (row['id'],))


def test_delete_and_activate_cannot_both_succeed(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    saved = draft(client)
    path = f"{BASE}/search/versions/{saved['id']}"

    def delete():
        return client.request('DELETE', path, headers=HEADERS,
                              json={'revision': 1, 'prompt_revision': 1, 'confirmed': True}).status_code

    def activate():
        return client.post(path + '/activate', headers=HEADERS,
                           json={'revision': 1, 'prompt_revision': 1}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        deleted, activated = pool.submit(delete), pool.submit(activate)
        assert (deleted.result(), activated.result()) in {(204, 404), (409, 200)}
    rows = client.get(BASE, headers=HEADERS).json()['versions']
    assert len([row for row in rows if row['agent'] == 'search' and row['status'] == 'active']) == 1
