"""Activity shows Reader conversations stored as live agent sessions."""
from uuid import uuid4

from psycopg.types.json import Jsonb

from test_api import _client
from test_repositories import HEADERS


def test_activity_lists_reader_sessions_only(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        alice = store.actor(conn, 'alice', 'alice@example.test')
        reader_session, admin_session = uuid4(), uuid4()
        for session_id, channel in ((reader_session, 'reader'), (admin_session, 'admin')):
            conn.execute('''INSERT INTO ka.agent_sessions(id,project_id,agent,actor_id,title,config,is_test,channel)
                VALUES (%s,%s,'search',%s,'How many retries?','{}',false,%s)''', (session_id, project, alice, channel))
            conn.execute('''INSERT INTO ka.operations_runs(project_id,actor_id,agent,session_id,task,context,status,answer)
                VALUES (%s,%s,'search',%s,'How many retries?',%s,'completed','Three [1].')''',
                (project, alice, session_id, Jsonb({'grounding': {'sources': [{'n': 1, 'path': 'p/r.md', 'heading_path': 'p', 'anchor': None}], 'not_covered': False}})))
    users = client.get('/api/admin/audit/users', headers=HEADERS).json()['users']
    assert users == [{'user_email': 'alice@example.test', 'sessions': 1, 'questions': 1, 'last_ts': users[0]['last_ts']}]
    sessions = client.get('/api/admin/audit/users/alice@example.test/sessions', headers=HEADERS).json()['sessions']
    assert [row['session_key'] for row in sessions] == [str(reader_session)]
    rows = client.get(f'/api/admin/audit/session/{reader_session}', headers=HEADERS).json()['rows']
    assert rows[0]['answer'] == 'Three [1].' and rows[0]['sources'][0]['path'] == 'p/r.md'
    assert rows[0]['sources_used'] == ['docs'] and rows[0]['agent_id'] == 'search' and rows[0]['not_covered'] is False
    assert client.get('/api/admin/audit/session/not-a-uuid', headers=HEADERS).status_code == 404
    assert client.get(f'/api/admin/audit/session/{admin_session}', headers=HEADERS).json()['rows'] == []
