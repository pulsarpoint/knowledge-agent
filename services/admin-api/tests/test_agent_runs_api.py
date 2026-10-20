"""Admins can list agent runs and read one run's full record."""

from psycopg.types.json import Jsonb

from test_api import _client
from test_repositories import HEADERS


def seed(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        alice = store.actor(conn, 'alice', 'alice@example.test')

        def add(run_key, seq, kind, payload, agent='search'):
            conn.execute(
                """INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,actor_id,payload)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (project, run_key, seq, kind, agent, alice, Jsonb(payload)),
            )

        add('run:1', 1, 'start', {'kind': 'test', 'title': 'How many retries?', 'agent': 'Search agent'})
        add(
            'run:1',
            2,
            'tool_call',
            {'agent': 'Search agent', 'name': 'search_documents', 'arguments': '{"query":"retries"}'},
        )
        add('run:1', 3, 'usage', {'agent': 'Search agent', 'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15})
        add('run:1', 4, 'end', {'status': 'completed'})
        add(
            'cc:s1:1',
            1,
            'start',
            {'kind': 'claude_code', 'title': 'Payments API reference', 'agent': 'Technical writer'},
            agent='technical-writer',
        )
        add('cc:s1:1', 2, 'error', {'type': 'TimeoutError', 'message': 'late'}, agent='technical-writer')
        add('cc:s1:1', 3, 'end', {'status': 'failed'}, agent='technical-writer')


def test_list_runs_with_filters(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed(store)
    runs = client.get('/api/agent-runs', headers=HEADERS).json()['runs']
    assert [r['run_key'] for r in runs] == ['cc:s1:1', 'run:1']
    first = runs[1]
    assert first | {'started_at': None} == {
        'run_key': 'run:1',
        'agent_id': 'search',
        'agent': 'Search agent',
        'kind': 'test',
        'title': 'How many retries?',
        'user': 'alice@example.test',
        'status': 'completed',
        'total_tokens': 15,
        'started_at': None,
        'tool_counts': [{'name': 'search_documents', 'calls': 1}],
    }
    assert runs[0]['status'] == 'failed'
    only = lambda **params: [
        r['run_key'] for r in client.get('/api/agent-runs', headers=HEADERS, params=params).json()['runs']
    ]
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


def seed_expert_calls(store):
    """A caller run `cc:s2:1` (agent Lead) with expert calls e1 (answered), e2 (failed), e3 (no result yet)."""
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        alice = store.actor(conn, 'alice', 'alice@example.test')

        def add(seq, kind, payload, agent='lead', run_key='cc:s2:1'):
            conn.execute(
                """INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,actor_id,payload)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (project, run_key, seq, kind, agent, alice, Jsonb(payload)),
            )

        expert = {'id': 'payments', 'name': 'Payments'}
        add(1, 'start', {'kind': 'claude_code', 'title': 'Plan the release', 'agent': 'Lead'})
        add(2, 'tool_call', {'name': 'ask_payments', 'call_id': 'e1'})
        add(3, 'input', {'task': 'Map payments', 'expert': expert, 'parent_call_id': 'e1'}, agent='payments')
        add(4, 'usage', {'total_tokens': 120, 'parent_call_id': 'e1'}, agent='payments')
        add(5, 'tool_call', {'name': 'read_file', 'parent_call_id': 'e1'}, agent='payments')
        add(6, 'tool_result', {'call_id': 'e1', 'preview': 'Answer'})
        add(7, 'tool_call', {'name': 'ask_payments', 'call_id': 'e2'})
        add(8, 'input', {'task': 'x' * 300, 'expert': expert, 'parent_call_id': 'e2'}, agent='payments')
        add(9, 'tool_result', {'call_id': 'e2', 'preview': 'Expert Payments failed: boom'})
        add(10, 'tool_call', {'name': 'ask_payments', 'call_id': 'e3'})
        add(11, 'input', {'task': 'Still going', 'expert': expert, 'parent_call_id': 'e3'}, agent='payments')
        # a subagent the expert started: same agent, another call id, not part of the listing
        add(12, 'input', {'task': 'Helper', 'parent_call_id': 's1'}, agent='payments')


