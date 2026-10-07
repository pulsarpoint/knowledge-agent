"""An agent with sessions or runs is deleted together with its history only when asked; nothing else goes with it."""
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from test_api import _client
from test_profiles import create
from test_repositories import HEADERS


def add_history(conn, agent_id: str, model_id: str) -> dict:
    """Give an agent one of every kind of history the delete has to remove, and return the ids."""
    project = conn.execute('SELECT id FROM ka.projects WHERE archived_at IS NULL LIMIT 1').fetchone()['id']
    actor = conn.execute('SELECT id FROM ka.principals LIMIT 1').fetchone()['id']
    ids = {'project': project, 'actor': actor}
    sessions = []
    for is_test in (True, True, False):
        session = uuid4()
        conn.execute('''INSERT INTO ka.agent_sessions(id,project_id,agent,actor_id,title,config,is_test)
            VALUES (%s,%s,%s,%s,'Chat','{}',%s)''', (session, project, agent_id, actor, is_test))
        sessions.append(session)
    ids['sessions'] = sessions
    review = conn.execute('''INSERT INTO ka.agent_session_reviews(session_id,actor_id,comment,context,suggestion)
        VALUES (%s,%s,'Too long','{}','Be brief') RETURNING id''', (sessions[0], actor)).fetchone()['id']
    ids['review'] = review
    # A saved configuration version made from that review (versions go with the agent).
    conn.execute('''INSERT INTO ka.agent_versions(agent,version,label,config,created_by,source_review_id)
        VALUES (%s,99,'From review','{}',%s,%s)''', (agent_id, actor, review))
    runs = []
    for session in (sessions[0], None):
        run = uuid4()
        conn.execute('''INSERT INTO ka.operations_runs(id,session_id,project_id,actor_id,agent,task,context,status)
            VALUES (%s,%s,%s,%s,%s,'Check disk','{}','completed')''', (run, session, project, actor, agent_id))
        conn.execute('''INSERT INTO ka.operations_commands(run_id,server_name,hostname,username,command,as_root)
            VALUES (%s,'web','web.local','ops','df -h',false)''', (run,))
        runs.append(run)
    ids['runs'] = runs
    document, version = uuid4(), uuid4()
    conn.execute('INSERT INTO ka.documents(id,project_id,slug,current_version_id) VALUES (%s,%s,%s,%s)',
                 (document, project, f'payments-{document.hex[:6]}', version))
    conn.execute("""INSERT INTO ka.document_versions(id,document_id,project_id,version,title,description,purpose,audience,
        markdown,content_hash,source_snapshot,origin,created_by) VALUES (%s,%s,%s,1,'Payments','','api','both',
        '# Payments',encode(sha256(convert_to('# Payments','UTF8')),'hex'),%s,'manual',%s)""",
        (version, document, project, Jsonb({'code_projects': []}), actor))
    ids['document'] = document
    cc = uuid4()
    conn.execute('''INSERT INTO ka.agent_sessions_cc(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
        context_snapshot,document_name,folder,turns,document_id,status) VALUES (%s,%s,%s,%s,%s,'Payments','{}','{}',
        'Payments','payments',2,%s,'waiting')''', (cc, project, agent_id, actor, model_id, document))
    conn.execute("INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%s,1,%s,'Write it')", (cc, actor))
    ids['cc'] = cc
    # As the board makes them: the coordinator's root task lists its team as members and delegates one subtask to
    # this agent and one to another agent; the whole run is recorded under board:<root id>.
    root, task, sibling = uuid4(), uuid4(), uuid4()
    conn.execute('''INSERT INTO ka.agent_board_tasks(id,agent_id,agent_name,task,created_by,status,finished_at)
        VALUES (%s,'coordinator','Coordinator','Plan',%s,'completed',now())''', (root, actor))
    for subtask, owner in ((task, agent_id), (sibling, 'code')):
        conn.execute('''INSERT INTO ka.agent_board_tasks(id,agent_id,agent_name,task,created_by,parent_id,status,
            finished_at) VALUES (%s,%s,'Agent','Do it',%s,%s,'completed',now())''', (subtask, owner, actor, root))
    conn.execute('INSERT INTO ka.agent_board_members(task_id,agent_id) VALUES (%s,%s),(%s,%s)',
                 (root, agent_id, root, 'code'))
    ids.update(root=root, task=task, sibling=sibling)
    keys = [f'run:{runs[0]}', f'run:{runs[1]}', f'cc:{cc}:1', f'cc:{cc}:2']
    for key in keys:
        conn.execute("""INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
            VALUES (%s,%s,1,'start',%s,'{}')""", (project, key, agent_id))
    conn.execute("""INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
        VALUES (%s,%s,1,'start','coordinator','{}')""", (project, f'board:{root}'))
    ids['keys'] = keys
    return ids


