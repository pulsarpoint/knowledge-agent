"""Interactive Claude Code sessions: the context snapshot, queued messages, Stop, Close and idle closing."""
import json
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from knowledge_admin import claude_code_api
from knowledge_admin.claude_code import FINAL_ANSWER, IDLE_CLOSED, SYSTEM_PROMPT
from knowledge_admin.documentation_generation import PROMPT_CHANGED, SAME_DOCUMENT_NAME
from test_api import _client
from test_claude_code_agents import claude_code_agent
from test_repositories import HEADERS


@pytest.fixture
def sessions(store, tmp_path, monkeypatch):
    """A Claude Code agent, a Claude and an OpenAI model, a code project with one folder, a server and DATA_ROOT folders."""
    data = tmp_path / 'data'
    for folder in ('code/app', 'jira', 'generated-documents'):
        (data / folder).mkdir(parents=True)
    monkeypatch.setenv('DATA_ROOT_DIR', str(data))
    monkeypatch.setenv('CODE_ROOT', str(data / 'code'))
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(data / 'generated-documents'))
    client = _client(tmp_path, repository_store=store)
    agent = claude_code_agent(client)
    claude = client.post('/api/llm/models', headers=HEADERS, json={
        'name': 'Claude', 'provider': 'bedrock', 'model': 'eu.anthropic.claude-sonnet-4-5-20250929-v1:0',
        'api_format': 'native', 'aws_region': 'eu-central-1', 'api_key': 'bedrock-secret-token'}).json()['id']
    gpt = client.post('/api/llm/models', headers=HEADERS, json={
        'name': 'GPT', 'provider': 'openai', 'model': 'gpt-5', 'api_format': 'responses',
        'base_url': 'http://model.test/v1'}).json()['id']
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        repo = uuid4()
        conn.execute("INSERT INTO ka.repositories(id,project_id,slug,name) VALUES (%s,%s,'payments-api','Payments API')", (repo, project))
        conn.execute("INSERT INTO ka.code_project_folders(code_project_id,project_id,path) VALUES (%s,%s,'app')", (repo, project))
    server = client.post('/api/servers', headers=HEADERS, json={
        'name': 'prod-1', 'hostname': '10.0.0.5', 'description': 'Payments host'}).json()
    prompt = next(p for p in client.get('/api/documentation/prompts', headers=HEADERS).json()['prompts']
                  if p['name'] == 'Code analysis and architecture')
    body = {'agent_id': agent, 'model_id': claude, 'prompt_id': prompt['id'], 'prompt_version_id': prompt['active_version_id'],
            'comments': 'Focus on the public API.', 'code_project_ids': [str(repo)], 'folders': ['jira'],
            'server_ids': [server['id']], 'notes': 'Deployed with Helm.',
            'document_name': 'Payments – API reference', 'folder': 'payments-api-docs'}
    return SimpleNamespace(client=client, store=store, body=body, agent=agent, claude=claude, gpt=gpt, repo=str(repo),
                           server=server, data=data, prompt=prompt)


def start(sessions, **change) -> dict:
    response = sessions.client.post('/api/claude-code/sessions', headers=HEADERS, json={**sessions.body, **change})
    assert response.status_code == 201, response.text
    return response.json()


