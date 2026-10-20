"""Administration → Schedules: the only editor of the Jira pull schedule."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from knowledge_admin.events import scheduler
from knowledge_admin.events.schedules import BAD_CRON, BAD_TIMEZONE, TOO_OFTEN
from test_api import _client
from test_jira import setup
from test_repositories import HEADERS

ON = {'enabled': True, 'cron': '*/15 * * * *', 'timezone': 'Europe/Belgrade'}


def error(response) -> str:
    return response.json()['error']['message']


def enable(client, **params) -> dict:
    body = {**ON, 'target_kind': 'jira.pull', **({'params': params} if params else {})}
    response = client.post('/api/schedules', headers=HEADERS, json=body)
    assert response.status_code == 201, response.text
    return response.json()['schedule']


def edit(client, schedule: dict, **changes):
    value = {'cron': schedule['cron'], 'timezone': schedule['timezone'], 'enabled': schedule['enabled']}
    return client.put(
        f'/api/schedules/{schedule["id"]}', headers=HEADERS, json={**value, 'revision': schedule['revision'], **changes}
    )


def test_the_auto_pull_page_api_is_gone(store, tmp_path):
    client, _ = setup(store, tmp_path)
    assert client.get('/api/jira/auto-pull', headers=HEADERS).status_code == 404
    assert client.put('/api/jira/auto-pull', headers=HEADERS, json=ON).status_code in (404, 405)


def test_pausing_keeps_the_schedule(store, tmp_path):
    client, _ = setup(store, tmp_path)
    first = enable(client)
    off = edit(client, first, enabled=False).json()['schedule']
    assert off['id'] == first['id'] and off['enabled'] is False and off['next_run_at'] is None and off['revision'] == 2


def test_bad_timetables_are_refused(store, tmp_path):
    client, _ = setup(store, tmp_path)
    for cron, timezone, message in [
        ('*/2 * * * *', 'UTC', TOO_OFTEN),
        ('bad', 'UTC', BAD_CRON),
        ('0 * * * *', 'Mars/Base', BAD_TIMEZONE),
    ]:
        for response in [
            client.post(
                '/api/schedules',
                headers=HEADERS,
                json={'target_kind': 'jira.pull', 'enabled': True, 'cron': cron, 'timezone': timezone},
            ),
            client.post('/api/schedules/preview', headers=HEADERS, json={'cron': cron, 'timezone': timezone}),
        ]:
            assert response.status_code == 400 and error(response) == message
    assert client.get('/api/schedules', headers=HEADERS).json()['schedules'] == []


def test_preview_lists_the_next_three_runs(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    runs = client.post('/api/schedules/preview', headers=HEADERS, json={'cron': '0 6 * * *', 'timezone': 'UTC'}).json()[
        'runs'
    ]
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
    saved = client.put(f'/api/schedules/{schedule["id"]}', headers=HEADERS, json=value)
    assert saved.status_code == 200 and saved.json()['schedule']['description'] == 'Every hour'
    stale = client.put(f'/api/schedules/{schedule["id"]}', headers=HEADERS, json=value)
    assert stale.status_code == 409 and error(stale) == 'Schedule changed. Reload and try again.'
    missing = client.put(f'/api/schedules/{uuid4()}', headers=HEADERS, json=value)
    assert missing.status_code == 404 and error(missing) == 'Schedule not found.'


def test_run_now_and_history(store, tmp_path):
    client, _ = setup(store, tmp_path)
    schedule = enable(client)
    paused = edit(client, schedule, enabled=False).json()['schedule']
    requested = client.post(f'/api/schedules/{paused["id"]}/run', headers=HEADERS)
    assert requested.status_code == 200
    assert datetime.fromisoformat(requested.json()['schedule']['next_run_at']) <= datetime.now(UTC) + timedelta(
        seconds=1
    )
    assert scheduler.run_once(store, {'jira.pull': lambda store, s: ('succeeded', 'Pulled 7 of 7 tickets.')}) is True
    after = client.get('/api/schedules', headers=HEADERS).json()['schedules'][0]
    assert (
        after['enabled'] is False and after['next_run_at'] is None and after['last_message'] == 'Pulled 7 of 7 tickets.'
    )
    history = client.get(f'/api/schedules/{schedule["id"]}/runs', headers=HEADERS).json()['runs']
    assert [(r['trigger'], r['status']) for r in history] == [('manual', 'succeeded')]
    assert client.get(f'/api/schedules/{uuid4()}/runs', headers=HEADERS).status_code == 404
    with store.connect() as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM ka.audit_events WHERE action='schedule.run_requested'").fetchone()[
                'n'
            ]
            == 1
        )


def test_run_now_is_refused_while_running(store, tmp_path):
    client, _ = setup(store, tmp_path)
    schedule = enable(client)
    with store.connect() as conn:
        conn.execute(
            "UPDATE ka.schedules SET lease_expires_at=now()+interval '5 minutes' WHERE id=%s", (schedule['id'],)
        )
    assert client.get('/api/schedules', headers=HEADERS).json()['schedules'][0]['running'] is True
    busy = client.post(f'/api/schedules/{schedule["id"]}/run', headers=HEADERS)
    assert busy.status_code == 409 and error(busy) == 'This schedule is already running.'


def test_create_saves_the_jira_query_and_limit(store, tmp_path):
    client, _ = setup(store, tmp_path)
    schedule = enable(client, jql='  project = AUTO  ', limit=40)
    assert schedule['params'] == {'jql': 'project = AUTO', 'limit': 40}
    assert client.get('/api/schedules', headers=HEADERS).json()['schedules'][0]['params'] == schedule['params']


def test_editing_saves_the_jira_query_and_limit(store, tmp_path):
    client, _ = setup(store, tmp_path)
    schedule = enable(client)
    saved = edit(client, schedule, params={'jql': 'project = AUTO', 'limit': 40})
    assert saved.status_code == 200, saved.text
    assert saved.json()['schedule']['params'] == {'jql': 'project = AUTO', 'limit': 40}
    with store.connect() as conn:
        details = conn.execute(
            "SELECT details FROM ka.audit_events WHERE action='schedule.saved' ORDER BY id DESC LIMIT 1"
        ).fetchone()['details']
    assert details['jql'] == 'project = AUTO' and details['limit'] == 40


def test_editing_without_params_keeps_the_stored_ones(store, tmp_path):
    client, _ = setup(store, tmp_path)
    schedule = enable(client, jql='project = AUTO', limit=40)
    again = edit(client, schedule, cron='0 * * * *').json()['schedule']
    assert again['params'] == {'jql': 'project = AUTO', 'limit': 40} and again['description'] == 'Every hour'
    only_limit = edit(client, again, params={'limit': 10}).json()['schedule']
    assert only_limit['params'] == {'jql': 'project = AUTO', 'limit': 10}


def test_jira_query_and_limit_are_checked(store, tmp_path):
    client, _ = setup(store, tmp_path)
    schedule = enable(client)
    for bad in [
        {'jql': ''},
        {'jql': '   '},
        {'jql': 'x' * 4001},
        {'limit': 0},
        {'limit': 201},
        {'limit': '10'},
        {'x': 1},
    ]:
        body = {**ON, 'target_kind': 'jira.pull', 'params': bad}
        assert client.post('/api/schedules', headers=HEADERS, json=body).status_code in (400, 422), bad
        assert edit(client, schedule, params=bad).status_code in (400, 422), bad
    assert client.get('/api/schedules', headers=HEADERS).json()['schedules'][0]['params'] == {
        'jql': 'ORDER BY updated DESC, key DESC',
        'limit': 25,
    }


def test_the_run_uses_the_saved_query_and_limit(store, tmp_path):
    client, _ = setup(store, tmp_path)
    schedule = enable(client, jql='project = AUTO', limit=7)
    client.post(f'/api/schedules/{schedule["id"]}/run', headers=HEADERS)
    seen = []

    def pull(store, claimed):
        seen.append(claimed['params'])
        return 'succeeded', 'ok'

    assert scheduler.run_once(store, {'jira.pull': pull}) is True
    assert seen == [{'jql': 'project = AUTO', 'limit': 7}]


def test_create_jira_schedule_copies_the_connection_defaults(store, tmp_path):
    client, config = setup(store, tmp_path)
    created = client.post(
        '/api/schedules',
        headers=HEADERS,
        json={'target_kind': 'jira.pull', 'cron': '0 * * * *', 'timezone': 'UTC', 'enabled': True},
    )
    assert created.status_code == 201, created.text
    schedule = created.json()['schedule']
    assert (
        schedule['name'] == 'Jira auto pull'
        and schedule['target_id'] == config['id']
        and schedule['next_run_at'] is not None
    )
    assert schedule['params'] == {'jql': 'ORDER BY updated DESC, key DESC', 'limit': 25}
    duplicate = client.post(
        '/api/schedules',
        headers=HEADERS,
        json={'target_kind': 'jira.pull', 'cron': '0 * * * *', 'timezone': 'UTC', 'enabled': True},
    )
    assert duplicate.status_code == 409 and error(duplicate) == 'This target already has a schedule.'
    with store.connect() as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM ka.audit_events WHERE action='schedule.created'").fetchone()['n']
            == 1
        )


def test_create_needs_jira_and_a_known_target(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    value = {'target_kind': 'jira.pull', 'cron': '0 * * * *', 'timezone': 'UTC', 'enabled': True}
    refused = client.post('/api/schedules', headers=HEADERS, json=value)
    assert refused.status_code == 400 and error(refused) == 'Save Jira settings first.'
    assert client.post('/api/schedules', headers=HEADERS, json={**value, 'target_kind': 'agent.run'}).status_code in (
        400,
        422,
    )
    assert client.post('/api/schedules', headers=HEADERS, json={**value, 'cron': '*/2 * * * *'}).status_code == 400


def test_delete_removes_the_schedule_and_its_history(store, tmp_path):
    client, _ = setup(store, tmp_path)
    schedule = enable(client)
    client.post(f'/api/schedules/{schedule["id"]}/run', headers=HEADERS)
    scheduler.run_once(store, {'jira.pull': lambda store, s: ('succeeded', 'ok')})
    assert client.delete(f'/api/schedules/{schedule["id"]}', headers=HEADERS).status_code == 204
    assert client.get('/api/schedules', headers=HEADERS).json()['schedules'] == []
    assert client.delete(f'/api/schedules/{schedule["id"]}', headers=HEADERS).status_code == 404
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.schedule_runs').fetchone()['n'] == 0
        assert (
            conn.execute("SELECT count(*) AS n FROM ka.audit_events WHERE action='schedule.deleted'").fetchone()['n']
            == 1
        )


def test_deleting_a_running_schedule(store, tmp_path):
    client, _ = setup(store, tmp_path)
    schedule = enable(client)
    client.post(f'/api/schedules/{schedule["id"]}/run', headers=HEADERS)

    def delete_meanwhile(store, claimed):
        assert client.delete(f'/api/schedules/{claimed["id"]}', headers=HEADERS).status_code == 204
        return 'succeeded', 'ok'

    assert scheduler.run_once(store, {'jira.pull': delete_meanwhile}) is True
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.schedule_runs').fetchone()['n'] == 0
