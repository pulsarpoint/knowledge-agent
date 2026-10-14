"""Session text: the agent's system prompt, idle closing and event run notifications (admin-api and worker)."""

from typing import Any

IDLE_CLOSED = 'Closed after 24 hours without activity.'
MAX_NOTIFICATION_BODY = 200_000
NO_ANSWER = '(The agent returned no answer.)'


def agent_system_prompt(config: dict[str, Any]) -> str:
    """The agent's instructions for a session: the active prompt, the agent's instructions and its enabled skills. What to write in this session stays in the first message."""
    parts = [config['system_prompt']['content'].strip()]
    if config['instructions'].strip():
        parts.append('## Instructions\n\n' + config['instructions'].strip())
    skills = [skill for skill in config['skills']['items'] if skill['enabled']] if config['skills']['enabled'] else []
    if skills:
        blocks = [
            '\n\n'.join(
                part
                for part in (f'### {skill["name"]}', skill['description'].strip(), skill['instructions'].strip())
                if part
            )
            for skill in skills
        ]
        parts.append('## Skills\n\n' + '\n\n'.join(blocks))
    return '\n\n'.join(parts)


def close_idle(conn) -> int:
    """Close sessions without activity for 24 hours (never one with a running turn); their history is kept."""
    rows = conn.execute(
        """UPDATE ka.agent_sessions SET status='closed',closed_at=now(),status_message=%s
        WHERE status IN ('starting','waiting','stopped','failed') AND last_activity_at < now() - interval '24 hours'
        RETURNING id""",
        (IDLE_CLOSED,),
    ).fetchall()
    if rows:
        conn.execute(
            "UPDATE ka.agent_session_inputs SET status='discarded' WHERE status='queued' AND session_id=ANY(%s)",
            ([row['id'] for row in rows],),
        )
        for row in rows:
            notify_closed_before_first_turn(conn, row['id'])
    return len(rows)


def notify_event_run(conn, session_id, number: int, status: str, text: str) -> None:
    """When an event run's first turn ends: one notification for the run, in the transaction that finishes the turn —
    the answer when the turn finished (`waiting`), else why it did not (the status message; for a run closed meanwhile,
    its close message). Number 0 is a run closed before its first turn. Other turns and runs that no event started
    write none, and a run never gets a second one."""
    if number > 1:
        return
    row = conn.execute(
        "SELECT status,status_message FROM ka.agent_sessions WHERE id=%s AND purpose='event'", (session_id,)
    ).fetchone()
    if row is None:
        return
    succeeded = status == 'waiting'
    if not succeeded and row['status'] == 'closed' and row['status_message']:
        text = row['status_message']
    body = (text if text.strip() else NO_ANSWER) if succeeded else text
    conn.execute(
        """INSERT INTO ka.notifications(project_id,kind,status,title,body,event_id,handler_id,agent_id,session_id)
        SELECT s.project_id,'agent.response',%s,left(s.title,500),%s,s.event_id,s.handler_id,s.agent_id,s.id
        FROM ka.agent_sessions s WHERE s.id=%s AND s.purpose='event'
          AND NOT EXISTS (SELECT 1 FROM ka.notifications n WHERE n.session_id=s.id AND n.kind='agent.response')""",
        ('succeeded' if succeeded else 'failed', body.replace('\x00', '')[:MAX_NOTIFICATION_BODY], session_id),
    )


def notify_closed_before_first_turn(conn, session_id) -> None:
    """A closed event run that never ran a turn: its failed notification (the close message), in the closing
    transaction."""
    row = conn.execute(
        "SELECT turns,status_message FROM ka.agent_sessions WHERE id=%s AND status='closed'", (session_id,)
    ).fetchone()
    if row is not None and row['turns'] == 0:
        notify_event_run(conn, session_id, 0, 'closed', row['status_message'])
