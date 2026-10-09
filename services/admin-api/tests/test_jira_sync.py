"""The Jira pull shared by Fetch tickets and the scheduled auto pull (target jira.pull)."""
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from knowledge_admin.jira_sync import CLASH_SKIPPED, auto_pull
from knowledge_admin.schedules import TargetMissing
from test_jira import SETTINGS, jira_response, setup
from test_repositories import HEADERS


def a_schedule(store) -> dict:
    with store.connect() as conn:
        connection = conn.execute('SELECT id,project_id FROM ka.jira_connections').fetchone()
    return {'id': uuid4(), 'project_id': connection['project_id'], 'target_kind': 'jira.pull', 'target_id': connection['id']}


def audits(store) -> list[dict]:
    with store.connect() as conn:
        return conn.execute("""SELECT actor_id,details FROM ka.audit_events
            WHERE action='jira.tickets_auto_pulled' ORDER BY id""").fetchall()


def test_auto_pull_uses_the_query_and_limit_of_the_last_fetch(store, tmp_path):
    client, _ = setup(store, tmp_path)
    assert client.post('/api/jira/tickets/fetch', headers=HEADERS, json={'limit': 5}).status_code == 200
    result = auto_pull(store, a_schedule(store), transport=httpx.MockTransport(jira_response))
    assert result == ('succeeded', 'Pulled 5 of 7 tickets.')
    assert audits(store) == [{'actor_id': None, 'details': {'requested': 5, 'fetched': 5, 'total': 7}}]
    tickets = client.get('/api/jira/tickets', headers=HEADERS).json()
    assert len(tickets['tickets']) == 5 and tickets['connection']['last_fetch_limit'] == 5


def test_auto_pull_before_any_fetch_uses_25(store, tmp_path):
    setup(store, tmp_path)
    assert auto_pull(store, a_schedule(store), transport=httpx.MockTransport(jira_response)) == ('succeeded', 'Pulled 7 of 7 tickets.')


def test_auto_pull_without_token_raises_the_button_error(store, tmp_path):
    client, config = setup(store, tmp_path)
    removed = client.put('/api/jira/settings', headers=HEADERS, json={
        'base_url': config['base_url'], 'username': config['username'], 'revision': 1, 'token': None})
    assert removed.status_code == 200
    with pytest.raises(HTTPException) as error:
        auto_pull(store, a_schedule(store), transport=httpx.MockTransport(jira_response))
    assert error.value.detail == 'Save a Jira URL, username and token in Jira Settings first'


def test_a_fetch_at_the_same_time_skips_the_run(store, tmp_path):
    setup(store, tmp_path)

    def clashing(request):
        if request.url.path.endswith('/search'):
            with store.connect() as conn:  # a manual fetch finishing while the auto pull talks to Jira
                conn.execute('UPDATE ka.jira_connections SET fetch_revision=fetch_revision+1')
        return jira_response(request)

    assert auto_pull(store, a_schedule(store), transport=httpx.MockTransport(clashing)) == ('skipped', CLASH_SKIPPED)
    assert audits(store) == []


def test_a_missing_connection_is_a_missing_target(store, tmp_path):
    setup(store, tmp_path)
    schedule = {**a_schedule(store), 'target_id': uuid4()}
    with pytest.raises(TargetMissing):
        auto_pull(store, schedule, transport=httpx.MockTransport(jira_response))


def test_the_token_never_reaches_the_audit(store, tmp_path):
    setup(store, tmp_path)
    auto_pull(store, a_schedule(store), transport=httpx.MockTransport(jira_response))
    assert SETTINGS['token'] not in str(audits(store))
