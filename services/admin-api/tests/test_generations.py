"""Generate documents sessions (an ad-hoc native agent): creation, the context, queued messages, Stop, Close and idle
closing."""

import json
import threading
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from psycopg.types.json import Jsonb

from knowledge_admin.sessions import api as sessions_api
from knowledge_admin.sessions.text import IDLE_CLOSED
from knowledge_admin.sessions.rows import NO_MODEL
from session_rows import add_chat_session
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
    client = _client(tmp_path, repository_store=store)
    claude = client.post(
        '/api/llm/models',
        headers=HEADERS,
        json={
            'name': 'Claude',
            'provider': 'bedrock',
            'model': 'eu.anthropic.claude-sonnet-4-5-20250929-v1:0',
            'api_format': 'native',
            'aws_region': 'eu-central-1',
            'api_key': 'bedrock-secret-token',
        },
    ).json()['id']
    gpt = client.post(
        '/api/llm/models',
        headers=HEADERS,
        json={
            'name': 'GPT',
            'provider': 'openai',
            'model': 'gpt-5',
            'api_format': 'responses',
            'base_url': 'http://model.test/v1',
        },
    ).json()['id']
    agent = native_agent(client, model_id=claude)
    server = client.post(
        '/api/servers', headers=HEADERS, json={'name': 'prod-1', 'hostname': '10.0.0.5', 'description': 'Payments host'}
    ).json()
    repo, worker, environment, service = uuid4(), uuid4(), uuid4(), uuid4()
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        for project_id, slug, name, folder in (
            (repo, 'payments-api', 'Payments API', 'app'),
            (worker, 'payments-worker', 'Payments worker', 'worker'),
        ):
            conn.execute(
                'INSERT INTO ka.repositories(id,project_id,slug,name) VALUES (%s,%s,%s,%s)',
                (project_id, project, slug, name),
            )
            conn.execute(
                'INSERT INTO ka.code_project_folders(code_project_id,project_id,path) VALUES (%s,%s,%s)',
                (project_id, project, folder),
            )
        conn.execute(
            "INSERT INTO ka.environments(id,project_id,name,description) VALUES (%s,%s,'Production','Live systems')",
            (environment, project),
        )
        conn.execute("INSERT INTO ka.services(id,project_id,name) VALUES (%s,%s,'Payments')", (service, project))
        for project_id in (repo, worker):
            conn.execute(
                "INSERT INTO ka.service_relations(project_id,from_service_id,to_code_project_id,kind) VALUES (%s,%s,%s,'built_from')",
                (project, service, project_id),
            )
        conn.execute(
            """INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment_id,
            runtime_name,port) VALUES (%s,%s,%s,%s,'payments-api',8080)""",
            (project, service, server['id'], environment),
        )
    body = {
        'model_id': gpt,
        'code_project_ids': [str(repo)],
        'environment_ids': [str(environment)],
        'service_ids': [str(service)],
        'server_ids': [],
        'system_prompt': ' You document systems. ',
        'prompt': ' Write the API reference. ',
    }
    return SimpleNamespace(
        client=client,
        store=store,
        body=body,
        agent=agent,
        claude=claude,
        gpt=gpt,
        repo=str(repo),
        worker=str(worker),
        environment=str(environment),
        service=str(service),
        server=server,
        data=data,
    )


@pytest.fixture
def live(sessions):
    """The sessions client with its lifespan running, so its session hub listens; yields the hub. Stream tests need it:
    a TestClient used without `with` never starts the lifespan."""
    with sessions.client as client:
        yield client.app.state.session_hub


SELECTION = ('code_project_ids', 'environment_ids', 'service_ids', 'server_ids')


def post(sessions, **change):
    """Start a Write with an agent run from the picked resources: `sessions.body` (the form's keys) as POST
    /api/sessions. `expert_ids` passes through when given (every run gets every tool)."""
    form = {**sessions.body, **change}
    body = {
        'started_from': {'new_document': True},
        'message': form['prompt'],
        'model_id': form['model_id'],
        'system_prompt': form['system_prompt'],
        'prompt_id': form.get('prompt_id'),
        **{key: form[key] for key in ('expert_ids',) if key in form},
        'resources': {key: form[key] for key in SELECTION if key in form},
    }
    return sessions.client.post('/api/sessions', headers=HEADERS, json=body)


def start(sessions, **change) -> dict:
    response = post(sessions, **change)
    assert response.status_code == 201, response.text
    return response.json()


