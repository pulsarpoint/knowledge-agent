"""Admins can list agent runs and read one run's full record."""
from psycopg.types.json import Jsonb

from test_api import _client
from test_repositories import HEADERS


def seed(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        alice = store.actor(conn, 'alice', 'alice@example.test')
        def add(run_key, seq, kind, payload, agent='search'):
            conn.execute('''INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,actor_id,payload)
                VALUES (%s,%s,%s,%s,%s,%s,%s)''', (project, run_key, seq, kind, agent, alice, Jsonb(payload)))
        add('run:1', 1, 'start', {'kind': 'test', 'title': 'How many retries?', 'agent': 'Search agent'})
        add('run:1', 2, 'tool_call', {'agent': 'Search agent', 'name': 'search_documents', 'arguments': '{"query":"retries"}'})
        add('run:1', 3, 'usage', {'agent': 'Search agent', 'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15})
        add('run:1', 4, 'end', {'status': 'completed'})
        add('cc:s1:1', 1, 'start', {'kind': 'claude_code', 'title': 'Payments API reference', 'agent': 'Technical writer'}, agent='technical-writer')
        add('cc:s1:1', 2, 'error', {'type': 'TimeoutError', 'message': 'late'}, agent='technical-writer')
        add('cc:s1:1', 3, 'end', {'status': 'failed'}, agent='technical-writer')


def test_list_runs_with_filters(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed(store)
    runs = client.get('/api/agent-runs', headers=HEADERS).json()['runs']
    assert [r['run_key'] for r in runs] == ['cc:s1:1', 'run:1']
    first = runs[1]
    assert first | {'started_at': None} == {'run_key': 'run:1', 'agent_id': 'search', 'agent': 'Search agent',
        'kind': 'test', 'title': 'How many retries?', 'user': 'alice@example.test', 'status': 'completed',
        'total_tokens': 15, 'started_at': None}
    assert runs[0]['status'] == 'failed'
    only = lambda **params: [r['run_key'] for r in client.get('/api/agent-runs', headers=HEADERS, params=params).json()['runs']]
    assert only(kind='test') == ['run:1']
    assert only(agent_id='technical-writer') == ['cc:s1:1']
    assert only(user='alice@example.test') == ['cc:s1:1', 'run:1']
    assert only(key_prefix='cc:s1:') == ['cc:s1:1']


def test_read_one_run(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed(store)
    record = client.get('/api/agent-runs/run:1', headers=HEADERS).json()
    assert record['run_key'] == 'run:1' and record['user'] == 'alice@example.test'
    assert [e['kind'] for e in record['events']] == ['start', 'tool_call', 'usage', 'end']
    assert record['events'][1]['payload']['name'] == 'search_documents'
    assert client.get('/api/agent-runs/run:missing', headers=HEADERS).status_code == 404


def test_runs_are_admin_only(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    for path in ('/api/agent-runs', '/api/agent-runs/run:1'):
        assert client.get(path).status_code == 403
        assert client.get(path, headers={'X-Forwarded-Groups': 'readers', 'X-Forwarded-User': 'u'}).status_code == 403
