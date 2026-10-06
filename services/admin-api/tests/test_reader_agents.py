"""The Reader assistant is chosen in admin and must satisfy the Knowledge tier."""
import pytest

from knowledge_admin.reader_policy import knowledge_violations
from test_agent_versions import draft
from test_api import _client
from test_profiles import create
from test_repositories import HEADERS


def base(**changes):
    config = {'enabled': True, 'model': {'enabled': True}, 'documents': {'enabled': True, 'grounded': True}, 'ssh': {'enabled': False},
              'mcp': {'enabled': False}, 'code': {'enabled': False}}
    for key, value in changes.items():
        config[key] = value
    return config


@pytest.mark.parametrize('changes, reason', [
    ({'ssh': {'enabled': True}}, 'uses SSH'),
    ({'mcp': {'enabled': True}}, 'uses MCP tools'),
    ({'code': {'enabled': True}}, 'uses code projects'),
    ({'documents': {'enabled': False}}, 'document search is off'),
    ({'documents': {'enabled': True}}, 'Answer only from documents is off'),
    ({'enabled': False}, 'agent is disabled'),
    ({'model': None}, 'no enabled model'),
    ({'model': {'enabled': False}}, 'no enabled model'),
])
def test_knowledge_violations(changes, reason):
    assert knowledge_violations(base()) == []
    assert reason in knowledge_violations(base(**changes))


@pytest.fixture
def admin(store, tmp_path, monkeypatch):
    monkeypatch.setenv('CODE_ROOT', str(tmp_path.resolve()))
    monkeypatch.setenv('DATA_ROOT_DIR', str(tmp_path.resolve()))
    client = _client(tmp_path, repository_store=store)
    model = client.post('/api/llm/models', headers=HEADERS, json={
        'name': 'Reader model', 'provider': 'openai', 'api_format': 'responses', 'model': 'reader-fixture',
        'base_url': 'http://model.test/v1', 'api_key': 'reader-model-secret'}).json()
    with store.connect() as conn:
        conn.execute('UPDATE ka.agents SET model_id=%s', (model['id'],))
    return client


def test_settings_default_to_search_and_list_candidates(admin):
    settings = admin.get('/api/reader/settings', headers=HEADERS).json()
    assert settings['knowledge_agent_id'] == 'search' and settings['revision'] == 1
    by_id = {row['id']: row for row in settings['candidates']}
    assert 'coordinator' not in by_id
    assert 'uses SSH' in by_id['operations']['violations']
    assert by_id['search']['violations'] == []


def test_save_rejects_violating_agent_and_stale_revision(admin, store):
    custom = create(admin, 'Docs helper')
    rejected = admin.put('/api/reader/settings', headers=HEADERS, json={'knowledge_agent_id': custom['id'], 'revision': 1})
    assert rejected.status_code == 400 and 'document search is off' in rejected.text
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET documents_enabled=true,documents_grounded=true,model_id=(SELECT model_id FROM ka.agents WHERE id='search') WHERE id=%s", (custom['id'],))
    saved = admin.put('/api/reader/settings', headers=HEADERS, json={'knowledge_agent_id': custom['id'], 'revision': 1})
    assert saved.status_code == 200 and saved.json()['knowledge_agent_id'] == custom['id'] and saved.json()['revision'] == 2
    stale = admin.put('/api/reader/settings', headers=HEADERS, json={'knowledge_agent_id': 'search', 'revision': 1})
    assert stale.status_code == 409
    missing = admin.put('/api/reader/settings', headers=HEADERS, json={'knowledge_agent_id': 'nope', 'revision': 2})
    assert missing.status_code == 400
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.audit_events WHERE action='reader.settings.updated'").fetchone()['n'] == 1


def test_reader_agent_cannot_be_deleted(admin, store):
    custom = create(admin, 'Docs helper')
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET documents_enabled=true,documents_grounded=true,model_id=(SELECT model_id FROM ka.agents WHERE id='search') WHERE id=%s", (custom['id'],))
    admin.put('/api/reader/settings', headers=HEADERS, json={'knowledge_agent_id': custom['id'], 'revision': 1})
    revision = admin.get(f"/api/agents/{custom['id']}", headers=HEADERS).json()['revision']
    response = admin.request('DELETE', f"/api/agents/{custom['id']}", headers=HEADERS,
                             json={'revision': revision, 'confirmed': True})
    assert response.status_code == 409
    assert response.json()['error']['message'] == 'This agent is the Reader assistant. Choose another Reader agent first.'


def test_settings_require_admin(admin):
    assert admin.get('/api/reader/settings').status_code == 403


def test_versioned_agent_whose_snapshot_predates_reader_settings_explains_the_upgrade(admin, store):
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET documents_grounded=false WHERE id='search'")
    draft(admin, 'search')
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET documents_grounded=true WHERE id='search'")
    by_id = {row['id']: row for row in admin.get('/api/reader/settings', headers=HEADERS).json()['candidates']}
    assert by_id['search']['violations'] == [
        'Answer only from documents is off', 'active version v1 predates these settings — save and activate a new version']


def test_disabled_or_modelless_agents_list_why_they_cannot_answer(admin, store):
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET enabled=false WHERE id='search'")
        conn.execute("UPDATE ka.agents SET model_id=NULL WHERE id='code'")
    by_id = {row['id']: row for row in admin.get('/api/reader/settings', headers=HEADERS).json()['candidates']}
    assert by_id['search']['violations'] == ['agent is disabled']
    assert 'no enabled model' in by_id['code']['violations']