@pytest.fixture
def agents(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    model = client.post('/api/llm/models', headers=HEADERS, json={
        'name': 'GPT', 'provider': 'openai', 'model': 'gpt-5', 'api_format': 'responses',
        'base_url': 'http://model.test/v1'}).json()['id']
    target, other = create(client, 'Target agent'), create(client, 'Other agent')
    with store.connect() as conn:
        mine = add_history(conn, target['id'], model)
        theirs = add_history(conn, other['id'], model)
    return client, target['id'], other['id'], mine, theirs


def delete(client, agent_id, **body):
    revision = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()['revision']
    return client.request('DELETE', f'/api/agents/{agent_id}', headers=HEADERS,
                          json={'revision': revision, 'confirmed': True, **body})


def test_history_counts_what_a_delete_would_remove(agents):
    client, target, _, _, _ = agents
    history = client.get(f'/api/agents/{target}/history', headers=HEADERS)
    assert history.status_code == 200, history.text
    assert history.json() == {'test_chats': 2, 'chats': 1, 'claude_code_sessions': 1, 'runs': 2,
                              'board_tasks': 1, 'board_memberships': 1}
    assert client.get(f'/api/agents/{target}/history').status_code == 403
    assert client.get('/api/agents/missing/history', headers=HEADERS).status_code == 404


def test_an_agent_without_history_has_none(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    agent = create(client, 'Fresh agent')
    assert client.get(f"/api/agents/{agent['id']}/history", headers=HEADERS).json() == {
        'test_chats': 0, 'chats': 0, 'claude_code_sessions': 0, 'runs': 0, 'board_tasks': 0, 'board_memberships': 0}


def test_delete_without_history_flag_is_refused(agents, store):
    client, target, _, _, _ = agents
    refused = delete(client, target)
    assert refused.status_code == 409
    assert refused.json()['error']['message'] == ('This agent has sessions or run history. Delete it with its history, '
                                                  'or disable it to keep the history.')
    assert client.get(f'/api/agents/{target}', headers=HEADERS).status_code == 200


def test_delete_with_history_removes_only_this_agents_history(agents, store):
    client, target, other, mine, theirs = agents
    deleted = delete(client, target, with_history=True)
    assert deleted.status_code == 204, deleted.text
    assert client.get(f'/api/agents/{target}', headers=HEADERS).status_code == 404
    with store.connect() as conn:
        def count(sql, *args):
            return conn.execute(f'SELECT count(*) AS n FROM {sql}', args).fetchone()['n']
        for agent_id, expected in ((target, 0), (other, 1)):
            ids = mine if agent_id == target else theirs
            assert count('ka.agent_sessions WHERE agent=%s', agent_id) == 3 * expected
            assert count('ka.agent_session_reviews WHERE id=%s', ids['review']) == expected
            assert count('ka.operations_runs WHERE agent=%s', agent_id) == 2 * expected
            assert count('ka.operations_commands WHERE run_id=ANY(%s)', ids['runs']) == 2 * expected
            assert count('ka.agent_sessions_cc WHERE agent_id=%s', agent_id) == expected
            assert count('ka.agent_session_inputs WHERE session_id=%s', ids['cc']) == expected
            assert count('ka.agent_board_tasks WHERE id=%s', ids['task']) == expected
            assert count('ka.agent_board_members WHERE agent_id=%s', agent_id) == expected
            assert count('ka.agent_run_events WHERE run_key=ANY(%s)', ids['keys']) == 4 * expected
            assert (count('ka.agent_versions WHERE agent=%s', agent_id) > 0) == bool(expected)
            # Saved documents, the coordinator's root task with its run record, and the other agent's subtask stay.
            assert count('ka.documents WHERE id=%s', ids['document']) == 1
            assert count('ka.agent_board_tasks WHERE id=ANY(%s)', [ids['root'], ids['sibling']]) == 2
            assert count('ka.agent_board_members WHERE task_id=%s AND agent_id=%s', ids['root'], 'code') == 1
            assert count('ka.agent_run_events WHERE run_key=%s', f"board:{ids['root']}") == 1
        audit = conn.execute("""SELECT details FROM ka.audit_events WHERE action='agent.deleted'
            ORDER BY created_at DESC LIMIT 1""").fetchone()['details']
    assert audit['agent_id'] == target
    assert audit['history'] == {'test_chats': 2, 'chats': 1, 'claude_code_sessions': 1, 'runs': 2,
                                'board_tasks': 1, 'board_memberships': 1}
    assert client.get('/api/agent-board/tasks', headers=HEADERS).status_code == 200
    assert client.get(f'/api/agents/{other}/history', headers=HEADERS).json()['runs'] == 2


def test_delete_with_history_still_checks_the_coordinator_and_revision(agents):
    client, target, _, _, _ = agents
    refused = delete(client, 'coordinator', with_history=True)
    assert refused.status_code == 409 and 'cannot be removed' in refused.text
    stale = client.request('DELETE', f'/api/agents/{target}', headers=HEADERS,
                           json={'revision': 999, 'confirmed': True, 'with_history': True})
    assert stale.status_code == 409 and 'Reload before deleting' in stale.text
    assert client.get(f'/api/agents/{target}/history', headers=HEADERS).json()['runs'] == 2


def test_board_memberships_count_every_member_row_the_delete_removes(agents, store):
    client, target, _, _, _ = agents
    # A subtask of this agent with a child of another agent who is a member of it: the delete removes that row too.
    with store.connect() as conn:
        actor = conn.execute('SELECT id FROM ka.principals LIMIT 1').fetchone()['id']
        task = conn.execute('SELECT id FROM ka.agent_board_tasks WHERE agent_id=%s', (target,)).fetchone()['id']
        child = uuid4()
        conn.execute('''INSERT INTO ka.agent_board_tasks(id,agent_id,agent_name,task,created_by,parent_id,status,
            finished_at) VALUES (%s,'code','Code','Help',%s,%s,'completed',now())''', (child, actor, task))
        conn.execute("INSERT INTO ka.agent_board_members(task_id,agent_id) VALUES (%s,'code')", (child,))
    history = client.get(f'/api/agents/{target}/history', headers=HEADERS).json()
    assert (history['board_tasks'], history['board_memberships']) == (2, 2)
    assert delete(client, target, with_history=True).status_code == 204
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_board_members WHERE task_id=%s', (child,)).fetchone()['n'] == 0


@pytest.mark.parametrize('working', [
    "UPDATE ka.agent_sessions_cc SET status='working' WHERE agent_id=%(a)s",
    "UPDATE ka.agent_sessions_cc SET status='starting' WHERE agent_id=%(a)s",
    "UPDATE ka.operations_runs SET status='running' WHERE agent=%(a)s",
    "UPDATE ka.agent_board_tasks SET status='running',finished_at=NULL WHERE agent_id=%(a)s",
    # The coordinator's root task is still running with this agent on its team.
    """UPDATE ka.agent_board_tasks SET status='queued',finished_at=NULL WHERE id IN (
        SELECT task_id FROM ka.agent_board_members WHERE agent_id=%(a)s)""",
])
def test_a_working_agent_is_not_deleted(agents, store, working):
    client, target, _, _, _ = agents
    with store.connect() as conn:
        conn.execute(working, {'a': target})
    for body in ({'with_history': True}, {}):
        refused = delete(client, target, **body)
        assert refused.status_code == 409
        assert refused.json()['error']['message'] == (
            'This agent is working right now. Stop its Claude Code sessions, runs and board tasks, '
            'or wait for them to finish, then delete it.')
    history = client.get(f'/api/agents/{target}/history', headers=HEADERS).json()
    assert history == {'test_chats': 2, 'chats': 1, 'claude_code_sessions': 1, 'runs': 2,
                       'board_tasks': 1, 'board_memberships': 1}


def test_waiting_or_closed_claude_code_sessions_do_not_block(agents, store):
    client, target, _, _, _ = agents
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions_cc SET status='closed',closed_at=now() WHERE agent_id=%s", (target,))
    assert delete(client, target, with_history=True).status_code == 204
