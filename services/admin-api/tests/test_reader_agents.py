"""Any agent that meets the Knowledge tier can be published to Reader users."""
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


def save(client, agent_id, **changes):
    profile = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()
    body = {key: profile[key] for key in ('revision', 'name', 'description', 'when_to_use', 'enabled', 'all_collections', 'all_repositories',
        'collection_ids', 'repository_ids', 'service_ids', 'documents_enabled', 'documents_grounded', 'repositories_enabled', 'ssh_enabled',
        'skills_enabled', 'mcp_enabled', 'services_enabled')}
    return client.put(f'/api/agents/{agent_id}', headers=HEADERS, json={**body, **changes})


def listed(client):
    return {row['id']: row for row in client.get('/api/agents', headers=HEADERS).json()['agents']}


def test_search_starts_published_and_ready(admin):
    agents = listed(admin)
    assert agents['search']['reader_enabled'] is True and agents['search']['reader_violations'] == []
    assert agents['operations']['reader_enabled'] is False and agents['operations']['reader_violations'] == []


def test_publishing_requires_the_reader_rules(admin, store):
    custom = create(admin, 'Docs helper')
    rejected = save(admin, custom['id'], reader_enabled=True)
    assert rejected.status_code == 400 and 'document search is off' in rejected.text
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET model_id=NULL WHERE id=%s", (custom['id'],))
    modelless = save(admin, custom['id'], reader_enabled=True, documents_enabled=True, documents_grounded=True)
    assert modelless.status_code == 400 and 'no enabled model' in modelless.text
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET model_id=(SELECT model_id FROM ka.agents WHERE id='search') WHERE id=%s", (custom['id'],))
    published = save(admin, custom['id'], reader_enabled=True, documents_enabled=True, documents_grounded=True)
    assert published.status_code == 200 and published.json()['reader_enabled'] is True
    assert listed(admin)[custom['id']]['reader_enabled'] is True and listed(admin)['search']['reader_enabled'] is True


@pytest.mark.parametrize('changes, reason', [
    ({'ssh_enabled': True}, 'uses SSH'),
    ({'mcp_enabled': True}, 'uses MCP tools'),
    ({'repositories_enabled': True}, 'uses code projects'),
    ({'documents_grounded': False}, 'Answer only from documents is off'),
])
def test_published_agent_cannot_gain_capabilities(admin, changes, reason):
    response = save(admin, 'search', **changes)
    assert response.status_code == 400 and reason in response.text
    assert 'Turn off Available in Reader' in response.text
    assert save(admin, 'search', reader_enabled=False, **changes).status_code == 200


def test_published_agent_without_a_model_can_still_be_edited(admin, store):
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET model_id=NULL WHERE id='search'")
    assert save(admin, 'search', description='Docs answers').status_code == 200
    assert listed(admin)['search']['reader_violations'] == ['no enabled model']


def test_published_agent_can_be_deleted(admin, store):
    custom = create(admin, 'Docs helper')
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET documents_enabled=true,documents_grounded=true,reader_enabled=true WHERE id=%s", (custom['id'],))
    revision = admin.get(f"/api/agents/{custom['id']}", headers=HEADERS).json()['revision']
    response = admin.request('DELETE', f"/api/agents/{custom['id']}", headers=HEADERS, json={'revision': revision, 'confirmed': True})
    assert response.status_code == 204


def test_reader_settings_api_is_gone(admin):
    assert admin.get('/api/reader/settings', headers=HEADERS).status_code == 404


def test_versioned_agent_whose_snapshot_predates_reader_rules_explains_the_upgrade(admin, store):
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET documents_grounded=false WHERE id='search'")
    draft(admin, 'search')
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET documents_grounded=true WHERE id='search'")
    assert listed(admin)['search']['reader_violations'] == [
        'Answer only from documents is off', 'active version v1 predates these settings — save and activate a new version']


def test_disabled_published_agent_lists_why_it_cannot_answer(admin, store):
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET enabled=false WHERE id='search'")
    assert listed(admin)['search']['reader_violations'] == ['agent is disabled']
