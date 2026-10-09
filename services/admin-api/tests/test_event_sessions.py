"""Sessions an event handler starts (purpose 'event'): start_session creates them like a Test session; they are listed
with the agent's Test sessions and shown the same way (configuration, suggestions)."""
import pytest

from knowledge_admin.agent_sessions import NO_MODEL, SessionRefused, start_session
from test_repositories import HEADERS
from test_test_sessions import tester  # noqa: F401  (fixture)


def started(t, message='Triage DEMO-1', **options):
    with t.store.connect() as conn:
        project = t.store.project(conn, 'shop')
        actor = t.store.actor(conn, 'admin-id', 'admin@example.test')
        return start_session(conn, project, t.agent, actor, message, conninfo=t.store.conninfo, **options)


def test_an_event_session_starts_like_a_test_session(tester):
    t = tester
    session_id = started(t, purpose='event', title='jira.ticket.created DEMO-1')
    detail = t.client.get(f'/api/generations/sessions/{session_id}', headers=HEADERS).json()
    assert (detail['purpose'], detail['status'], detail['title'], detail['agent_id']) == (
        'event', 'starting', 'jira.ticket.created DEMO-1', t.agent)
    assert [(i['seq'], i['text']) for i in detail['inputs']] == [(1, 'Triage DEMO-1')]
    assert detail['config']['id'] == t.agent and detail['reviews'] == []
    rows = t.client.get(t.path, headers=HEADERS).json()['sessions']
    assert [(row['id'], row['purpose']) for row in rows] == [(str(session_id), 'event')]


def test_refusals_carry_the_test_tab_status_and_message(tester):
    t = tester
    with t.store.connect() as conn:
        conn.execute('UPDATE ka.agents SET enabled=false WHERE id=%s', (t.agent,))
    with pytest.raises(SessionRefused) as raised:
        started(t, purpose='event')
    assert (raised.value.status, str(raised.value)) == (409, 'Agent is disabled.')
    with t.store.connect() as conn:
        conn.execute('UPDATE ka.agents SET enabled=true,model_id=NULL WHERE id=%s', (t.agent,))
    with pytest.raises(SessionRefused) as raised:
        started(t, purpose='event')
    assert (raised.value.status, str(raised.value)) == (409, NO_MODEL)
