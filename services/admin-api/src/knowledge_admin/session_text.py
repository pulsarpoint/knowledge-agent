"""Session text: the agent's system prompt and idle closing (admin-api and worker)."""
from typing import Any

IDLE_CLOSED = 'Closed after 24 hours without activity.'


def agent_system_prompt(config: dict[str, Any]) -> str:
    """The agent's instructions for a session: the active prompt, the agent's instructions and its enabled skills. What to write in this session stays in the first message."""
    parts = [config['system_prompt']['content'].strip()]
    if config['instructions'].strip():
        parts.append('## Instructions\n\n' + config['instructions'].strip())
    skills = [skill for skill in config['skills']['items'] if skill['enabled']] if config['skills']['enabled'] else []
    if skills:
        blocks = ['\n\n'.join(part for part in (f"### {skill['name']}", skill['description'].strip(),
                                                skill['instructions'].strip()) if part) for skill in skills]
        parts.append('## Skills\n\n' + '\n\n'.join(blocks))
    return '\n\n'.join(parts)


def close_idle(conn) -> int:
    """Close sessions without activity for 24 hours (never one with a running turn); their history is kept."""
    rows = conn.execute("""UPDATE ka.agent_sessions SET status='closed',closed_at=now(),status_message=%s
        WHERE status IN ('starting','waiting','stopped','failed') AND last_activity_at < now() - interval '24 hours'
        RETURNING id""", (IDLE_CLOSED,)).fetchall()
    if rows:
        conn.execute("UPDATE ka.agent_session_inputs SET status='discarded' WHERE status='queued' AND session_id=ANY(%s)",
                     ([row['id'] for row in rows],))
    return len(rows)
