"""0099: an Ask agent run is an agent session (purpose revision) aimed at one document, and goes with it."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from session_rows import a_model
from old_documents import DOCUMENT_MODEL, before_0104, old_document  # noqa: F401  (fixture)

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0099_document_agent.up.sql', '0099_document_agent.down.sql'


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


pytestmark = pytest.mark.usefixtures('before_0104')


def world(conn, store):
    project = store.project(conn, 'shop')
    runbook = old_document(conn, project, 'runbook', 'Runbook', '# Runbook')
    return project, runbook['id']


def session(conn, project, document, purpose='revision'):
    sid = uuid4()
    actor = conn.execute('SELECT id FROM ka.principals LIMIT 1').fetchone()['id']
    conn.execute(
        """INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
        context_snapshot,purpose,target_document_id,agent_config) VALUES (%s,%s,NULL,%s,%s,'Revise','{}','{}',%s,%s,%s)""",
        (sid, project, actor, a_model(conn, actor), purpose, document, Jsonb({})),
    )
    return sid


def test_a_revision_run_needs_its_document_and_other_sessions_have_none(store):
    with store.connect() as conn:
        project, runbook = world(conn, store)
        session(conn, project, runbook)
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            session(conn, project, None)
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            session(conn, project, runbook, purpose='test')


def test_a_revision_run_goes_with_its_document_and_leaves_its_drafts(store):
    with store.connect() as conn:
        project, runbook = world(conn, store)
        sid = session(conn, project, runbook)
        conn.execute(
            'INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) SELECT %s,1,actor_id,%s FROM ka.agent_sessions WHERE id=%s',
            (sid, 'Add a section.', sid),
        )
        conn.execute('DELETE FROM ka.documents WHERE id=%s', (runbook,))
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions WHERE id=%s', (sid,)).fetchone()['n'] == 0


def test_down_refuses_while_revision_runs_exist_then_round_trips(store):
    with store.connect() as conn:
        project, runbook = world(conn, store)
        sid = session(conn, project, runbook)
        conn.commit()  # the migration files carry their own BEGIN/COMMIT
        with pytest.raises(psycopg.errors.RaiseException, match='Ask agent runs exist'):
            run(conn, DOWN)
        conn.rollback()
        conn.execute('DELETE FROM ka.agent_sessions WHERE id=%s', (sid,))
        conn.commit()
        run(conn, DOWN)
        assert not conn.execute("""SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='ka'
            AND table_name='agent_sessions' AND column_name='target_document_id') AS present""").fetchone()['present']
        run(conn, UP)
        session(conn, project, runbook)
        conn.commit()
        run(conn, DOCUMENT_MODEL[1])
