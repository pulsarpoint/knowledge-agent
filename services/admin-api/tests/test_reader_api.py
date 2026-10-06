"""Reader conversations run through the shared turn service on the internal network only."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agents.usage import Usage

from knowledge_admin.reader_api import narrow
from test_agent_versions import draft
from test_api import _client
from test_sdk_agent import responses_api
from test_repositories import HEADERS

KEY = 'reader-test-key'


def reader_headers(user='alice'):
    return {'X-Reader-Key': KEY, 'X-Reader-User': user, 'X-Reader-Email': f'{user}@example.test'}


def sse(response):
    events, name = [], None
    for line in response.text.splitlines():
        if line.startswith('event: '):
            name = line[7:]
        elif line.startswith('data: '):
            events.append((name, json.loads(line[6:])))
    return events


@pytest.fixture
def reader(store, tmp_path, monkeypatch, responses_api):
    monkeypatch.setenv('CODE_ROOT', str(tmp_path.resolve()))
    monkeypatch.setenv('DATA_ROOT_DIR', str(tmp_path.resolve()))
    monkeypatch.setenv('READER_AGENT_KEY', KEY)
    client = _client(tmp_path, repository_store=store)
    model = client.post('/api/llm/models', headers=HEADERS, json={
        'name': 'Reader model', 'provider': 'openai', 'api_format': 'responses', 'model': 'reader-fixture',
        'base_url': responses_api[0], 'api_key': 'reader-model-secret'}).json()
    with store.connect() as conn:
        conn.execute('''UPDATE ka.agents SET model_id=%s,enabled=true,documents_enabled=true,documents_grounded=true,
            all_collections=true,repositories_enabled=false,ssh_enabled=false,mcp_enabled=false WHERE id='search' ''', (model['id'],))
    seen = []
    async def fake(config, task, **kwargs):
        seen.append(config)
        state = {'excerpts': [{'n': 1, 'path': 'retries.md', 'collection': 'payments', 'text': 'Three.'}],
                 'searches': 1, 'empty_searches': 0, 'reported': None}
        return SimpleNamespace(final_output='Three retries [1].',
            context_wrapper=SimpleNamespace(usage=Usage(), context={'grounding': state}),
            to_input_list=lambda: [*kwargs.get('history', []), {'role': 'user', 'content': task},
                                   {'role': 'assistant', 'content': 'Three retries [1].'}])
    monkeypatch.setattr('knowledge_admin.agent_sessions.agent', fake)
    return client, seen


def ask(client, user='alice', **body):
    return client.post('/internal/reader/runs', headers=reader_headers(user), json={'question': 'How many retries?', **body})


def test_key_is_required_and_routes_are_not_under_api(reader, monkeypatch):
    client, _ = reader
    assert client.get('/internal/reader/sessions', headers={**reader_headers(), 'X-Reader-Key': 'wrong'}).status_code == 401
    assert client.get('/internal/reader/sessions', headers={'X-Reader-User': 'alice'}).status_code == 401
    assert client.get('/internal/reader/sessions', headers={'X-Reader-Key': KEY}).status_code == 403
    assert client.get('/api/internal/reader/sessions', headers={**HEADERS, **reader_headers()}).status_code == 404
    monkeypatch.delenv('READER_AGENT_KEY')
    assert client.get('/internal/reader/sessions', headers=reader_headers()).status_code == 503


def test_new_conversation_streams_session_activity_and_cited_answer(reader, store):
    client, _ = reader
    response = ask(client)
    assert response.status_code == 200, response.text
    events = sse(response)
    assert events[0][0] == 'session'
    completed = events[-1]
    assert completed[0] == 'completed' and completed[1]['status'] == 'completed'
    assert completed[1]['grounding']['sources'][0]['path'] == 'payments/retries.md'
    session_id = events[0][1]['id']
    with store.connect() as conn:
        row = conn.execute('''SELECT s.channel,s.is_test,s.idle_timeout,p.email FROM ka.agent_sessions s
            JOIN ka.principals p ON p.id=s.actor_id WHERE s.id=%s''', (session_id,)).fetchone()
    assert row['channel'] == 'reader' and row['is_test'] is False and row['idle_timeout'].days == 7
    assert row['email'] == 'alice@example.test'
    assert 'reader-model-secret' not in response.text


def test_follow_up_list_transcript_and_owner_isolation(reader):
    client, _ = reader
    session_id = sse(ask(client))[0][1]['id']
    follow = sse(ask(client, session_id=session_id, question='And after that?'))
    assert follow[0][0] != 'session' and follow[-1][1]['status'] == 'completed'
    sessions = client.get('/internal/reader/sessions', headers=reader_headers()).json()['sessions']
    assert [row['id'] for row in sessions] == [session_id]
    transcript = client.get(f'/internal/reader/sessions/{session_id}', headers=reader_headers()).json()
    assert [m['role'] for m in transcript['messages']] == ['user', 'assistant', 'user', 'assistant']
    assert transcript['messages'][1]['sources'][0]['n'] == 1 and transcript['messages'][1]['not_covered'] is False
    assert client.get('/internal/reader/sessions', headers=reader_headers('bob')).json()['sessions'] == []
    assert client.get(f'/internal/reader/sessions/{session_id}', headers=reader_headers('bob')).status_code == 404
    assert ask(client, user='bob', session_id=session_id).status_code == 404
    assert client.delete(f'/internal/reader/sessions/{session_id}', headers=reader_headers('bob')).status_code == 404


def test_delete_hides_from_owner_but_admin_keeps_transcript(reader):
    client, _ = reader
    session_id = sse(ask(client))[0][1]['id']
    assert client.delete(f'/internal/reader/sessions/{session_id}', headers=reader_headers()).status_code == 204
    assert client.get('/internal/reader/sessions', headers=reader_headers()).json()['sessions'] == []
    assert client.get(f'/api/agents/search/sessions/{session_id}', headers=HEADERS).status_code == 200


def test_tier_and_configuration_errors(reader, store):
    client, _ = reader
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET ssh_enabled=true WHERE id='search'")
    response = ask(client)
    assert response.status_code == 409
    assert response.json()['error']['message'] == "The assistant's configuration isn't allowed in the Reader."
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET ssh_enabled=false,model_id=NULL WHERE id='search'")
    assert ask(client).json()['error']['message'] == "The assistant isn't configured yet. Ask an administrator."


def test_ended_conversation(reader, store):
    client, _ = reader
    session_id = sse(ask(client))[0][1]['id']
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET status='expired' WHERE id=%s", (session_id,))
    response = ask(client, session_id=session_id)
    assert response.status_code == 409
    assert response.json()['error']['message'] == 'This conversation has ended. Start a new one.'


def publish_code(store):
    with store.connect() as conn:
        conn.execute("""UPDATE ka.agents SET documents_grounded=true,documents_enabled=true,repositories_enabled=false,all_collections=true,ssh_enabled=false,mcp_enabled=false,
            reader_enabled=true,model_id=(SELECT model_id FROM ka.agents WHERE id='search') WHERE id='code'""")


def test_publishing_another_agent_keeps_existing_conversations(reader, store):
    client, seen = reader
    session_id = sse(ask(client))[0][1]['id']
    publish_code(store)
    follow = sse(ask(client, session_id=session_id, question='Again?'))
    assert follow[-1][1]['status'] == 'completed' and seen[-1]['id'] == 'search'
    assert [row['id'] for row in client.get('/internal/reader/sessions', headers=reader_headers()).json()['sessions']] == [session_id]


def test_new_conversation_uses_the_requested_published_agent(reader, store):
    client, seen = reader
    publish_code(store)
    response = ask(client, agent_id='code')
    assert response.status_code == 200, response.text
    assert sse(response)[-1][1]['status'] == 'completed' and seen[-1]['id'] == 'code'
    assert ask(client, agent_id='operations').json()['error']['message'] == "The assistant's configuration isn't allowed in the Reader."
    assert ask(client, agent_id='missing').json()['error']['message'] == "The assistant's configuration isn't allowed in the Reader."


def test_unpublishing_stops_new_and_existing_conversations(reader, store):
    client, _ = reader
    session_id = sse(ask(client))[0][1]['id']
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET reader_enabled=false WHERE id='search'")
    assert ask(client, session_id=session_id).json()['error']['message'] == "The assistant's configuration isn't allowed in the Reader."
    assert ask(client).json()['error']['message'] == "The assistant isn't configured yet. Ask an administrator."


def test_reader_narrows_to_selected_collections_and_never_broadens():
    config = {'documents': {'enabled': True, 'all_collections': False, 'collections': [
        {'id': '1', 'name': 'docs', 'description': '', 'path': '/work/rag-data/docs'},
        {'id': '2', 'name': 'jira', 'description': '', 'path': '/work/rag-data/jira'}]}}
    narrowed = narrow(config, ['jira', 'secret'])
    assert [c['name'] for c in narrowed['documents']['collections']] == ['jira']
    assert narrowed['documents']['all_collections'] is False
    assert narrow(config, None) is config


def test_narrow_all_collections_agent_uses_the_selection():
    config = {'documents': {'enabled': True, 'all_collections': True, 'collections': []}}
    narrowed = narrow(config, ['jira', 'docs'])
    assert narrowed['documents']['all_collections'] is False
    assert narrowed['documents']['collections'] == [
        {'name': 'docs', 'description': '', 'paths': []}, {'name': 'jira', 'description': '', 'paths': []}]


def test_transcript_reports_collections(reader):
    client, _ = reader
    session_id = sse(ask(client))[0][1]['id']
    transcript = client.get(f'/internal/reader/sessions/{session_id}', headers=reader_headers()).json()
    assert transcript['collections'] is None and 'folders' not in transcript and 'include_subfolders' not in transcript


def test_reader_answers_with_the_active_version_snapshot(reader, store):
    client, seen = reader
    draft(client, 'search')
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET ssh_enabled=true WHERE id='search'")
    events = sse(ask(client))
    assert events[-1][1]['status'] == 'completed'
    assert seen[-1]['config_version']['version'] == 1 and seen[-1]['ssh']['enabled'] is False


def test_ungrounded_active_version_is_not_allowed(reader, store):
    client, seen = reader
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET documents_grounded=false WHERE id='search'")
    draft(client, 'search')
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET documents_grounded=true WHERE id='search'")
    response = ask(client)
    assert response.status_code == 409
    assert response.json()['error']['message'] == "The assistant's configuration isn't allowed in the Reader."
    assert seen == []


def test_tool_activity_maps_to_reader_activity(reader, monkeypatch):
    client, _ = reader
    async def acting(config, task, *, on_activity, **kwargs):
        await on_activity({'id': 'r:1', 'kind': 'message', 'title': 'Thinking', 'detail': '', 'status': 'inProgress'})
        await on_activity({'id': 'r:2', 'kind': 'tool', 'title': 'search_documents', 'detail': '{"query":"retries"}', 'status': 'inProgress'})
        await on_activity({'id': 'r:2', 'kind': 'tool', 'title': 'search_documents', 'detail': '[]', 'status': 'completed'})
        return SimpleNamespace(final_output='Not documented.',
            context_wrapper=SimpleNamespace(usage=Usage(), context={'grounding': {'excerpts': [], 'searches': 1, 'empty_searches': 1, 'reported': None}}),
            to_input_list=lambda: [{'role': 'user', 'content': task}, {'role': 'assistant', 'content': 'Not documented.'}])
    monkeypatch.setattr('knowledge_admin.agent_sessions.agent', acting)
    events = sse(ask(client))
    assert [event for event in events if event[0] == 'activity'] == [
        ('activity', {'tool': 'search_documents', 'argument': '{"query":"retries"}'})]
    assert events[-1][0] == 'completed'


@pytest.mark.parametrize('change', [
    "UPDATE ka.agents SET enabled=false WHERE id='search'",
    "UPDATE ka.llm_models SET enabled=false",
])
def test_follow_up_after_agent_or_model_disabled_is_not_configured(reader, store, change):
    client, _ = reader
    session_id = sse(ask(client))[0][1]['id']
    with store.connect() as conn:
        conn.execute(change)
    response = ask(client, session_id=session_id, question='And after that?')
    assert response.status_code == 409
    assert response.json()['error']['message'] == "The assistant isn't configured yet. Ask an administrator."