def test_session_context_snapshot_folders_servers_and_notes(sessions):
    session = start(sessions)
    data = sessions.data
    assert (session['status'], session['turns'], session['title'], session['folder'], session['agent_name'], session['model_name']) == (
        'starting', 0, 'Payments – API reference', 'payments-api-docs', 'Technical writer', 'Claude')
    assert session['context_snapshot'] == {
        'folders': [{'path': f'{data}/code/app', 'kind': 'code_project', 'name': 'Payments API', 'code_project_id': sessions.repo},
                    {'path': f'{data}/jira', 'kind': 'folder', 'name': 'jira'}],
        'code_project_ids': [sessions.repo],
        'servers': [{'id': sessions.server['id'], 'name': 'prod-1', 'hostname': '10.0.0.5', 'description': 'Payments host'}],
        'notes': 'Deployed with Helm.'}
    snapshot = session['prompt_snapshot']
    assert (snapshot['system_prompt'], snapshot['version_id'], snapshot['comments']) == (
        SYSTEM_PROMPT, sessions.prompt['active_version_id'], 'Focus on the public API.')
    [first] = session['inputs']
    assert (first['seq'], first['status'], first['turn']) == (1, 'queued', None)
    assert first['text'].startswith(snapshot['content'].strip())
    for part in ('## Comments for this run\n\nFocus on the public API.', '## Context',
                 f'- {data}/code/app (code project Payments API)', f'- {data}/jira\n', '- prod-1 (10.0.0.5): Payments host',
                 'Notes:\n\nDeployed with Helm.', FINAL_ANSWER):
        assert part in first['text'], part
    assert 'bedrock-secret-token' not in json.dumps(session)
    # A DATA_ROOT folder alone is enough context; without servers, notes and comments those sections are left out.
    folder_only = start(sessions, code_project_ids=[], server_ids=[], notes='', comments='')
    text = folder_only['inputs'][0]['text']
    assert folder_only['context_snapshot']['folders'] == [{'path': f'{data}/jira', 'kind': 'folder', 'name': 'jira'}]
    assert 'Servers' not in text and 'Notes:' not in text and 'Comments for this run' not in text


@pytest.mark.parametrize('change, status', [
    ({'model_id': 'gpt'}, 400), ({'agent_id': 'search'}, 400), ({'agent_id': 'missing'}, 400),
    ({'folders': ['../etc']}, 400), ({'folders': ['/etc']}, 400), ({'folders': ['.git']}, 400), ({'folders': ['missing']}, 400),
    ({'code_project_ids': [], 'folders': []}, 400), ({'code_project_ids': [str(uuid4())]}, 400),
    ({'folder': 'Payments'}, 400), ({'folder': 'services/x'}, 400), ({'folder': '../docs'}, 400),
    ({'document_name': '– –'}, 400), ({'notes': 'a\x00b'}, 400), ({'comments': 'x' * 20001}, 400),
    ({'server_ids': [str(uuid4())]}, 400), ({'prompt_version_id': str(uuid4())}, 409), ({'prompt_id': str(uuid4())}, 400)])
def test_session_input_is_validated(sessions, change, status):
    if change.get('model_id') == 'gpt':
        change = {'model_id': sessions.gpt}
    response = sessions.client.post('/api/claude-code/sessions', headers=HEADERS, json={**sessions.body, **change})
    assert response.status_code == status, response.text
    if status == 409:
        assert response.json()['error']['message'] == PROMPT_CHANGED
    with sessions.store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions_cc').fetchone()['n'] == 0


def test_a_removed_folder_or_disabled_agent_refuses_the_session(sessions):
    (sessions.data / 'code/app').rmdir()
    response = sessions.client.post('/api/claude-code/sessions', headers=HEADERS, json=sessions.body)
    assert response.status_code == 400 and 'does not exist' in response.json()['error']['message']
    with sessions.store.connect() as conn:
        conn.execute('UPDATE ka.agents SET enabled=false WHERE id=%s', (sessions.agent,))
    response = sessions.client.post('/api/claude-code/sessions', headers=HEADERS, json={**sessions.body, 'code_project_ids': []})
    assert response.status_code == 400 and response.json()['error']['message'] == 'Select an enabled Claude Code agent.'
    with sessions.store.connect() as conn:  # an enabled agent with a disabled Claude model is refused as well
        conn.execute('UPDATE ka.agents SET enabled=true WHERE id=%s', (sessions.agent,))
        conn.execute('UPDATE ka.llm_models SET enabled=false WHERE id=%s', (sessions.claude,))
    response = sessions.client.post('/api/claude-code/sessions', headers=HEADERS, json={**sessions.body, 'code_project_ids': []})
    assert response.status_code == 400 and response.json()['error']['message'].startswith('Select an enabled Claude model')
    with sessions.store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions_cc').fetchone()['n'] == 0


