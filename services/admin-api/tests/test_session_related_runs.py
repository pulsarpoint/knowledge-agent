"""A run started by a Jira event lists the other runs on the same ticket (sessions.rows.session_detail)."""

from uuid import uuid4

from psycopg.types.json import Jsonb

from knowledge_admin.sessions.rows import session_detail
from session_rows import a_model, add_test_session

CONNECTION = str(uuid4())


def an_event(
    conn,
    project,
    *,
    source='jira',
    source_id=CONNECTION,
    issue_id='10001',
    subject='DEMO-1',
    type_='jira.ticket.updated',
):
    return conn.execute(
        """INSERT INTO ka.events(project_id,source,source_id,type,subject,title,payload,dedupe_key,occurred_at)
        VALUES (%s,%s,%s,%s,%s,'T',%s,%s,now()) RETURNING id""",
        (project, source, source_id, type_, subject, Jsonb({'issue_id': issue_id}), uuid4().hex),
    ).fetchone()['id']


def a_run(conn, project, actor, model, event=None):
    session = add_test_session(conn, project, actor, 'ops', model, purpose='event', status='waiting')
    conn.execute('UPDATE ka.agent_sessions SET event_id=%s WHERE id=%s', (event, session))
    return session


def test_runs_on_one_ticket_list_each_other(store):
    with store.connect() as conn:
        project, actor = store.project(conn, 'shop'), store.actor(conn, 'admin-id', 'admin@example.test')
        model = a_model(conn, actor)
        conn.execute("INSERT INTO ka.agents(id,name,system_prompt) VALUES ('ops','Ops agent','x')")
        first = a_run(conn, project, actor, model, an_event(conn, project, type_='jira.ticket.created'))
        second = a_run(conn, project, actor, model, an_event(conn, project))
        other_ticket = a_run(conn, project, actor, model, an_event(conn, project, issue_id='10002', subject='DEMO-2'))
        other_connection = a_run(conn, project, actor, model, an_event(conn, project, source_id=str(uuid4())))
        not_jira = a_run(conn, project, actor, model, an_event(conn, project, source='memory'))
        manual = a_run(conn, project, actor, model)

        detail = session_detail(conn, project, second)
        assert detail['ticket_key'] == 'DEMO-1'
        [related] = detail['related_runs']
        assert related['session_id'] == first and related['agent_id'] == 'ops'
        assert related['agent_name'] == 'Ops agent' and related['status'] == 'waiting'
        assert related['event_type'] == 'jira.ticket.created' and related['started_at']
        assert [r['session_id'] for r in session_detail(conn, project, first)['related_runs']] == [second]
        assert session_detail(conn, project, other_ticket)['related_runs'] == []
        assert session_detail(conn, project, other_connection)['related_runs'] == []
        for plain in (not_jira, manual):
            assert 'related_runs' not in session_detail(conn, project, plain)