def refused(sessions, **change) -> str:
    response = post(sessions, **change)
    assert response.status_code == 400, response.text
    return response.json()['error']['message']


def test_ad_hoc_sessions_refuse_what_cannot_run(sessions):
    s = sessions
    chat = s.client.post(
        '/api/llm/models',
        headers=HEADERS,
        json={
            'name': 'Chat',
            'provider': 'openai',
            'model': 'gpt-4o',
            'api_format': 'chat_completions',
            'base_url': 'http://model.test/v1',
        },
    ).json()['id']
    assert refused(s, model_id=chat) == NO_MODEL  # Chat Completions does not fit native agents
    assert refused(s, model_id=str(uuid4())) == NO_MODEL
    with s.store.connect() as conn:
        conn.execute('UPDATE ka.llm_models SET enabled=false WHERE id=%s', (s.gpt,))
    assert refused(s) == NO_MODEL
    with s.store.connect() as conn:
        conn.execute('UPDATE ka.llm_models SET enabled=true WHERE id=%s', (s.gpt,))
    # prod-1 has no SSH connection: brought by Production it is left out, chosen directly it is refused by name.
    assert refused(s, server_ids=[s.server['id']]) == 'Server prod-1: SSH is not set up for this server.'
    with s.store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions').fetchone()['n'] == 0


def test_messages_stop_and_close(sessions):
    client, store = sessions.client, sessions.store
    session = start(sessions)
    url = f'/api/sessions/{session["id"]}'
    assert client.post(url + '/messages', headers=HEADERS, json={'text': ' Also cover retries. '}).json() == {
        'seq': 2,
        'status': 'queued',
        'working': False,
    }
    assert client.post(url + '/messages', headers=HEADERS, json={'text': '   '}).status_code == 400
    assert client.post(url + '/stop', headers=HEADERS).status_code == 409  # starting: nothing runs yet
    with store.connect() as conn:  # a worker delivered both messages as turn 1
        conn.execute("UPDATE ka.agent_sessions SET status='working',turns=1 WHERE id=%s", (session['id'],))
        conn.execute(
            "UPDATE ka.agent_session_inputs SET status='delivered',turn=1,delivered_at=now() WHERE session_id=%s",
            (session['id'],),
        )
    assert client.post(url + '/messages', headers=HEADERS, json={'text': 'Shorten it.'}).json()['working'] is True
    stopped = client.post(url + '/stop', headers=HEADERS)
    assert stopped.status_code == 202 and stopped.json()['discarded'] == 1
    detail = client.get(url, headers=HEADERS).json()
    assert [(i['seq'], i['text'], i['status'], i['turn']) for i in detail['inputs'][1:]] == [
        (2, 'Also cover retries.', 'delivered', 1),
        (3, 'Shorten it.', 'discarded', None),
    ]
    with store.connect() as conn:
        assert conn.execute('SELECT stop_requested_at FROM ka.agent_sessions WHERE id=%s', (session['id'],)).fetchone()[
            'stop_requested_at'
        ]
    assert client.post(url + '/close', headers=HEADERS).status_code == 200
    detail = client.get(url, headers=HEADERS).json()
    assert (detail['status'], detail['status_message'], bool(detail['closed_at'])) == ('closed', 'Closed.', True)
    closed = client.post(url + '/messages', headers=HEADERS, json={'text': 'Hello?'})
    assert (
        closed.status_code == 409
        and closed.json()['error']['message'] == 'This session is closed. Start a new session.'
    )
    assert client.post(url + '/close', headers=HEADERS).status_code == 200  # idempotent
    listed = client.get('/api/sessions?purpose=documents', headers=HEADERS).json()['sessions']
    assert [(s['id'], s['status'], s['agent_name'], s['model_name'], s['queued']) for s in listed] == [
        (session['id'], 'closed', 'Ad-hoc agent', 'GPT', 0)
    ]
    assert 'draft' not in listed[0] and 'prompt_snapshot' not in listed[0]
    assert client.get(f'/api/sessions/{uuid4()}', headers=HEADERS).status_code == 404
    for path in (url + '/messages', url + '/stop', url + '/close'):
        assert client.post(path, json={'text': 'x'}).status_code == 403
    assert client.get('/api/sessions?purpose=documents').status_code == 403
    with sessions.store.connect() as conn:  # an agent with a session keeps it
        project, actor = (
            sessions.store.project(conn, 'shop'),
            sessions.store.actor(conn, 'admin-id', 'admin@example.test'),
        )
        add_chat_session(conn, project, actor, sessions.agent, sessions.claude, status='waiting')
    revision = client.get(f'/api/agents/{sessions.agent}', headers=HEADERS).json()['revision']
    assert (
        client.request(
            'DELETE', f'/api/agents/{sessions.agent}', headers=HEADERS, json={'revision': revision, 'confirmed': True}
        ).status_code
        == 409
    )  # retained session history


