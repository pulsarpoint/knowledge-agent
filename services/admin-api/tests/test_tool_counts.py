"""Which tools each run called and how often, from the run records' tool_call events."""

from psycopg.types.json import Jsonb

from knowledge_admin.run_record import tool_counts
from test_agent_runs_api import seed
from test_api import _client
from test_claude_code_sessions import sessions, start  # noqa: F401  (fixture)
from test_repositories import HEADERS


def calls(store, run_key, names, *, kind='tool_call', first_seq=10):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        for offset, name in enumerate(names):
            conn.execute('''INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
                VALUES (%s,%s,%s,%s,'search',%s)''', (project, run_key, first_seq + offset, kind, Jsonb({'name': name})))


def test_tool_counts_count_tool_calls_of_exactly_the_given_runs(store):
    calls(store, 'run:a', ['read_file', 'read_file', 'code_graph', 'shell_run', 'code_graph', 'read_file'])
    calls(store, 'run:b', ['ssh_run'])
    calls(store, 'run:a', ['read_file'], kind='tool_result', first_seq=50)
    calls(store, 'run:ab', ['read_file'])
    with store.connect() as conn:
        assert tool_counts(conn, ['run:a']) == [{'name': 'read_file', 'calls': 3}, {'name': 'code_graph', 'calls': 2},
                                                {'name': 'shell_run', 'calls': 1}]
        assert tool_counts(conn, ['run:a', 'run:b'])[-1] == {'name': 'ssh_run', 'calls': 1}
        assert tool_counts(conn, ['run:none']) == [] and tool_counts(conn, []) == []


def test_run_lists_and_records_carry_tool_counts(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed(store)
    calls(store, 'run:1', ['search_documents'])
    runs = {run['run_key']: run for run in client.get('/api/agent-runs', headers=HEADERS).json()['runs']}
    assert runs['run:1']['tool_counts'] == [{'name': 'search_documents', 'calls': 2}]
    assert runs['cc:s1:1']['tool_counts'] == []
    record = client.get('/api/agent-runs/run:1', headers=HEADERS).json()
    assert record['tool_counts'] == [{'name': 'search_documents', 'calls': 2}]


def test_sessions_count_only_their_own_turns_and_runs(store, sessions):
    session = start(sessions)
    sid = session['id']
    with store.connect() as conn:
        conn.execute('UPDATE ka.agent_sessions_cc SET turns=2 WHERE id=%s', (sid,))
    calls(store, f'cc:{sid}:1', ['Read', 'Grep'])
    calls(store, f'cc:{sid}:2', ['Read'])
    calls(store, f'cc:{sid}:3', ['Bash'])  # not a turn of this session yet
    calls(store, f'cc:{sid}0:1', ['Glob'])  # another session sharing the prefix
    detail = sessions.client.get(f'/api/claude-code/sessions/{sid}', headers=HEADERS).json()
    assert detail['tool_counts'] == [{'name': 'Read', 'calls': 2}, {'name': 'Grep', 'calls': 1}]
    with store.connect() as conn:  # the last tool_call counted, so a live view adds only later streamed calls
        last = conn.execute("SELECT max(id) AS id FROM ka.agent_run_events WHERE run_key=ANY(%s) AND kind='tool_call'",
                            ([f'cc:{sid}:1', f'cc:{sid}:2'],)).fetchone()['id']
    assert detail['tool_counts_through_id'] == last


def test_a_session_without_tool_calls_counts_through_zero(store, sessions):
    sid = start(sessions)['id']
    detail = sessions.client.get(f'/api/claude-code/sessions/{sid}', headers=HEADERS).json()
    assert detail['tool_counts'] == [] and detail['tool_counts_through_id'] == 0
