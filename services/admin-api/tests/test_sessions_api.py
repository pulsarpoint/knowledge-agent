"""/api/sessions: the run operations for every kind of run, and the lists."""
from test_sessions_world import ask, post  # noqa: F401  (fixtures)
from test_generations import sessions  # noqa: F401  (fixture)
from test_repositories import HEADERS


def test_old_session_routes_are_gone(sessions):
    some = '00000000-0000-0000-0000-000000000001'
    for method, path in (('get', f'/api/generations/sessions/{some}'), ('post', f'/api/generations/sessions/{some}/close'),
                         ('post', f'/api/generations/sessions/{some}/document'), ('get', '/api/agent-sessions/options')):
        assert getattr(sessions.client, method)(path, headers=HEADERS).status_code in (404, 405)


def test_the_lists_filter_by_document_and_by_purpose(ask):
    first, second = post(ask).json(), post(ask, message='Remove the old backup steps.').json()
    client = ask.s.client
    assert client.post(f"/api/sessions/{first['id']}/close", headers=HEADERS).status_code == 200
    listed = client.get(f"/api/sessions?document_id={ask.document['id']}", headers=HEADERS).json()['sessions']
    assert [row['id'] for row in listed] == [second['id'], first['id']]
    assert listed[0]['draft_id'] is None and listed[0]['started_by']
    assert client.get('/api/sessions?purpose=documents', headers=HEADERS).json()['sessions'] == []
    assert client.post(f"/api/sessions/{second['id']}/messages", headers=HEADERS, json={'text': 'Also cron.'}).status_code == 202