def test_idle_sessions_close_after_24_hours(sessions):
    ids = [start(sessions)['id'] for _ in range(3)]
    with sessions.store.connect() as conn:
        conn.execute(
            "UPDATE ka.agent_sessions SET last_activity_at=now()-interval '25 hours' WHERE id=ANY(%s::uuid[])",
            (ids[:2],),
        )
        conn.execute(
            "UPDATE ka.agent_sessions SET status='working' WHERE id=%s", (ids[1],)
        )  # a running turn is never idle
    listed = {
        s['id']: s for s in sessions.client.get('/api/sessions?purpose=documents', headers=HEADERS).json()['sessions']
    }
    assert (listed[ids[0]]['status'], listed[ids[0]]['status_message']) == ('closed', IDLE_CLOSED)
    assert (listed[ids[1]]['status'], listed[ids[2]]['status']) == ('working', 'starting')
    inputs = sessions.client.get(f'/api/sessions/{ids[0]}', headers=HEADERS).json()['inputs']
    assert [i['status'] for i in inputs] == ['discarded']


def parse(text: str) -> list[dict]:
    events = []
    for block in text.strip().split('\n\n'):
        fields = dict(line.split(': ', 1) for line in block.split('\n') if line and not line.startswith(':'))
        if 'event' in fields:
            events.append(
                {
                    'event': fields['event'],
                    'id': int(fields['id']) if 'id' in fields else None,
                    'data': json.loads(fields['data']),
                }
            )
    return events


def record(conn, session_id, turn: int, seq: int, kind: str, payload: dict) -> None:
    conn.execute(
        """INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
        SELECT project_id,%s,%s,%s,coalesce(agent_id,'ad-hoc'),%s FROM ka.agent_sessions WHERE id=%s""",
        (f'cc:{session_id}:{turn}', seq, kind, Jsonb(payload), session_id),
    )


def test_event_stream_replays_history_and_follows_a_fake_worker(sessions, live):
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
            conn.execute(
                "UPDATE ka.agent_sessions SET status='waiting',draft='# Payments API',draft_turn=1 WHERE id=%s", (sid,)
            )
        time.sleep(0.3)
        with store.connect() as conn:
            conn.execute("UPDATE ka.agent_sessions SET status='closed',closed_at=now() WHERE id=%s", (sid,))

    thread = threading.Thread(target=worker)
    thread.start()
    response = client.get(f'/api/sessions/{sid}/events', headers=HEADERS)
    thread.join()
    assert response.status_code == 200 and response.headers['content-type'].startswith('text/event-stream')
    assert response.headers['cache-control'] == 'no-store'
    events = parse(response.text)
    runs = [e for e in events if e['event'] == 'run_event']
    assert [(e['data']['turn'], e['data']['seq'], e['data']['kind']) for e in runs] == [
        (1, 1, 'start'),
        (1, 2, 'tool_call'),
        (1, 3, 'tool_result'),
        (1, 4, 'message'),
    ]
    assert all(e['id'] == e['data']['id'] for e in runs)
    assert runs[2]['data']['payload'] == {
        'name': 'Read',
        'preview': 'line\nline',
    }  # the full output stays in the run record
    states = [e['data'] for e in events if e['event'] == 'session']
    assert states[0]['status'] == 'working' and states[-1]['status'] == 'closed'
    assert any(s['status'] == 'waiting' and s['draft'] == '# Payments API' for s in states)
    assert events[-1]['event'] == 'closed'
    resumed = client.get(f'/api/sessions/{sid}/events', headers={**HEADERS, 'Last-Event-ID': str(runs[1]['id'])})
    assert [e['data']['kind'] for e in parse(resumed.text) if e['event'] == 'run_event'] == ['tool_result', 'message']
    after = client.get(f'/api/sessions/{sid}/events?after={runs[2]["id"]}', headers=HEADERS)
    assert [e['data']['kind'] for e in parse(after.text) if e['event'] == 'run_event'] == ['message']
    assert client.get(f'/api/sessions/{uuid4()}/events', headers=HEADERS).status_code == 404
    assert client.get(f'/api/sessions/{sid}/events').status_code == 403


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
    url = f'/api/sessions/{sid}/events/history'
    latest = client.get(url + '?limit=2', headers=HEADERS)
    assert latest.status_code == 200 and latest.headers['cache-control'] == 'no-store'
    page = latest.json()
    assert [(e['turn'], e['seq'], e['kind']) for e in page['events']] == [(2, 1, 'tool_result'), (2, 2, 'message')]
    assert page['has_more'] is True
    assert page['events'][0]['payload'] == {'name': 'Read', 'preview': 'line'}  # redacted like the stream
    assert 'the whole file body' not in latest.text
    older = client.get(f'{url}?before={page["events"][0]["id"]}&limit=2', headers=HEADERS).json()
    assert [e['payload']['text'] for e in older['events']] == ['one 2', 'one 3'] and older['has_more'] is True
    first = client.get(f'{url}?before={older["events"][0]["id"]}&limit=2', headers=HEADERS).json()
    assert [e['payload']['text'] for e in first['events']] == ['one 1'] and first['has_more'] is False
    default = client.get(url, headers=HEADERS).json()  # the latest 100: the whole session here, never another one's
    assert [e['seq'] for e in default['events']] == [1, 2, 3, 1, 2] and default['has_more'] is False
    assert all(e['id'] == r['id'] for e, r in zip(default['events'][-2:], page['events']))
    for bad in ('?limit=0', '?limit=501', '?before=0', '?before=x'):
        assert client.get(url + bad, headers=HEADERS).status_code == 400, bad
    assert client.get(f'/api/sessions/{uuid4()}/events/history', headers=HEADERS).status_code == 404
    assert client.get(url).status_code == 403