def seed_ended_and_renamed_calls(store):
    """Expert call e4 in a run that ended before it answered; e5 whose result only looks like another expert's failure."""
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        alice = store.actor(conn, 'alice', 'alice@example.test')

        def add(seq, kind, payload, agent='lead', run_key='cc:s2:1'):
            conn.execute(
                """INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,actor_id,payload)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (project, run_key, seq, kind, agent, alice, Jsonb(payload)),
            )

        expert = {'id': 'payments', 'name': 'Payments'}
        # a second run that ended (Stop) before its expert call answered
        add(1, 'start', {'kind': 'claude_code', 'title': 'Stopped', 'agent': 'Lead'}, run_key='cc:s2:2')
        add(2, 'tool_call', {'name': 'ask_payments', 'call_id': 'e4'}, run_key='cc:s2:2')
        add(
            3,
            'input',
            {'task': 'Cut short', 'expert': expert, 'parent_call_id': 'e4'},
            agent='payments',
            run_key='cc:s2:2',
        )
        add(4, 'end', {'status': 'stopped'}, run_key='cc:s2:2')
        # a result naming another expert is not this expert's failure
        add(13, 'tool_call', {'name': 'ask_payments', 'call_id': 'e5'})
        add(14, 'input', {'task': 'Name differs', 'expert': expert, 'parent_call_id': 'e5'}, agent='payments')
        add(15, 'tool_result', {'call_id': 'e5', 'preview': 'Expert Billing failed: not mine'})


def test_expert_calls_are_listed_for_the_expert(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed(store)
    seed_expert_calls(store)
    seed_ended_and_renamed_calls(store)
    listing = client.get('/api/agent-runs', headers=HEADERS, params={'agent_id': 'payments'}).json()
    rows = listing['runs']
    assert [r['call_id'] for r in rows] == ['e5', 'e4', 'e3', 'e2', 'e1'] and listing['total'] == 5
    done = rows[4]
    assert done | {'started_at': None, 'id': None} == {
        'run_key': 'cc:s2:1',
        'agent_id': 'payments',
        'agent': 'Payments',
        'kind': 'expert_call',
        'title': 'Map payments',
        'user': 'alice@example.test',
        'status': 'completed',
        'total_tokens': 120,
        'started_at': None,
        'tool_counts': [{'name': 'read_file', 'calls': 1}],
        'call_id': 'e1',
        'caller': 'Lead',
        'id': None,
    }
    assert rows[3]['status'] == 'failed' and rows[3]['title'] == 'x' * 200
    assert rows[2]['status'] == 'working'  # the run has not ended
    assert rows[2]['total_tokens'] == 0 and rows[2]['tool_counts'] == []
    assert rows[1]['status'] == 'stopped'  # no result, and the run ended
    assert rows[0]['status'] == 'completed'  # only Expert <its own name> failed: counts as a failure


def test_expert_calls_page_with_the_expert_runs(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed(store)
    seed_expert_calls(store)
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute(
            """INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
            VALUES (%s,'run:own',1,'start','payments',%s)""",
            (project, Jsonb({'kind': 'test', 'title': 'Own run', 'agent': 'Payments'})),
        )
    get = lambda **params: client.get(
        '/api/agent-runs', headers=HEADERS, params={'agent_id': 'payments'} | params
    ).json()
    everything = get()
    assert [r.get('call_id', r['run_key']) for r in everything['runs']] == ['run:own', 'e3', 'e2', 'e1']
    assert everything['total'] == 4
    first = get(page_size=25)
    assert first['page'] == 1 and first['page_size'] == 25
    assert [r['kind'] for r in get(status='working')['runs']] == ['test', 'expert_call']
    assert [r.get('call_id') for r in get(status='failed')['runs']] == ['e2']
    assert [r.get('call_id', r['run_key']) for r in get(order='asc')['runs']] == ['e1', 'e2', 'e3', 'run:own']
    assert [r.get('call_id', r['run_key']) for r in get(sort='tokens')['runs']] == ['e1', 'run:own', 'e3', 'e2']
    assert [r['kind'] for r in get(kind='expert_call')['runs']] == ['expert_call'] * 3
    assert [r['run_key'] for r in get(kind='test')['runs']] == ['run:own']


def test_caller_listing_has_no_expert_call_rows(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed_expert_calls(store)
    rows = client.get('/api/agent-runs', headers=HEADERS, params={'agent_id': 'lead'}).json()['runs']
    assert [(r['run_key'], r['kind']) for r in rows] == [('cc:s2:1', 'claude_code')]
    assert 'call_id' not in rows[0]


def test_read_one_expert_call(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed_expert_calls(store)
    record = client.get('/api/agent-runs/cc:s2:1', headers=HEADERS, params={'call': 'e1'}).json()
    assert [e['seq'] for e in record['events']] == [2, 3, 4, 5, 6]
    assert [e['kind'] for e in record['events']] == ['tool_call', 'input', 'usage', 'tool_call', 'tool_result']
    assert record['tool_counts'] == [{'name': 'read_file', 'calls': 1}]
    whole = client.get('/api/agent-runs/cc:s2:1', headers=HEADERS).json()
    assert len(whole['events']) == 12
    assert client.get('/api/agent-runs/cc:s2:1', headers=HEADERS, params={'call': 'nope'}).status_code == 404


def seed_expert_with_subagent(store):
    """Caller run `cc:s3:1`: expert call x1 whose expert starts one subagent (n1) with usage and a tool call; call x2
    is a different expert call with its own subagent that must stay out of x1."""
    with store.connect() as conn:
        project = store.project(conn, 'shop')

        def add(seq, kind, payload, agent='payments'):
            conn.execute(
                """INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
                VALUES (%s,'cc:s3:1',%s,%s,%s,%s)""",
                (project, seq, kind, agent, Jsonb(payload)),
            )

        expert = {'id': 'payments', 'name': 'Payments'}
        add(1, 'start', {'kind': 'claude_code', 'title': 'Plan', 'agent': 'Lead'}, agent='lead')
        add(2, 'tool_call', {'name': 'ask_payments', 'call_id': 'x1'}, agent='lead')
        add(3, 'input', {'task': 'Map payments', 'expert': expert, 'parent_call_id': 'x1'})
        add(4, 'usage', {'total_tokens': 100, 'parent_call_id': 'x1'})
        add(5, 'tool_call', {'name': 'run_subagent', 'call_id': 'n1', 'parent_call_id': 'x1'})
        add(6, 'input', {'task': 'Helper', 'parent_call_id': 'n1'})
        add(7, 'usage', {'total_tokens': 30, 'parent_call_id': 'n1'})
        add(8, 'tool_call', {'name': 'read_file', 'parent_call_id': 'n1'})
        add(9, 'tool_result', {'call_id': 'n1', 'preview': 'done', 'parent_call_id': 'x1'})
        add(10, 'tool_result', {'call_id': 'x1', 'preview': 'Answer'}, agent='lead')
        add(11, 'tool_call', {'name': 'ask_payments', 'call_id': 'x2'}, agent='lead')
        add(12, 'input', {'task': 'Other', 'expert': expert, 'parent_call_id': 'x2'})
        add(13, 'tool_call', {'name': 'run_subagent', 'call_id': 'n2', 'parent_call_id': 'x2'})
        add(14, 'usage', {'total_tokens': 7, 'parent_call_id': 'n2'})


def test_expert_call_includes_the_experts_subagents(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed_expert_with_subagent(store)
    rows = {
        r['call_id']: r
        for r in client.get('/api/agent-runs', headers=HEADERS, params={'agent_id': 'payments'}).json()['runs']
    }
    assert set(rows) == {'x1', 'x2'}
    assert rows['x1']['total_tokens'] == 130
    assert rows['x1']['tool_counts'] == [{'name': 'read_file', 'calls': 1}, {'name': 'run_subagent', 'calls': 1}]
    assert rows['x2']['total_tokens'] == 7
    record = client.get('/api/agent-runs/cc:s3:1', headers=HEADERS, params={'call': 'x1'}).json()
    assert [e['seq'] for e in record['events']] == [2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert record['tool_counts'] == [{'name': 'read_file', 'calls': 1}, {'name': 'run_subagent', 'calls': 1}]


def seed_many(store, count):
    """`count` runs run:0..run:{count-1}; run:i used i % 3 * 10 tokens; every third run is still working."""
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        alice = store.actor(conn, 'alice', 'Alice@Example.test')
        bob = store.actor(conn, 'bob', 'bob@example.test')
        for i in range(count):
            key = f'run:{i}'
            rows = [(1, 'start', {'kind': 'test', 'title': f'Run {i}', 'agent': 'Search'})]
            rows.append((2, 'usage', {'total_tokens': i % 3 * 10}))
            if i % 3:
                rows.append((3, 'end', {'status': 'completed' if i % 3 == 1 else 'stopped'}))
            for seq, kind, payload in rows:
                conn.execute(
                    """INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,actor_id,payload)
                    VALUES (%s,%s,%s,%s,'search',%s,%s)""",
                    (project, key, seq, kind, alice if i % 2 else bob, Jsonb(payload)),
                )


def test_runs_filter_by_status_and_user(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed_many(store, 6)
    keys = lambda **params: [
        r['run_key'] for r in client.get('/api/agent-runs', headers=HEADERS, params=params).json()['runs']
    ]
    assert keys(status='working') == ['run:3', 'run:0']  # no end event yet
    assert keys(status='completed') == ['run:4', 'run:1']
    assert keys(status='stopped') == ['run:5', 'run:2']
    assert keys(status='failed') == []
    assert keys(user='alice@') == ['run:5', 'run:3', 'run:1']  # contains, case-insensitive
    assert keys(user='EXAMPLE') == [f'run:{i}' for i in range(5, -1, -1)]
    assert keys(user='carol') == []
    assert client.get('/api/agent-runs', headers=HEADERS, params={'status': 'odd'}).status_code == 400


def test_runs_sort_by_start_or_tokens(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed_many(store, 6)
    keys = lambda **params: [
        r['run_key'] for r in client.get('/api/agent-runs', headers=HEADERS, params=params).json()['runs']
    ]
    assert keys() == keys(sort='started', order='desc') == [f'run:{i}' for i in range(5, -1, -1)]
    assert keys(order='asc') == [f'run:{i}' for i in range(6)]
    # tokens: 20 (run:2, run:5), 10 (run:1, run:4), 0 (run:0, run:3); ties by id in the same direction
    assert keys(sort='tokens') == ['run:5', 'run:2', 'run:4', 'run:1', 'run:3', 'run:0']
    assert keys(sort='tokens', order='asc') == ['run:0', 'run:3', 'run:1', 'run:4', 'run:2', 'run:5']
    assert client.get('/api/agent-runs', headers=HEADERS, params={'sort': 'title'}).status_code == 400


def test_runs_come_in_pages(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed_many(store, 60)
    get = lambda **params: client.get('/api/agent-runs', headers=HEADERS, params=params).json()
    first = get()
    assert (first['total'], first['page'], first['page_size'], len(first['runs'])) == (60, 1, 50, 50)
    assert first['runs'][0]['run_key'] == 'run:59' and first['runs'][-1]['run_key'] == 'run:10'
    second = get(page=2)
    assert [r['run_key'] for r in second['runs']] == [f'run:{i}' for i in range(9, -1, -1)] and second['total'] == 60
    small = get(page=3, page_size=25)
    assert [r['run_key'] for r in small['runs']] == [f'run:{i}' for i in range(9, -1, -1)]
    assert get(page=2, page_size=25)['runs'][0]['run_key'] == 'run:34'
    beyond = get(page=4, page_size=25)
    assert beyond['runs'] == [] and beyond['total'] == 60
    assert get(status='working', page_size=25)['total'] == 20
    for bad in ({'page_size': 10}, {'page_size': 200}, {'page': 0}):
        assert client.get('/api/agent-runs', headers=HEADERS, params=bad).status_code == 400


def test_expert_calls_are_paged_with_the_runs(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed_expert_calls(store)
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        for i in range(30):
            conn.execute(
                """INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
                VALUES (%s,%s,1,'start','payments',%s)""",
                (project, f'run:own:{i}', Jsonb({'kind': 'test', 'title': 'Own run', 'agent': 'Payments'})),
            )
    get = lambda **params: client.get(
        '/api/agent-runs', headers=HEADERS, params={'agent_id': 'payments', 'page_size': 25} | params
    ).json()
    first, second = get(), get(page=2)
    assert first['total'] == second['total'] == 33
    assert len(first['runs']) == 25 and first['runs'][0]['run_key'] == 'run:own:29'
    assert [r.get('call_id', r['run_key']) for r in second['runs']] == [f'run:own:{i}' for i in range(4, -1, -1)] + [
        'e3',
        'e2',
        'e1',
    ]
    assert 'call_id' not in second['runs'][0] and 'id' not in second['runs'][0]
    assert get(order='asc')['runs'][0]['call_id'] == 'e1'


def test_stopped_includes_cancelled_runs(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed_many(store, 3)  # run:2 ended stopped
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        for seq, kind, payload in (
            (1, 'start', {'kind': 'test', 'title': 'Cancelled'}),
            (2, 'end', {'status': 'cancelled'}),
        ):
            conn.execute(
                """INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
                VALUES (%s,'run:cancelled',%s,%s,'search',%s)""",
                (project, seq, kind, Jsonb(payload)),
            )
    rows = client.get('/api/agent-runs', headers=HEADERS, params={'status': 'stopped'}).json()['runs']
    assert [(r['run_key'], r['status']) for r in rows] == [('run:cancelled', 'cancelled'), ('run:2', 'stopped')]