def test_messages_stop_and_close(sessions):
    client, store = sessions.client, sessions.store
    session = start(sessions)
    url = f"/api/claude-code/sessions/{session['id']}"
    assert client.post(url + '/messages', headers=HEADERS, json={'text': ' Also cover retries. '}).json() == {
        'seq': 2, 'status': 'queued', 'working': False}
    assert client.post(url + '/messages', headers=HEADERS, json={'text': '   '}).status_code == 400
    assert client.post(url + '/stop', headers=HEADERS).status_code == 409  # starting: nothing runs yet
    with store.connect() as conn:  # a worker delivered both messages as turn 1
        conn.execute("UPDATE ka.agent_sessions_cc SET status='working',turns=1 WHERE id=%s", (session['id'],))
        conn.execute("UPDATE ka.agent_session_inputs SET status='delivered',turn=1,delivered_at=now() WHERE session_id=%s", (session['id'],))
    assert client.post(url + '/messages', headers=HEADERS, json={'text': 'Shorten it.'}).json()['working'] is True
    stopped = client.post(url + '/stop', headers=HEADERS)
    assert stopped.status_code == 202 and stopped.json()['discarded'] == 1
    detail = client.get(url, headers=HEADERS).json()
    assert [(i['seq'], i['text'], i['status'], i['turn']) for i in detail['inputs'][1:]] == [
        (2, 'Also cover retries.', 'delivered', 1), (3, 'Shorten it.', 'discarded', None)]
    with store.connect() as conn:
        assert conn.execute('SELECT stop_requested_at FROM ka.agent_sessions_cc WHERE id=%s', (session['id'],)).fetchone()['stop_requested_at']
    assert client.post(url + '/close', headers=HEADERS).status_code == 200
    detail = client.get(url, headers=HEADERS).json()
    assert (detail['status'], detail['status_message'], bool(detail['closed_at'])) == ('closed', 'Closed.', True)
    closed = client.post(url + '/messages', headers=HEADERS, json={'text': 'Hello?'})
    assert closed.status_code == 409 and closed.json()['error']['message'] == 'This session is closed. Start a new session.'
    assert client.post(url + '/close', headers=HEADERS).status_code == 200  # idempotent
    listed = client.get('/api/claude-code/sessions', headers=HEADERS).json()['sessions']
    assert [(s['id'], s['status'], s['agent_name'], s['model_name'], s['queued']) for s in listed] == [
        (session['id'], 'closed', 'Technical writer', 'Claude', 0)]
    assert 'draft' not in listed[0] and 'prompt_snapshot' not in listed[0]
    assert client.get(f'/api/claude-code/sessions/{uuid4()}', headers=HEADERS).status_code == 404
    for path in (url + '/messages', url + '/stop', url + '/close'):
        assert client.post(path, json={'text': 'x'}).status_code == 403
    assert client.get('/api/claude-code/sessions').status_code == 403
    revision = client.get(f'/api/agents/{sessions.agent}', headers=HEADERS).json()['revision']
    assert client.request('DELETE', f'/api/agents/{sessions.agent}', headers=HEADERS,
                          json={'revision': revision, 'confirmed': True}).status_code == 409  # retained session history


