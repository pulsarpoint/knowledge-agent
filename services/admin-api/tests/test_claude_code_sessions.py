"""Generate documents sessions (an ad-hoc native agent): creation, the context, queued messages, Stop, Close and idle
closing."""
import json
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from knowledge_admin import claude_code_api
from knowledge_admin.claude_code import FINAL_ANSWER, IDLE_CLOSED
from knowledge_admin.claude_code_api import NO_MODEL, NOTHING_CHOSEN
from knowledge_admin.documents import SAME_DOCUMENT_NAME
from session_rows import add_test_session
from test_api import _client
from test_profiles import wizard_payload
from test_repositories import HEADERS


@pytest.fixture
def sessions(store, tmp_path, monkeypatch):
    """A native agent with a Claude model; code projects Payments API (app) and Payments worker (worker); the
    Production environment with the Payments service (both projects, deployed on prod-1) and the prod-1 server."""
    data = tmp_path / 'data'
    for folder in ('code/app', 'code/worker', 'jira', 'generated-documents'):
        (data / folder).mkdir(parents=True)
    monkeypatch.setenv('DATA_ROOT_DIR', str(data))
    monkeypatch.setenv('CODE_ROOT', str(data / 'code'))
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(data / 'generated-documents'))
    client = _client(tmp_path, repository_store=store)
    claude = client.post('/api/llm/models', headers=HEADERS, json={
        'name': 'Claude', 'provider': 'bedrock', 'model': 'eu.anthropic.claude-sonnet-4-5-20250929-v1:0',
        'api_format': 'native', 'aws_region': 'eu-central-1', 'api_key': 'bedrock-secret-token'}).json()['id']
    gpt = client.post('/api/llm/models', headers=HEADERS, json={
        'name': 'GPT', 'provider': 'openai', 'model': 'gpt-5', 'api_format': 'responses',
        'base_url': 'http://model.test/v1'}).json()['id']
    agent = native_agent(client, model_id=claude)
    server = client.post('/api/servers', headers=HEADERS, json={
        'name': 'prod-1', 'hostname': '10.0.0.5', 'description': 'Payments host'}).json()
    repo, worker, environment, service = uuid4(), uuid4(), uuid4(), uuid4()
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        for project_id, slug, name, folder in ((repo, 'payments-api', 'Payments API', 'app'),
                                               (worker, 'payments-worker', 'Payments worker', 'worker')):
            conn.execute('INSERT INTO ka.repositories(id,project_id,slug,name) VALUES (%s,%s,%s,%s)', (project_id, project, slug, name))
            conn.execute('INSERT INTO ka.code_project_folders(code_project_id,project_id,path) VALUES (%s,%s,%s)',
                         (project_id, project, folder))
        conn.execute("INSERT INTO ka.environments(id,project_id,name,description) VALUES (%s,%s,'Production','Live systems')",
                     (environment, project))
        conn.execute("INSERT INTO ka.services(id,project_id,name) VALUES (%s,%s,'Payments')", (service, project))
        for project_id in (repo, worker):
            conn.execute('INSERT INTO ka.service_repositories(project_id,service_id,repository_id) VALUES (%s,%s,%s)',
                         (project, service, project_id))
        conn.execute('INSERT INTO ka.environment_services(project_id,environment_id,service_id) VALUES (%s,%s,%s)',
                     (project, environment, service))
        conn.execute('INSERT INTO ka.environment_servers(project_id,environment_id,server_id) VALUES (%s,%s,%s)',
                     (project, environment, server['id']))
        conn.execute("""INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,environment_id,
            runtime_name,port) VALUES (%s,%s,%s,'Production',%s,'payments-api',8080)""", (project, service, server['id'], environment))
    body = {'model_id': gpt, 'code_project_ids': [str(repo)], 'environment_ids': [str(environment)],
            'service_ids': [str(service)], 'server_ids': [], 'system_prompt': ' You document systems. ',
            'context': '# What you are documenting\n\nPayments', 'prompt': ' Write the API reference. ',
            'document_name': 'Payments – API reference', 'folder': 'payments-api-docs'}
    return SimpleNamespace(client=client, store=store, body=body, agent=agent, claude=claude, gpt=gpt, repo=str(repo),
                           worker=str(worker), environment=str(environment), service=str(service), server=server, data=data)


def start(sessions, **change) -> dict:
    response = sessions.client.post('/api/claude-code/sessions', headers=HEADERS, json={**sessions.body, **change})
    assert response.status_code == 201, response.text
    return response.json()


