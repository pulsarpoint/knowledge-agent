"""Claude Code sessions: fixed tools, the built-in system prompt, the first message and idle closing (admin-api and worker)."""
from typing import Any

SYSTEM_PROMPT = ('You are an expert technical writer. You explore the provided folders and context to write accurate '
                 'documentation; everything you state must be grounded in what you read.')
BUILT_IN_PROMPT_TITLE = 'Built-in documentation writer'  # prompt version 1 of agents created before 0072
ALLOWED_TOOLS = ('Read', 'Grep', 'Glob', 'Bash')
DISALLOWED_TOOLS = ('Edit', 'Write', 'NotebookEdit', 'Agent', 'Task', 'Skill', 'WebFetch', 'WebSearch', 'TodoWrite')
FINAL_ANSWER = ('When you finish, reply with the complete document in Markdown as your final answer: it becomes the draft. '
                'After each later message, reply again with the complete updated document.')
IDLE_CLOSED = 'Closed after 24 hours without activity.'


def first_message(prompt: str, comments: str, context: dict[str, Any]) -> str:
    """The one message that starts a session: the prompt, this run's comments and the context section."""
    parts = [prompt.strip()]
    if comments.strip():
        parts.append('## Comments for this run\n\n' + comments.strip())
    lines = ['## Context', '', 'Working folders (read-only; the first one is your working directory):']
    lines += [f"- {f['path']}" + (f" (code project {f['name']})" if f['kind'] == 'code_project' else '') for f in context['folders']]
    if context['servers']:
        lines += ['', 'Servers (for reference only; you have no access to them):']
        lines += [f"- {s['name']} ({s['hostname']})" + (f": {s['description']}" if s['description'] else '') for s in context['servers']]
    if context['notes'].strip():
        lines += ['', 'Notes:', '', context['notes'].strip()]
    parts.append('\n'.join(lines))
    parts.append(FINAL_ANSWER)
    return '\n\n'.join(parts)


def close_idle(conn) -> int:
    """Close sessions without activity for 24 hours (never one with a running turn); their history is kept."""
    rows = conn.execute("""UPDATE ka.agent_sessions_cc SET status='closed',closed_at=now(),status_message=%s
        WHERE status IN ('starting','waiting','stopped','failed') AND last_activity_at < now() - interval '24 hours'
        RETURNING id""", (IDLE_CLOSED,)).fetchall()
    if rows:
        conn.execute("UPDATE ka.agent_session_inputs SET status='discarded' WHERE status='queued' AND session_id=ANY(%s)",
                     ([row['id'] for row in rows],))
    return len(rows)