def test_idle_sessions_close_after_24_hours(sessions):
    ids = [start(sessions)['id'] for _ in range(3)]
    with sessions.store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions_cc SET last_activity_at=now()-interval '25 hours' WHERE id=ANY(%s::uuid[])", (ids[:2],))
        conn.execute("UPDATE ka.agent_sessions_cc SET status='working' WHERE id=%s", (ids[1],))  # a running turn is never idle
    listed = {s['id']: s for s in sessions.client.get('/api/claude-code/sessions', headers=HEADERS).json()['sessions']}
    assert (listed[ids[0]]['status'], listed[ids[0]]['status_message']) == ('closed', IDLE_CLOSED)
    assert (listed[ids[1]]['status'], listed[ids[2]]['status']) == ('working', 'starting')
    inputs = sessions.client.get(f'/api/claude-code/sessions/{ids[0]}', headers=HEADERS).json()['inputs']
    assert [i['status'] for i in inputs] == ['discarded']


def parse(text: str) -> list[dict]:
    events = []
    for block in text.strip().split('\n\n'):
        fields = dict(line.split(': ', 1) for line in block.split('\n') if line and not line.startswith(':'))
        if 'event' in fields:
            events.append({'event': fields['event'], 'id': int(fields['id']) if 'id' in fields else None,
                           'data': json.loads(fields['data'])})
    return events


def record(conn, session_id, turn: int, seq: int, kind: str, payload: dict) -> None:
    conn.execute('''INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
        SELECT project_id,%s,%s,%s,agent_id,%s FROM ka.agent_sessions_cc WHERE id=%s''',
        (f'cc:{session_id}:{turn}', seq, kind, Jsonb(payload), session_id))


def test_event_stream_replays_history_and_follows_a_fake_worker(sessions, monkeypatch):
    monkeypatch.setattr(claude_code_api, 'STREAM_POLL_SECONDS', 0.02)
    client, store = sessions.client, sessions.store
    sid = start(sessions)['id']
    other = start(sessions)['id']
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions_cc SET status='working',turns=1 WHERE id=ANY(%s::uuid[])", ([sid, other],))
        record(conn, sid, 1, 1, 'start', {'kind': 'claude_code', 'title': 'Payments'})
        record(conn, other, 1, 1, 'start', {'kind': 'claude_code', 'title': 'Another session'})
        record(conn, sid, 1, 2, 'tool_call', {'name': 'Read', 'summary': '/work/data/code/app/main.py'})
        record(conn, sid, 1, 3, 'tool_result', {'name': 'Read', 'output': 'line\n' * 500, 'preview': 'line\nline'})

    def worker():  # a fake worker finishes the turn, then the session is closed
        time.sleep(0.3)
        with store.connect() as conn:
            record(conn, sid, 1, 4, 'message', {'text': '# Payments API'})
            conn.execute("UPDATE ka.agent_sessions_cc SET status='waiting',draft='# Payments API',draft_turn=1 WHERE id=%s", (sid,))
        time.sleep(0.3)
        with store.connect() as conn:
            conn.execute("UPDATE ka.agent_sessions_cc SET status='closed',closed_at=now() WHERE id=%s", (sid,))

    thread = threading.Thread(target=worker)
    thread.start()
    response = client.get(f'/api/claude-code/sessions/{sid}/events', headers=HEADERS)
    thread.join()
    assert response.status_code == 200 and response.headers['content-type'].startswith('text/event-stream')
    assert response.headers['cache-control'] == 'no-store'
    events = parse(response.text)
    runs = [e for e in events if e['event'] == 'run_event']
    assert [(e['data']['turn'], e['data']['seq'], e['data']['kind']) for e in runs] == [
        (1, 1, 'start'), (1, 2, 'tool_call'), (1, 3, 'tool_result'), (1, 4, 'message')]
    assert all(e['id'] == e['data']['id'] for e in runs)
    assert runs[2]['data']['payload'] == {'name': 'Read', 'preview': 'line\nline'}  # the full output stays in the run record
    states = [e['data'] for e in events if e['event'] == 'session']
    assert states[0]['status'] == 'working' and states[-1]['status'] == 'closed'
    assert any(s['status'] == 'waiting' and s['draft'] == '# Payments API' for s in states)
    assert events[-1]['event'] == 'closed'
    resumed = client.get(f'/api/claude-code/sessions/{sid}/events', headers={**HEADERS, 'Last-Event-ID': str(runs[1]['id'])})
    assert [e['data']['kind'] for e in parse(resumed.text) if e['event'] == 'run_event'] == ['tool_result', 'message']
    after = client.get(f"/api/claude-code/sessions/{sid}/events?after={runs[2]['id']}", headers=HEADERS)
    assert [e['data']['kind'] for e in parse(after.text) if e['event'] == 'run_event'] == ['message']
    assert client.get(f'/api/claude-code/sessions/{uuid4()}/events', headers=HEADERS).status_code == 404
    assert client.get(f'/api/claude-code/sessions/{sid}/events').status_code == 403