def refused(sessions, **change) -> str:
    response = sessions.client.post('/api/claude-code/sessions', headers=HEADERS, json={**sessions.body, **change})
    assert response.status_code == 400, response.text
    return response.json()['error']['message']


def test_generate_documents_starts_an_ad_hoc_agent_with_the_forms_prompt_context_and_selection(sessions):
    s = sessions
    session = start(s, prompt_id=None)
    app, worker = f'{s.data}/code/app', f'{s.data}/code/worker'
    assert (session['agent_id'], session['agent_name'], session['model_name']) == (None, 'Ad-hoc agent', 'GPT')
    assert 'runtime' not in session and 'cost_usd' not in session
    assert (session['title'], session['folder'], session['status']) == ('Payments – API reference', 'payments-api-docs', 'starting')
    assert session['prompt_snapshot'] == {'system_prompt': 'You document systems.',
                                          'context': '# What you are documenting\n\nPayments', 'prompt_id': None}
    assert session['context_snapshot']['code_project_ids'] == [s.repo]  # Save document links the chosen projects
    assert [f['path'] for f in session['context_snapshot']['folders']] == [app, worker]  # chosen, then brought
    assert session['inputs'][0]['text'] == f'Write the API reference.\n\n{FINAL_ANSWER}'
    assert session['run_resources']['service_ids'] == [s.service]
    [first] = session['inputs']
    assert (first['seq'], first['status'], first['turn']) == (1, 'queued', None)
    detail = s.client.get(f"/api/claude-code/sessions/{session['id']}", headers=HEADERS).json()
    for shown in (session, detail):  # a documentation session's pinned config and history stay private
        assert shown['purpose'] == 'documents'
        for hidden in ('config', 'reviews', 'agent_config', 'history'):
            assert hidden not in shown
        serialized = json.dumps(shown)
        for secret in ('api_key_ref', 'private_key_ref', 'bearer_token_ref', 'bedrock-secret-token', 'private_key',
                       'credential'):
            assert secret not in serialized
    with s.store.connect() as conn:
        row = conn.execute('SELECT agent_id,agent_config FROM ka.agent_sessions WHERE id=%s', (session['id'],)).fetchone()
        audit = conn.execute("SELECT details FROM ka.audit_events WHERE resource_id=%s", (session['id'],)).fetchone()
        assert conn.execute('SELECT count(*) AS n FROM ka.agents WHERE id=%s', ('ad-hoc',)).fetchone()['n'] == 0
    assert row['agent_id'] is None
    assert row['agent_config']['system_prompt']['content'] == 'You document systems.\n\n# What you are documenting\n\nPayments'
    assert audit['details'] == {'ephemeral': True, 'model_id': s.gpt, 'prompt_id': None}
    no_context = start(s, context='  ')
    with s.store.connect() as conn:
        pinned = conn.execute('SELECT agent_config FROM ka.agent_sessions WHERE id=%s', (no_context['id'],)).fetchone()
    assert pinned['agent_config']['system_prompt']['content'] == 'You document systems.'
    listed = s.client.get('/api/claude-code/sessions', headers=HEADERS).json()['sessions']
    assert {row['agent_name'] for row in listed} == {'Ad-hoc agent'}


def test_the_context_endpoint_describes_the_selection(sessions):
    s = sessions
    response = s.client.post('/api/claude-code/context', headers=HEADERS, json={'service_ids': [s.service]})
    assert response.status_code == 200, response.text
    assert response.json()['text'].startswith('# What you are documenting\n\n## Services\n- Payments — deployed on prod-1')
    assert s.client.post('/api/claude-code/context', headers=HEADERS, json={}).json() == {'text': ''}
    assert s.client.post('/api/claude-code/context', headers=HEADERS,
                         json={'server_ids': [str(uuid4())]}).status_code == 400


def test_ad_hoc_sessions_refuse_what_cannot_run(sessions):
    s = sessions
    chat = s.client.post('/api/llm/models', headers=HEADERS, json={
        'name': 'Chat', 'provider': 'openai', 'model': 'gpt-4o', 'api_format': 'chat_completions',
        'base_url': 'http://model.test/v1'}).json()['id']
    assert refused(s, model_id=chat) == NO_MODEL  # Chat Completions does not fit native agents
    assert refused(s, model_id=str(uuid4())) == NO_MODEL
    with s.store.connect() as conn:
        conn.execute('UPDATE ka.llm_models SET enabled=false WHERE id=%s', (s.gpt,))
    assert refused(s) == NO_MODEL
    with s.store.connect() as conn:
        conn.execute('UPDATE ka.llm_models SET enabled=true WHERE id=%s', (s.gpt,))
    # prod-1 has no SSH connection: brought by Production it is left out, chosen directly it is refused by name.
    assert refused(s, server_ids=[s.server['id']]) == 'Server prod-1 is not available: it was removed, archived or disabled.'
    with s.store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions').fetchone()['n'] == 0


