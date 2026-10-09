"""Administration → Notifications: newest first, status filter, literal search, offset paging, one notification."""
from uuid import uuid4

from test_api import _client
from test_events_api import seed
from test_repositories import HEADERS


def add(store, title='Triage · jira.ticket.updated DEMO-1', body='Done.', status='succeeded', task=False, **values) -> int:
    with store.connect() as conn:
        project = values.pop('project_id', None) or store.project(conn, 'shop')
        key = None
        if task:
            key = f'agent-task:{values.get("event_id") or 1}:{uuid4()}'
            conn.execute("""INSERT INTO ka.agent_tasks(id,project_id,agent_id,agent_name,title,prompt,event_id,status,attempts,
                finished_at) VALUES (%s,%s,'triage','Triage',%s,'Hi',%s,'succeeded',2,now())""",
                (key, project, title, values.get('event_id')))
        return conn.execute("""INSERT INTO ka.notifications(project_id,kind,status,title,body,event_id,agent_id,agent_task_id)
            VALUES (%s,'agent.response',%s,%s,%s,%s,'triage',%s) RETURNING id""",
            (project, status, title, body, values.get('event_id'), key)).fetchone()['id']


def test_newest_first_with_offsets(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    ids = [add(store, title=f'T{n}') for n in range(3)]
    first = client.get('/api/notifications', headers=HEADERS, params={'limit': 2}).json()
    assert [row['id'] for row in first['notifications']] == [ids[2], ids[1]] and first['next_offset'] == 2
    rest = client.get('/api/notifications', headers=HEADERS, params={'limit': 2, 'offset': 2}).json()
    assert [row['id'] for row in rest['notifications']] == [ids[0]] and rest['next_offset'] is None


def test_rows_carry_a_preview_and_the_agent(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    [event] = seed(store, 1)
    add(store, body='x' * 300, task=True, event_id=event)
    [row] = client.get('/api/notifications', headers=HEADERS).json()['notifications']
    assert set(row) == {'id', 'created_at', 'kind', 'status', 'title', 'preview', 'event_id', 'agent_id', 'agent_name'}
    assert (row['preview'], row['agent_id'], row['agent_name'], row['event_id'], row['kind']) == (
        'x' * 200, 'triage', 'Triage', event, 'agent.response')


def test_status_filter(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    add(store, status='succeeded')
    failed = add(store, status='failed', body='Agent is disabled.')
    rows = client.get('/api/notifications', headers=HEADERS, params={'status': 'failed'}).json()['notifications']
    assert [row['id'] for row in rows] == [failed]
    assert client.get('/api/notifications', headers=HEADERS, params={'status': 'queued'}).status_code == 400


def test_search_matches_literally(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    percent = add(store, title='Triage · 100% done')
    add(store, title='Triage · 1000 done')
    underscore = add(store, title='Triage · a_b')
    add(store, title='Triage · axb')
    found = lambda q: [row['id'] for row in client.get('/api/notifications', headers=HEADERS, params={'q': q}).json()['notifications']]
    assert found('100%') == [percent]
    assert found('A_B') == [underscore]


def test_one_notification_with_its_event_and_task(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    [event] = seed(store, 1)
    note = add(store, body='The full answer.', task=True, event_id=event)
    one = client.get(f'/api/notifications/{note}', headers=HEADERS).json()['notification']
    assert (one['body'], one['event'], one['task']['attempts'], one['agent_name']) == (
        'The full answer.', {'id': event, 'type': 'jira.ticket.created', 'subject': 'DEMO-1'}, 2, 'Triage')
    assert one['task']['id'].startswith(f'agent-task:{event}:') and one['task']['finished_at'] is not None


def test_a_notification_whose_event_was_deleted(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    note = add(store)
    one = client.get(f'/api/notifications/{note}', headers=HEADERS).json()['notification']
    assert (one['event'], one['task'], one['event_id'], one['agent_name']) == (None, None, None, 'triage')


def test_missing_and_other_projects(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    with store.connect() as conn:
        other = conn.execute("INSERT INTO ka.projects(slug,name) VALUES ('other-n','Other') RETURNING id").fetchone()['id']
    hidden = add(store, project_id=other)
    assert client.get('/api/notifications', headers=HEADERS).json()['notifications'] == []
    response = client.get(f'/api/notifications/{hidden}', headers=HEADERS)
    assert (response.status_code, response.json()['error']['message']) == (404, 'Notification not found.')
