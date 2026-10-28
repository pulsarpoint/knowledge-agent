"""0147: session review: agent_sessions.purpose takes 'review' (a reviewer chat on a finished session), a skill's
source may be {kind: review} (created disabled by a reviewer), and the single-shot tuning suggestions
(ka.agent_session_reviews) go. Down gives back the 0146 schema: review runs are deleted with their run records, review
skills become editor skills, and the suggestions table comes back empty."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from session_rows import a_model, add_chat_session
from test_rag_gone_migration import schema

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0147_session_review.up.sql', '0147_session_review.down.sql'


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


@pytest.fixture
def at_0146(postgres):
    database = f'test_{uuid4().hex}'
    with psycopg.connect(postgres, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    try:
        with psycopg.connect(f'{postgres} dbname={database}', row_factory=dict_row) as conn:
            for migration in sorted(MIGRATIONS.glob('*.up.sql')):
                if migration.name[:4] <= '0146':
                    conn.execute(migration.read_text(), prepare=False)
            yield schema(conn)
    finally:
        with psycopg.connect(postgres, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))


def exists(conn, table: str) -> bool:
    return conn.execute('SELECT to_regclass(%s) AS found', (f'ka.{table}',)).fetchone()['found'] is not None


def agent(conn, actor) -> str:
    agent_id = f'agent-{uuid4().hex[:8]}'
    conn.execute(
        """INSERT INTO ka.agents(id,name,description,system_prompt,created_by,updated_by)
        VALUES (%s,%s,'','Help.',%s,%s)""",
        (agent_id, agent_id, actor, actor),
    )
    return agent_id


def review_skill(conn, project, actor) -> str:
    skill_id = str(uuid4())
    conn.execute(
        """INSERT INTO ka.agent_skills(id,project_id,name,description,enabled,source,folder,created_by,updated_by)
        VALUES (%s,%s,%s,'Steps.',false,%s,%s,%s,%s)""",
        (
            skill_id,
            project,
            f'skill-{skill_id[:8]}',
            Jsonb({'kind': 'review', 'session_id': str(uuid4())}),
            f'skills/{skill_id}/v1',
            actor,
            actor,
        ),
    )
    return skill_id


def test_up_takes_review_runs_and_review_skills_and_drops_the_suggestions(store):
    with store.connect() as conn:
        assert not exists(conn, 'agent_session_reviews')
        project = store.project(conn, 'shop')
        actor = store.actor(conn, 'alice', 'alice@example.test')
        review = add_chat_session(conn, project, actor, agent(conn, actor), a_model(conn, actor), purpose='review')
        assert conn.execute('SELECT purpose FROM ka.agent_sessions WHERE id=%s', (review,)).fetchone() == {
            'purpose': 'review'
        }
        review_skill(conn, project, actor)
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            add_chat_session(conn, project, actor, agent(conn, actor), a_model(conn, actor), purpose='tuning')


def test_down_gives_back_the_0146_schema_and_up_again(store, at_0146):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        actor = store.actor(conn, 'alice', 'alice@example.test')
        agent_id = agent(conn, actor)
        model = a_model(conn, actor)
        review = add_chat_session(conn, project, actor, agent_id, model, purpose='review', message='Shorter.')
        chat = add_chat_session(conn, project, actor, agent_id, model)
        for key in (f'cc:{review}:1', f'cc:{chat}:1'):
            conn.execute(
                """INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,actor_id,payload)
                VALUES (%s,%s,1,'start',%s,%s,'{}')""",
                (project, key, agent_id, actor),
            )
        skill = review_skill(conn, project, actor)
        conn.commit()
        run(conn, DOWN)
        assert schema(conn) == at_0146
        assert conn.execute('SELECT id FROM ka.agent_sessions WHERE id=%s', (review,)).fetchone() is None
        assert conn.execute('SELECT id FROM ka.agent_sessions WHERE id=%s', (chat,)).fetchone() is not None
        keys = [
            row['run_key']
            for row in conn.execute('SELECT run_key FROM ka.agent_run_events WHERE project_id=%s', (project,))
        ]
        assert f'cc:{review}:1' not in keys and f'cc:{chat}:1' in keys
        source = conn.execute('SELECT source FROM ka.agent_skills WHERE id=%s', (skill,)).fetchone()['source']
        assert source == {'kind': 'editor'}
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_session_reviews').fetchone()['n'] == 0
        conn.commit()
        run(conn, UP)
        assert not exists(conn, 'agent_session_reviews')
