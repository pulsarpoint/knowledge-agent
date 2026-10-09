"""Administration → Schedules and the Jira auto pull card."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from knowledge_admin import scheduler
from knowledge_admin.schedules import BAD_CRON, BAD_TIMEZONE, TOO_OFTEN
from test_api import _client
from test_jira import setup
from test_repositories import HEADERS

ON = {'enabled': True, 'cron': '*/15 * * * *', 'timezone': 'Europe/Belgrade'}


def error(response) -> str:
    return response.json()['error']['message']


def enable(client) -> dict:
    response = client.put('/api/jira/auto-pull', headers=HEADERS, json=ON)
    assert response.status_code == 200, response.text
    return response.json()['schedule']


def test_auto_pull_needs_jira_settings(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    assert client.get('/api/jira/auto-pull', headers=HEADERS).json() == {'schedule': None, 'jql': None, 'limit': None, 'connected': False}
    refused = client.put('/api/jira/auto-pull', headers=HEADERS, json=ON)
    assert refused.status_code == 400 and error(refused) == 'Save Jira settings first.'


def test_turning_auto_pull_on_creates_the_schedule(store, tmp_path):
    client, config = setup(store, tmp_path)
    schedule = enable(client)
    assert schedule['name'] == 'Jira auto pull' and schedule['target_kind'] == 'jira.pull' and schedule['target_id'] == config['id']
    assert schedule['description'] == 'Every 15 minutes' and schedule['timezone'] == 'Europe/Belgrade'
    assert schedule['enabled'] is True and schedule['running'] is False and schedule['next_run_at'] is not None
    state = client.get('/api/jira/auto-pull', headers=HEADERS).json()
    assert state['jql'] == 'ORDER BY updated DESC, key DESC' and state['limit'] == 25 and state['schedule']['id'] == schedule['id']
    assert [s['id'] for s in client.get('/api/schedules', headers=HEADERS).json()['schedules']] == [schedule['id']]
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.audit_events WHERE action='schedule.saved'").fetchone()['n'] == 1


def test_turning_auto_pull_off_keeps_the_schedule_paused(store, tmp_path):
    client, _ = setup(store, tmp_path)
    first = enable(client)
    off = client.put('/api/jira/auto-pull', headers=HEADERS, json={**ON, 'enabled': False}).json()['schedule']
    assert off['id'] == first['id'] and off['enabled'] is False and off['next_run_at'] is None and off['revision'] == 2


def test_bad_timetables_are_refused(store, tmp_path):
    client, _ = setup(store, tmp_path)
    for cron, timezone, message in [('*/2 * * * *', 'UTC', TOO_OFTEN), ('bad', 'UTC', BAD_CRON),
                                    ('0 * * * *', 'Mars/Base', BAD_TIMEZONE)]:
        for response in [client.put('/api/jira/auto-pull', headers=HEADERS, json={'enabled': True, 'cron': cron, 'timezone': timezone}),
                         client.post('/api/schedules/preview', headers=HEADERS, json={'cron': cron, 'timezone': timezone})]:
            assert response.status_code == 400 and error(response) == message
    assert client.get('/api/schedules', headers=HEADERS).json()['schedules'] == []


def test_preview_lists_the_next_three_runs(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    runs = client.post('/api/schedules/preview', headers=HEADERS, json={'cron': '0 6 * * *', 'timezone': 'UTC'}).json()['runs']
    times = [datetime.fromisoformat(run) for run in runs]
    assert len(times) == 3 and times == sorted(times) and all(t.hour == 6 and t > datetime.now(UTC) for t in times)


def test_preview_rejects_a_date_that_never_comes(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    response = client.post('/api/schedules/preview', headers=HEADERS, json={'cron': '0 0 30 2 *', 'timezone': 'UTC'})
    assert response.status_code == 400 and error(response) == BAD_CRON


def test_editing_checks_the_revision(store, tmp_path):
    client, _ = setup(store, tmp_path)
    schedule = enable(client)
    value = {'cron': '0 * * * *', 'timezone': 'UTC', 'enabled': True, 'revision': schedule['revision']}
    saved = client.put(f"/api/schedules/{schedule['id']}", headers=HEADERS, json=value)
    assert saved.status_code == 200 and saved.json()['schedule']['description'] == 'Every hour'
    stale = client.put(f"/api/schedules/{schedule['id']}", headers=HEADERS, json=value)
    assert stale.status_code == 409 and error(stale) == 'Schedule changed. Reload and try again.'
    missing = client.put(f'/api/schedules/{uuid4()}', headers=HEADERS, json=value)
    assert missing.status_code == 404 and error(missing) == 'Schedule not found.'


def test_run_now_and_history(store, tmp_path):
    client, _ = setup(store, tmp_path)
    schedule = enable(client)
    paused = client.put('/api/jira/auto-pull', headers=HEADERS, json={**ON, 'enabled': False}).json()['schedule']
    requested = client.post(f"/api/schedules/{paused['id']}/run", headers=HEADERS)
    assert requested.status_code == 200
    assert datetime.fromisoformat(requested.json()['schedule']['next_run_at']) <= datetime.now(UTC) + timedelta(seconds=1)
    assert scheduler.run_once(store, {'jira.pull': lambda store, s: ('succeeded', 'Pulled 7 of 7 tickets.')}) is True
    after = client.get('/api/schedules', headers=HEADERS).json()['schedules'][0]
    assert after['enabled'] is False and after['next_run_at'] is None and after['last_message'] == 'Pulled 7 of 7 tickets.'
    history = client.get(f"/api/schedules/{schedule['id']}/runs", headers=HEADERS).json()['runs']
    assert [(r['trigger'], r['status']) for r in history] == [('manual', 'succeeded')]
    assert client.get(f'/api/schedules/{uuid4()}/runs', headers=HEADERS).status_code == 404
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.audit_events WHERE action='schedule.run_requested'").fetchone()['n'] == 1


def test_run_now_is_refused_while_running(store, tmp_path):
    client, _ = setup(store, tmp_path)
    schedule = enable(client)
    with store.connect() as conn:
        conn.execute("UPDATE ka.schedules SET lease_expires_at=now()+interval '5 minutes' WHERE id=%s", (schedule['id'],))
    assert client.get('/api/schedules', headers=HEADERS).json()['schedules'][0]['running'] is True
    busy = client.post(f"/api/schedules/{schedule['id']}/run", headers=HEADERS)
    assert busy.status_code == 409 and error(busy) == 'This schedule is already running.'


def test_auto_pull_saves_its_own_query_and_limit(store, tmp_path):
    client, _ = setup(store, tmp_path)
    saved = client.put('/api/jira/auto-pull', headers=HEADERS, json={**ON, 'jql': 'project = AUTO', 'limit': 40}).json()
    assert saved['connected'] is True and saved['jql'] == 'project = AUTO' and saved['limit'] == 40
    assert saved['schedule']['params'] == {'jql': 'project = AUTO', 'limit': 40}
    assert client.get('/api/jira/auto-pull', headers=HEADERS).json()['jql'] == 'project = AUTO'


def test_auto_pull_without_query_keeps_the_stored_one(store, tmp_path):
    client, _ = setup(store, tmp_path)
    client.put('/api/jira/auto-pull', headers=HEADERS, json={**ON, 'jql': 'project = AUTO', 'limit': 40})
    again = client.put('/api/jira/auto-pull', headers=HEADERS, json={**ON, 'cron': '0 * * * *'}).json()
    assert again['schedule']['params'] == {'jql': 'project = AUTO', 'limit': 40} and again['schedule']['description'] == 'Every hour'


def test_auto_pull_query_and_limit_are_checked(store, tmp_path):
    client, _ = setup(store, tmp_path)
    for bad in [{'jql': ''}, {'jql': 'x' * 4001}, {'limit': 0}, {'limit': 201}, {'limit': '10'}]:
        assert client.put('/api/jira/auto-pull', headers=HEADERS, json={**ON, **bad}).status_code in (400, 422)


def test_create_jira_schedule_copies_the_connection_defaults(store, tmp_path):
    client, config = setup(store, tmp_path)
    created = client.post('/api/schedules', headers=HEADERS, json={'target_kind': 'jira.pull', 'cron': '0 * * * *', 'timezone': 'UTC', 'enabled': True})
    assert created.status_code == 201, created.text
    schedule = created.json()['schedule']
    assert schedule['name'] == 'Jira auto pull' and schedule['target_id'] == config['id'] and schedule['next_run_at'] is not None
    assert schedule['params'] == {'jql': 'ORDER BY updated DESC, key DESC', 'limit': 25}
    duplicate = client.post('/api/schedules', headers=HEADERS, json={'target_kind': 'jira.pull', 'cron': '0 * * * *', 'timezone': 'UTC', 'enabled': True})
    assert duplicate.status_code == 409 and error(duplicate) == 'This target already has a schedule.'
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.audit_events WHERE action='schedule.created'").fetchone()['n'] == 1


def test_create_needs_jira_and_a_known_target(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    value = {'target_kind': 'jira.pull', 'cron': '0 * * * *', 'timezone': 'UTC', 'enabled': True}
    refused = client.post('/api/schedules', headers=HEADERS, json=value)
    assert refused.status_code == 400 and error(refused) == 'Save Jira settings first.'
    assert client.post('/api/schedules', headers=HEADERS, json={**value, 'target_kind': 'agent.run'}).status_code in (400, 422)
    assert client.post('/api/schedules', headers=HEADERS, json={**value, 'cron': '*/2 * * * *'}).status_code == 400


def test_delete_removes_the_schedule_and_its_history(store, tmp_path):
    client, _ = setup(store, tmp_path)
    schedule = enable(client)
    client.post(f"/api/schedules/{schedule['id']}/run", headers=HEADERS)
    scheduler.run_once(store, {'jira.pull': lambda store, s: ('succeeded', 'ok')})
    assert client.delete(f"/api/schedules/{schedule['id']}", headers=HEADERS).status_code == 204
    assert client.get('/api/schedules', headers=HEADERS).json()['schedules'] == []
    assert client.delete(f"/api/schedules/{schedule['id']}", headers=HEADERS).status_code == 404
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.schedule_runs').fetchone()['n'] == 0
        assert conn.execute("SELECT count(*) AS n FROM ka.audit_events WHERE action='schedule.deleted'").fetchone()['n'] == 1


def test_deleting_a_running_schedule(store, tmp_path):
    client, _ = setup(store, tmp_path)
    schedule = enable(client)
    client.post(f"/api/schedules/{schedule['id']}/run", headers=HEADERS)

    def delete_meanwhile(store, claimed):
        assert client.delete(f"/api/schedules/{claimed['id']}", headers=HEADERS).status_code == 204
        return 'succeeded', 'ok'

    assert scheduler.run_once(store, {'jira.pull': delete_meanwhile}) is True
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.schedule_runs').fetchone()['n'] == 0