@pytest.mark.parametrize('change, status', [
    ({'model_id': 'not-a-uuid'}, 400), ({'agent_id': 'search'}, 400), ({'instructions': 'x'}, 400),
    ({'code_project_ids': [], 'environment_ids': [], 'service_ids': [], 'server_ids': []}, 400),
    ({'code_project_ids': ['not-a-uuid']}, 400), ({'code_project_ids': [str(uuid4())]}, 400),
    ({'environment_ids': [str(uuid4())]}, 400), ({'service_ids': [str(uuid4())]}, 400), ({'server_ids': [str(uuid4())]}, 400),
    ({'folder': 'Payments'}, 400), ({'folder': 'services/x'}, 400), ({'folder': '../docs'}, 400),
    ({'document_name': '– –'}, 400), ({'system_prompt': '  '}, 400), ({'system_prompt': 'x' * 20001}, 400),
    ({'prompt': ''}, 400), ({'prompt': 'a\x00b'}, 400), ({'prompt': 'x' * 100001}, 400),
    ({'context': 'x' * 100001}, 400), ({'context': 'a\x00b'}, 400), ({'prompt_id': 'nope'}, 400)])
def test_session_input_is_validated(sessions, change, status):
    response = sessions.client.post('/api/claude-code/sessions', headers=HEADERS, json={**sessions.body, **change})
    assert response.status_code == status, response.text
    if change == {'code_project_ids': [], 'environment_ids': [], 'service_ids': [], 'server_ids': []}:
        assert NOTHING_CHOSEN in response.json()['error']['message']
    with sessions.store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions').fetchone()['n'] == 0


def test_messages_stop_and_close(sessions):
    client, store = sessions.client, sessions.store
    session = start(sessions)
    url = f"/api/claude-code/sessions/{session['id']}"
    assert client.post(url + '/messages', headers=HEADERS, json={'text': ' Also cover retries. '}).json() == {
        'seq': 2, 'status': 'queued', 'working': False}
    assert client.post(url + '/messages', headers=HEADERS, json={'text': '   '}).status_code == 400
    assert client.post(url + '/stop', headers=HEADERS).status_code == 409  # starting: nothing runs yet
    with store.connect() as conn:  # a worker delivered both messages as turn 1
        conn.execute("UPDATE ka.agent_sessions SET status='working',turns=1 WHERE id=%s", (session['id'],))
        conn.execute("UPDATE ka.agent_session_inputs SET status='delivered',turn=1,delivered_at=now() WHERE session_id=%s", (session['id'],))
    assert client.post(url + '/messages', headers=HEADERS, json={'text': 'Shorten it.'}).json()['working'] is True
    stopped = client.post(url + '/stop', headers=HEADERS)
    assert stopped.status_code == 202 and stopped.json()['discarded'] == 1
    detail = client.get(url, headers=HEADERS).json()
    assert [(i['seq'], i['text'], i['status'], i['turn']) for i in detail['inputs'][1:]] == [
        (2, 'Also cover retries.', 'delivered', 1), (3, 'Shorten it.', 'discarded', None)]
    with store.connect() as conn:
        assert conn.execute('SELECT stop_requested_at FROM ka.agent_sessions WHERE id=%s', (session['id'],)).fetchone()['stop_requested_at']
    assert client.post(url + '/close', headers=HEADERS).status_code == 200
    detail = client.get(url, headers=HEADERS).json()
    assert (detail['status'], detail['status_message'], bool(detail['closed_at'])) == ('closed', 'Closed.', True)
    closed = client.post(url + '/messages', headers=HEADERS, json={'text': 'Hello?'})
    assert closed.status_code == 409 and closed.json()['error']['message'] == 'This session is closed. Start a new session.'
    assert client.post(url + '/close', headers=HEADERS).status_code == 200  # idempotent
    listed = client.get('/api/claude-code/sessions', headers=HEADERS).json()['sessions']
    assert [(s['id'], s['status'], s['agent_name'], s['model_name'], s['queued']) for s in listed] == [
        (session['id'], 'closed', 'Ad-hoc agent', 'GPT', 0)]
    assert 'draft' not in listed[0] and 'prompt_snapshot' not in listed[0]
    assert client.get(f'/api/claude-code/sessions/{uuid4()}', headers=HEADERS).status_code == 404
    for path in (url + '/messages', url + '/stop', url + '/close'):
        assert client.post(path, json={'text': 'x'}).status_code == 403
    assert client.get('/api/claude-code/sessions').status_code == 403
    with sessions.store.connect() as conn:  # an agent with a session keeps it
        project, actor = sessions.store.project(conn, 'shop'), sessions.store.actor(conn, 'admin-id', 'admin@example.test')
        add_test_session(conn, project, actor, sessions.agent, sessions.claude, status='waiting')
    revision = client.get(f'/api/agents/{sessions.agent}', headers=HEADERS).json()['revision']
    assert client.request('DELETE', f'/api/agents/{sessions.agent}', headers=HEADERS,
                          json={'revision': revision, 'confirmed': True}).status_code == 409  # retained session history