def test_save_document_needs_confirmation_for_the_same_name_and_exports_into_the_folder(sessions):
    client, store = sessions.client, sessions.store
    sid = start(sessions)['id']
    url = f'/api/claude-code/sessions/{sid}'
    empty = client.post(url + '/document', headers=HEADERS, json={})
    assert empty.status_code == 409 and empty.json()['error']['message'] == 'There is no draft yet. Wait for the agent to finish a turn.'
    with store.connect() as conn:
        conn.execute("""UPDATE ka.agent_sessions_cc SET status='waiting',turns=1,draft=%s,draft_turn=1 WHERE id=%s""",
                     ('# Payments API\n\nRetries back off.', sid))
    first = client.post(url + '/document', headers=HEADERS, json={})
    assert first.status_code == 201, first.text
    saved = first.json()
    assert (saved['revision'], saved['folder'], saved['export_error']) == (1, 'payments-api-docs', None)
    document = client.get(f"/api/documentation/documents/{saved['id']}", headers=HEADERS).json()
    assert (document['title'], document['slug'], document['folder'], document['code_project_ids'], document['purpose']) == (
        'Payments – API reference', 'payments-api-reference', 'payments-api-docs', [sessions.repo], 'code')
    readme = sessions.data / 'generated-documents/payments-api-docs/payments-api-reference/README.md'
    assert readme.read_text() == '# Payments API\n\nRetries back off.'
    assert saved['output_folder'] == str(readme.parent)
    detail = client.get(url, headers=HEADERS).json()
    assert (detail['document_id'], detail['document_title']) == (saved['id'], 'Payments – API reference')
    again = client.post(url + '/document', headers=HEADERS, json={})
    assert again.status_code == 409 and again.json()['error']['message'] == SAME_DOCUMENT_NAME
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions_cc SET draft='# Payments API\n\nShorter.' WHERE id=%s", (sid,))
    second = client.post(url + '/document', headers=HEADERS, json={'update_existing': True})
    assert second.status_code == 201 and (second.json()['id'], second.json()['revision']) == (saved['id'], 2)
    assert readme.read_text() == '# Payments API\n\nShorter.'
    # Another session with the same name and folder needs the confirmation too; a folder-only session is 'explanation'.
    other = start(sessions, code_project_ids=[])['id']
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions_cc SET status='waiting',turns=1,draft='# Other' WHERE id=%s", (other,))
    assert client.post(f'/api/claude-code/sessions/{other}/document', headers=HEADERS, json={}).status_code == 409
    renamed = start(sessions, code_project_ids=[], document_name='Jira overview')['id']
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions_cc SET status='waiting',turns=1,draft='# Jira' WHERE id=%s", (renamed,))
    created = client.post(f'/api/claude-code/sessions/{renamed}/document', headers=HEADERS, json={}).json()
    overview = client.get(f"/api/documentation/documents/{created['id']}", headers=HEADERS).json()
    assert (overview['purpose'], overview['code_project_ids']) == ('explanation', [])
    assert client.post(url + '/document', json={}).status_code == 403


