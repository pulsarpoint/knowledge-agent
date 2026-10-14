"""0101: the new-document prompt is in the prompt library; open Generate documents runs from before are closed."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from test_document_agent_migration import session, world
from old_documents import DOCUMENT_MODEL, before_0104  # noqa: F401  (fixture)

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0101_write_documents.up.sql', '0101_write_documents.down.sql'
CLOSED = 'Closed by an update; start a new chat.'
PROMPT = """SELECT p.tags,p.enabled,p.input_kind,v.content FROM ka.documentation_prompts p JOIN ka.documentation_prompt_versions v
    ON v.id=p.active_version_id WHERE p.project_id=%s AND p.name='Write a document'"""


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


pytestmark = pytest.mark.usefixtures('before_0104')


def test_every_workspace_gets_the_write_prompt_with_the_writing_rules(store):
    with store.connect() as conn:
        project = store.project(conn, f'shop-{uuid4().hex[:6]}')
        prompt = conn.execute(PROMPT, (project,)).fetchone()
    assert prompt['tags'] == ['documentation', 'new-document'] and prompt['enabled'] and prompt['input_kind'] == 'mixed'
    for words in ('save_document', 'edit_document', 'get_document', 'never'):
        assert words in prompt['content'].lower()


def test_up_closes_open_documents_runs_and_their_queued_messages_but_not_revision_runs(store):
    with store.connect() as conn:
        project, runbook = world(conn, store)
        generated = uuid4()
        conn.execute(
            """INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
            context_snapshot,purpose,document_name,folder,agent_config)
            SELECT %s,project_id,NULL,actor_id,model_id,'Payments','{}','{}','documents','Payments','payments','{}'
            FROM ka.agent_sessions WHERE id=%s""",
            (generated, session(conn, project, runbook)),
        )
        revision = session(conn, project, runbook)
        for sid in (generated, revision):
            conn.execute("UPDATE ka.agent_sessions SET status='working' WHERE id=%s", (sid,))
            conn.execute(
                """INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text,status)
                SELECT %s,1,actor_id,'Next.','queued' FROM ka.agent_sessions WHERE id=%s""",
                (sid, sid),
            )
        conn.commit()  # the migration files carry their own BEGIN/COMMIT
        run(conn, DOWN)
        run(conn, UP)
        rows = {
            row['id']: row
            for row in conn.execute(
                'SELECT id,status,status_message FROM ka.agent_sessions WHERE id=ANY(%s)', ([generated, revision],)
            )
        }
        queued = {
            row['session_id']: row['status']
            for row in conn.execute(
                'SELECT session_id,status FROM ka.agent_session_inputs WHERE session_id=ANY(%s)',
                ([generated, revision],),
            )
        }
    assert rows[generated]['status'] == 'closed' and rows[generated]['status_message'] == CLOSED
    assert rows[revision]['status'] == 'working'
    assert queued == {generated: 'discarded', revision: 'queued'}


def test_down_refuses_a_customized_prompt_then_round_trips(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute(
            "UPDATE ka.documentation_prompts SET revision=revision+1 WHERE project_id=%s AND name='Write a document'",
            (project,),
        )
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match='customized'):
            run(conn, DOWN)
        conn.rollback()
        conn.execute(
            "UPDATE ka.documentation_prompts SET revision=revision-1 WHERE project_id=%s AND name='Write a document'",
            (project,),
        )
        conn.commit()
        run(conn, DOWN)
        assert conn.execute(PROMPT, (project,)).fetchone() is None
        run(conn, UP)
        assert conn.execute(PROMPT, (project,)).fetchone() is not None
        run(conn, DOCUMENT_MODEL[1])