def test_idle_sessions_close_after_24_hours(sessions):
    ids = [start(sessions)['id'] for _ in range(3)]
    with sessions.store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET last_activity_at=now()-interval '25 hours' WHERE id=ANY(%s::uuid[])", (ids[:2],))
        conn.execute("UPDATE ka.agent_sessions SET status='working' WHERE id=%s", (ids[1],))  # a running turn is never idle
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
        SELECT project_id,%s,%s,%s,coalesce(agent_id,'ad-hoc'),%s FROM ka.agent_sessions WHERE id=%s''',
        (f'cc:{session_id}:{turn}', seq, kind, Jsonb(payload), session_id))


def test_event_stream_replays_history_and_follows_a_fake_worker(sessions, monkeypatch):
    monkeypatch.setattr(claude_code_api, 'STREAM_POLL_SECONDS', 0.02)
    client, store = sessions.client, sessions.store
    sid = start(sessions)['id']
    other = start(sessions)['id']
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET status='working',turns=1 WHERE id=ANY(%s::uuid[])", ([sid, other],))
        record(conn, sid, 1, 1, 'start', {'kind': 'claude_code', 'title': 'Payments'})
        record(conn, other, 1, 1, 'start', {'kind': 'claude_code', 'title': 'Another session'})
        record(conn, sid, 1, 2, 'tool_call', {'name': 'Read', 'summary': '/work/data/code/app/main.py'})
        record(conn, sid, 1, 3, 'tool_result', {'name': 'Read', 'output': 'line\n' * 500, 'preview': 'line\nline'})

    def worker():  # a fake worker finishes the turn, then the session is closed
        time.sleep(0.3)
        with store.connect() as conn:
            record(conn, sid, 1, 4, 'message', {'text': '# Payments API'})
            conn.execute("UPDATE ka.agent_sessions SET status='waiting',draft='# Payments API',draft_turn=1 WHERE id=%s", (sid,))
        time.sleep(0.3)
        with store.connect() as conn:
            conn.execute("UPDATE ka.agent_sessions SET status='closed',closed_at=now() WHERE id=%s", (sid,))

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


def test_event_history_pages_backwards_like_the_stream(sessions):
    client, store = sessions.client, sessions.store
    sid = start(sessions)['id']
    other = start(sessions)['id']
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET status='waiting',turns=2 WHERE id=ANY(%s::uuid[])", ([sid, other],))
        for seq in (1, 2, 3):
            record(conn, sid, 1, seq, 'message', {'text': f'one {seq}'})
        record(conn, other, 1, 1, 'message', {'text': 'another session'})
        record(conn, sid, 2, 1, 'tool_result', {'name': 'Read', 'output': 'the whole file body', 'preview': 'line'})
        record(conn, sid, 2, 2, 'message', {'text': 'two 2'})
    url = f'/api/claude-code/sessions/{sid}/events/history'
    latest = client.get(url + '?limit=2', headers=HEADERS)
    assert latest.status_code == 200 and latest.headers['cache-control'] == 'no-store'
    page = latest.json()
    assert [(e['turn'], e['seq'], e['kind']) for e in page['events']] == [(2, 1, 'tool_result'), (2, 2, 'message')]
    assert page['has_more'] is True
    assert page['events'][0]['payload'] == {'name': 'Read', 'preview': 'line'}  # redacted like the stream
    assert 'the whole file body' not in latest.text
    older = client.get(f"{url}?before={page['events'][0]['id']}&limit=2", headers=HEADERS).json()
    assert [e['payload']['text'] for e in older['events']] == ['one 2', 'one 3'] and older['has_more'] is True
    first = client.get(f"{url}?before={older['events'][0]['id']}&limit=2", headers=HEADERS).json()
    assert [e['payload']['text'] for e in first['events']] == ['one 1'] and first['has_more'] is False
    default = client.get(url, headers=HEADERS).json()  # the latest 100: the whole session here, never another one's
    assert [e['seq'] for e in default['events']] == [1, 2, 3, 1, 2] and default['has_more'] is False
    assert all(e['id'] == r['id'] for e, r in zip(default['events'][-2:], page['events']))
    for bad in ('?limit=0', '?limit=501', '?before=0', '?before=x'):
        assert client.get(url + bad, headers=HEADERS).status_code == 400, bad
    assert client.get(f'/api/claude-code/sessions/{uuid4()}/events/history', headers=HEADERS).status_code == 404
    assert client.get(url).status_code == 403


def test_save_document_needs_confirmation_for_the_same_name_and_exports_into_the_folder(sessions):
    client, store = sessions.client, sessions.store
    sid = start(sessions)['id']
    url = f'/api/claude-code/sessions/{sid}'
    empty = client.post(url + '/document', headers=HEADERS, json={})
    assert empty.status_code == 409 and empty.json()['error']['message'] == 'There is no draft yet. Wait for the agent to finish a turn.'
    with store.connect() as conn:
        conn.execute("""UPDATE ka.agent_sessions SET status='waiting',turns=1,draft=%s,draft_turn=1 WHERE id=%s""",
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
        conn.execute("UPDATE ka.agent_sessions SET draft='# Payments API\n\nShorter.' WHERE id=%s", (sid,))
    second = client.post(url + '/document', headers=HEADERS, json={'update_existing': True})
    assert second.status_code == 201 and (second.json()['id'], second.json()['revision']) == (saved['id'], 2)
    assert readme.read_text() == '# Payments API\n\nShorter.'
    # Another session with the same name and folder needs the confirmation too; without chosen code projects it is
    # 'explanation'.
    other = start(sessions, code_project_ids=[])['id']
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET status='waiting',turns=1,draft='# Other' WHERE id=%s", (other,))
    assert client.post(f'/api/claude-code/sessions/{other}/document', headers=HEADERS, json={}).status_code == 409
    renamed = start(sessions, code_project_ids=[], document_name='Jira overview')['id']
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET status='waiting',turns=1,draft='# Jira' WHERE id=%s", (renamed,))
    created = client.post(f'/api/claude-code/sessions/{renamed}/document', headers=HEADERS, json={}).json()
    overview = client.get(f"/api/documentation/documents/{created['id']}", headers=HEADERS).json()
    assert (overview['purpose'], overview['code_project_ids']) == ('explanation', [])
    assert client.post(url + '/document', json={}).status_code == 403


def test_idle_session_is_closed_when_opened_or_sent_to(sessions):
    sid = start(sessions)['id']
    url = f'/api/claude-code/sessions/{sid}'
    with sessions.store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET last_activity_at=now()-interval '25 hours' WHERE id=%s", (sid,))
    detail = sessions.client.get(url, headers=HEADERS).json()
    assert (detail['status'], detail['status_message']) == ('closed', IDLE_CLOSED)
    other = start(sessions)['id']
    with sessions.store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET last_activity_at=now()-interval '25 hours' WHERE id=%s", (other,))
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
            conn.execute('DELETE FROM ka.agent_sessions WHERE id=%s', (sid,))

    thread = threading.Thread(target=remove)
    thread.start()
    response = sessions.client.get(f'/api/claude-code/sessions/{sid}/events', headers=HEADERS)
    thread.join()
    assert parse(response.text)[-1]['event'] == 'closed'
    assert ': keepalive' not in response.text  # well under KEEPALIVE_SECONDS
    assert connected == [5]
    assert claude_code_api.KEEPALIVE_SECONDS == 60 and 10 <= claude_code_api.DEFAULT_KEEPALIVE_SECONDS <= 20


def native_agent(client, name: str = 'Native writer', **change) -> str:
    """Create an agent as New agent does."""
    body = wizard_payload(name, description='', when_to_use='', system_prompt='You document systems.')
    response = client.post('/api/agents/wizard', headers=HEADERS, json={**body, **change})
    assert response.status_code == 201, response.text
    return response.json()['id']
