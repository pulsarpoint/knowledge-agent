"""0082: Generate documents sessions may run an ad-hoc agent (agent_id NULL, native, pinned config); Test sessions keep
their agent. Down deletes ad-hoc sessions and restores NOT NULL. Rollbacks run 0084 down first, as golang-migrate does."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from session_rows import a_model, add_ad_hoc_session, add_chat_session

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0082_ad_hoc_sessions.up.sql', '0082_ad_hoc_sessions.down.sql'
LATER = '0084_remove_claude_code.down.sql'


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def ad_hoc_before_0084(conn, project, actor, model_id):
    """An ad-hoc session as POST /api/generations/sessions inserted it before 0084 (ka.agent_sessions_cc, native)."""
    conn.execute(
        """INSERT INTO ka.agent_sessions_cc(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
        context_snapshot,document_name,folder,runtime,agent_config)
        VALUES (%s,%s,NULL,%s,%s,'Payments','{}','{}','Payments','payments','openai',%s)""",
        (uuid4(), project, actor, model_id, Jsonb({'id': 'ad-hoc'})),
    )


def test_only_documents_sessions_run_without_an_agent_and_down_removes_them(store):
    with store.connect() as conn:
        project, actor = store.project(conn, 'shop'), store.actor(conn, 'admin-id', 'admin@example.test')
        model = a_model(conn, actor)
        ad_hoc = add_ad_hoc_session(conn, project, actor, model, message='Document it')
        tested = add_chat_session(conn, project, actor, 'search', model)
        conn.commit()
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "UPDATE ka.agent_sessions SET purpose='test',document_name=NULL,folder=NULL WHERE id=%s", (ad_hoc,)
            )
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute('UPDATE ka.agent_sessions SET agent_id=NULL WHERE id=%s', (tested,))
        run(conn, LATER)
        run(conn, DOWN)
        conn.commit()
        ids = [row['id'] for row in conn.execute('SELECT id FROM ka.agent_sessions_cc')]
        assert ids == [tested]
        assert (
            conn.execute('SELECT count(*) AS n FROM ka.agent_session_inputs WHERE session_id=%s', (ad_hoc,)).fetchone()[
                'n'
            ]
            == 0
        )
        with pytest.raises(psycopg.errors.NotNullViolation), conn.transaction():
            ad_hoc_before_0084(conn, project, actor, model)
        run(conn, UP)
        conn.commit()
        ad_hoc_before_0084(conn, project, actor, model)