def test_symlinked_folders_and_folders_outside_data_root_are_refused(sessions, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (sessions.data / 'linked').symlink_to(outside)
    (sessions.data / 'jira-link').symlink_to(sessions.data / 'jira')  # inside DATA_ROOT, but still a link
    (sessions.data / 'code/app-link').symlink_to(outside)
    for change in ({'code_project_ids': [], 'folders': ['linked']}, {'code_project_ids': [], 'folders': ['jira-link']},
                   {'code_project_ids': [], 'folders': ['jira-link/..']}):
        response = sessions.client.post('/api/claude-code/sessions', headers=HEADERS, json={**sessions.body, **change})
        assert response.status_code == 400, (change, response.text)
    with sessions.store.connect() as conn:
        conn.execute("UPDATE ka.code_project_folders SET path='app-link' WHERE code_project_id=%s", (sessions.repo,))
    response = sessions.client.post('/api/claude-code/sessions', headers=HEADERS, json={**sessions.body, 'folders': []})
    assert response.status_code == 400 and 'symbolic link' in response.json()['error']['message']
    with sessions.store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions_cc').fetchone()['n'] == 0


def test_idle_session_is_closed_when_opened_or_sent_to(sessions):
    sid = start(sessions)['id']
    url = f'/api/claude-code/sessions/{sid}'
    with sessions.store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions_cc SET last_activity_at=now()-interval '25 hours' WHERE id=%s", (sid,))
    detail = sessions.client.get(url, headers=HEADERS).json()
    assert (detail['status'], detail['status_message']) == ('closed', IDLE_CLOSED)
    other = start(sessions)['id']
    with sessions.store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions_cc SET last_activity_at=now()-interval '25 hours' WHERE id=%s", (other,))
    sent = sessions.client.post(f'/api/claude-code/sessions/{other}/messages', headers=HEADERS, json={'text': 'Hello?'})
    assert sent.status_code == 409 and sent.json()['error']['message'] == 'This session is closed. Start a new session.'


def test_event_stream_ends_when_the_session_is_gone_and_keeps_alive_sparingly(sessions, monkeypatch):
    monkeypatch.setattr(claude_code_api, 'STREAM_POLL_SECONDS', 0.02)
    monkeypatch.setattr(claude_code_api, 'KEEPALIVE_SECONDS', 60)
    connected = []
    original = claude_code_api.psycopg.AsyncConnection.connect

    async def connect(*args, **kwargs):
        connected.append(kwargs.get('connect_timeout'))
        return await original(*args, **kwargs)
    monkeypatch.setattr(claude_code_api.psycopg.AsyncConnection, 'connect', connect)
    sid = start(sessions)['id']

    def remove():
        time.sleep(0.3)
        with sessions.store.connect() as conn:
            conn.execute('DELETE FROM ka.agent_session_inputs WHERE session_id=%s', (sid,))
            conn.execute('DELETE FROM ka.agent_sessions_cc WHERE id=%s', (sid,))

    thread = threading.Thread(target=remove)
    thread.start()
    response = sessions.client.get(f'/api/claude-code/sessions/{sid}/events', headers=HEADERS)
    thread.join()
    assert parse(response.text)[-1]['event'] == 'closed'
    assert ': keepalive' not in response.text  # well under KEEPALIVE_SECONDS
    assert connected == [5]
    assert claude_code_api.KEEPALIVE_SECONDS == 60 and 10 <= claude_code_api.DEFAULT_KEEPALIVE_SECONDS <= 20


def test_session_uses_the_agent_prompt_instructions_skills_tools_and_code_projects(sessions):
    client, data = sessions.client, sessions.data
    skill = client.post('/api/skills', headers=HEADERS, json={
        'name': 'reference', 'description': 'API reference style', 'instructions': 'List every endpoint with its errors.'}).json()
    agent = claude_code_agent(client, 'API writer', system_prompt='You write API references.',
                              instructions='Write for operators.', model_id=sessions.claude, skills_enabled=True,
                              skill_ids=[skill['id']], repositories_enabled=True, repository_ids=[sessions.repo],
                              claude_code_tools=['Grep', 'Read'])
    body = {key: value for key, value in sessions.body.items() if key != 'model_id'}  # the agent's model is used
    response = client.post('/api/claude-code/sessions', headers=HEADERS, json={**body, 'agent_id': agent, 'code_project_ids': []})
    assert response.status_code == 201, response.text
    session = response.json()
    assert session['model_name'] == 'Claude'
    assert session['context_snapshot']['folders'] == [
        {'path': f'{data}/jira', 'kind': 'folder', 'name': 'jira'},
        {'path': f'{data}/code/app', 'kind': 'code_project', 'name': 'Payments API', 'code_project_id': sessions.repo}]
    assert session['context_snapshot']['code_project_ids'] == []
    snapshot = session['prompt_snapshot']
    assert snapshot['system_prompt'] == ('You write API references.\n\n## Instructions\n\nWrite for operators.\n\n'
                                         '## Skills\n\n### reference\n\nAPI reference style\n\nList every endpoint with its errors.')
    assert (snapshot['claude_code_tools'], snapshot['agent_version']) == (['Read', 'Grep'], None)
    assert f'- {data}/code/app (code project Payments API)' in session['inputs'][0]['text']
    serialized = json.dumps(session)
    for secret in ('api_key_ref', 'private_key_ref', 'bearer_token_ref', 'bedrock-secret-token'):
        assert secret not in serialized
    # An agent without a model needs one in the request.
    plain = claude_code_agent(client, 'Plain writer')
    missing = client.post('/api/claude-code/sessions', headers=HEADERS, json={**body, 'agent_id': plain})
    assert missing.status_code == 400 and missing.json()['error']['message'].startswith('Select an enabled Claude model')
    # The agent's own folders must exist too.
    (data / 'code/app').rmdir()
    gone = client.post('/api/claude-code/sessions', headers=HEADERS, json={**body, 'agent_id': agent, 'code_project_ids': []})
    assert gone.status_code == 400 and "of the agent's code project Payments API does not exist" in gone.json()['error']['message']


def test_session_uses_the_active_version_tools_and_code_projects(sessions):
    from test_profiles import update
    client, data = sessions.client, sessions.data
    agent = claude_code_agent(client, 'Versioned writer', model_id=sessions.claude, repositories_enabled=True,
                              repository_ids=[sessions.repo], claude_code_tools=['Grep', 'Read'])
    path = f'/api/agents/{agent}/versions'
    source = client.get(path + '/source', headers=HEADERS).json()
    draft = client.post(path, headers=HEADERS, json={'label': 'Draft', 'source_hash': source['source_hash'],
                        'changes': {'system_prompt': None, 'instructions': None, 'server_instructions': []}})
    assert draft.status_code == 201, draft.text
    active = client.get(path, headers=HEADERS).json()['active_id']  # the initial configuration
    # Working settings changed after the version was saved do not reach the session.
    profile = client.get(f'/api/agents/{agent}', headers=HEADERS).json()
    changed = update(client, profile, repositories_enabled=False, repository_ids=[], claude_code_tools=['Bash'])
    assert changed.status_code == 200, changed.text
    body = {key: value for key, value in sessions.body.items() if key != 'model_id'}
    response = client.post('/api/claude-code/sessions', headers=HEADERS,
                           json={**body, 'agent_id': agent, 'code_project_ids': [], 'folders': []})
    assert response.status_code == 201, response.text
    session = response.json()
    assert session['model_name'] == 'Claude'
    snapshot = session['prompt_snapshot']
    assert snapshot['claude_code_tools'] == ['Read', 'Grep'] and snapshot['agent_version']['id'] == active
    assert session['context_snapshot']['folders'] == [
        {'path': f'{data}/code/app', 'kind': 'code_project', 'name': 'Payments API', 'code_project_id': sessions.repo}]
