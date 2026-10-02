"""The Reader assistant is chosen in admin and must satisfy the Knowledge tier."""
import pytest

from knowledge_admin.reader_policy import knowledge_violations
from test_api import _client
from test_profiles import create
from test_repositories import HEADERS


def base(**changes):
    config = {'documents': {'enabled': True, 'grounded': True}, 'ssh': {'enabled': False},
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
])
def test_knowledge_violations(changes, reason):
    assert knowledge_violations(base()) == []
    assert reason in knowledge_violations(base(**changes))


@pytest.fixture
def admin(store, tmp_path, monkeypatch):
    monkeypatch.setenv('CODE_ROOT', str(tmp_path.resolve()))
    monkeypatch.setenv('RAG_SOURCE_ROOT', str(tmp_path.resolve()))
    return _client(tmp_path, repository_store=store)


def test_settings_default_to_search_and_list_candidates(admin):
    settings = admin.get('/api/reader/settings', headers=HEADERS).json()
    assert settings['knowledge_agent_id'] == 'search' and settings['revision'] == 1
    by_id = {row['id']: row for row in settings['candidates']}
    assert 'coordinator' not in by_id
    assert 'uses SSH' in by_id['operations']['violations']


def test_save_rejects_violating_agent_and_stale_revision(admin, store):
    custom = create(admin, 'Docs helper')
    rejected = admin.put('/api/reader/settings', headers=HEADERS, json={'knowledge_agent_id': custom['id'], 'revision': 1})
    assert rejected.status_code == 400 and 'document search is off' in rejected.text
    with store.connect() as conn:
        conn.execute('UPDATE ka.agents SET documents_enabled=true,documents_grounded=true WHERE id=%s', (custom['id'],))
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
        conn.execute('UPDATE ka.agents SET documents_enabled=true,documents_grounded=true WHERE id=%s', (custom['id'],))
    admin.put('/api/reader/settings', headers=HEADERS, json={'knowledge_agent_id': custom['id'], 'revision': 1})
    revision = admin.get(f"/api/agents/{custom['id']}", headers=HEADERS).json()['revision']
    response = admin.request('DELETE', f"/api/agents/{custom['id']}", headers=HEADERS,
                             json={'revision': revision, 'confirmed': True})
    assert response.status_code == 409
    assert response.json()['error']['message'] == 'This agent is the Reader assistant. Choose another Reader agent first.'


def test_settings_require_admin(admin):
    assert admin.get('/api/reader/settings').status_code == 403
