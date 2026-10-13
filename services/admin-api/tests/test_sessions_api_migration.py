"""0100: the Ask agent prompt is in the prompt library; open Ask agent runs from before are closed."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from test_document_agent_migration import session, world

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0100_sessions_api.up.sql', '0100_sessions_api.down.sql'
PROMPT = """SELECT p.tags,p.enabled,v.content FROM ka.documentation_prompts p JOIN ka.documentation_prompt_versions v
    ON v.id=p.active_version_id WHERE p.project_id=%s AND p.name='Revise a document'"""


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def test_every_workspace_gets_the_revise_prompt_with_the_editing_rules(store):
    with store.connect() as conn:
        project = store.project(conn, f'shop-{uuid4().hex[:6]}')
        prompt = conn.execute(PROMPT, (project,)).fetchone()
    assert prompt['tags'] == ['document-revision'] and prompt['enabled']
    for words in ('get_document', 'edit_document', 'save_document', 'ask', 'never'):
        assert words in prompt['content'].lower()


def test_up_closes_open_ask_agent_runs_and_their_queued_messages_and_nothing_else(store):
    with store.connect() as conn:
        project, runbook = world(conn, store)
        open_run, closed_run = session(conn, project, runbook), session(conn, project, runbook)
        conn.execute("UPDATE ka.agent_sessions SET status='closed',closed_at=now() WHERE id=%s", (closed_run,))
        conn.execute("UPDATE ka.agent_sessions SET status='working' WHERE id=%s", (open_run,))
        conn.execute("""INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text,status)
            SELECT %s,1,actor_id,'Next.','queued' FROM ka.agent_sessions WHERE id=%s""", (open_run, open_run))
        conn.commit()  # the migration files carry their own BEGIN/COMMIT
        run(conn, DOWN)
        run(conn, UP)
        rows = {row['id']: row for row in conn.execute('SELECT id,status,status_message FROM ka.agent_sessions WHERE id=ANY(%s)',
                                                       ([open_run, closed_run],))}
        queued = conn.execute("SELECT status FROM ka.agent_session_inputs WHERE session_id=%s", (open_run,)).fetchone()
    assert rows[open_run]['status'] == 'closed' and rows[open_run]['status_message'] == 'Closed by an update; start a new chat.'
    assert rows[closed_run]['status_message'] != 'Closed by an update; start a new chat.'
    assert queued['status'] == 'discarded'


def test_down_refuses_a_customized_prompt_then_round_trips(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute("UPDATE ka.documentation_prompts SET revision=revision+1 WHERE project_id=%s AND name='Revise a document'", (project,))
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match='customized'):
            run(conn, DOWN)
        conn.rollback()
        conn.execute("UPDATE ka.documentation_prompts SET revision=revision-1 WHERE project_id=%s AND name='Revise a document'", (project,))
        conn.commit()
        run(conn, DOWN)
        assert conn.execute(PROMPT, (project,)).fetchone() is None
        run(conn, UP)
        assert conn.execute(PROMPT, (project,)).fetchone() is not None