def test_idle_session_is_closed_when_opened_or_sent_to(sessions):
    sid = start(sessions)['id']
    url = f'/api/sessions/{sid}'
    with sessions.store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET last_activity_at=now()-interval '25 hours' WHERE id=%s", (sid,))
    detail = sessions.client.get(url, headers=HEADERS).json()
    assert (detail['status'], detail['status_message']) == ('closed', IDLE_CLOSED)
    other = start(sessions)['id']
    with sessions.store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET last_activity_at=now()-interval '25 hours' WHERE id=%s", (other,))
    sent = sessions.client.post(f'/api/sessions/{other}/messages', headers=HEADERS, json={'text': 'Hello?'})
    assert sent.status_code == 409 and sent.json()['error']['message'] == 'This session is closed. Start a new session.'


def test_event_stream_ends_when_the_session_is_gone_and_keeps_alive_sparingly(sessions, live, monkeypatch):
    monkeypatch.setattr(sessions_api, 'KEEPALIVE_SECONDS', 60)
    sid = start(sessions)['id']

    def remove():
        time.sleep(0.3)
        with sessions.store.connect() as conn:
            conn.execute('DELETE FROM ka.agent_session_inputs WHERE session_id=%s', (sid,))
            conn.execute('DELETE FROM ka.agent_sessions WHERE id=%s', (sid,))

    thread = threading.Thread(target=remove)
    thread.start()
    response = sessions.client.get(f'/api/sessions/{sid}/events', headers=HEADERS)
    thread.join()
    assert parse(response.text)[-1]['event'] == 'closed'
    assert ': keepalive' not in response.text  # well under KEEPALIVE_SECONDS
    assert live.subscribers(UUID(sid)) == 0  # unsubscribed and forgotten
    assert not hasattr(sessions_api, 'STREAM_POLL_SECONDS')
    assert sessions_api.KEEPALIVE_SECONDS == 60 and 10 <= sessions_api.DEFAULT_KEEPALIVE_SECONDS <= 20


def native_agent(client, name: str = 'Native writer', **change) -> str:
    """Create an agent as New agent does."""
    body = wizard_payload(name, description='', when_to_use='', system_prompt='You document systems.')
    response = client.post('/api/agents/wizard', headers=HEADERS, json={**body, **change})
    assert response.status_code == 201, response.text
    return response.json()['id']


def test_the_generation_api_moved(sessions):
    client = sessions.client
    assert client.get('/api/sessions?purpose=documents', headers=HEADERS).status_code == 200
    assert client.get('/api/claude-code/sessions', headers=HEADERS).status_code == 404
    assert client.post('/api/claude-code/context', headers=HEADERS, json={}).status_code == 404


def test_the_generate_documents_form_api_is_gone(sessions):
    for path in ('/api/generations/sessions', '/api/generations/context'):
        assert sessions.client.post(path, headers=HEADERS, json=sessions.body).status_code in (404, 405)
