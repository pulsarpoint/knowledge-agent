"""0084: Claude Code is removed. Claude Code agents become native with the tools their capabilities give, their sessions
are deleted, the agent type and Claude Code columns go, and ka.agent_sessions_cc becomes ka.agent_sessions (every
session native, its config pinned). Down brings the columns back with every agent and session native."""

import itertools
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from implied_tools import implied_tools
from session_rows import a_model, add_ad_hoc_session, add_review, add_chat_session
from test_api import _client
from test_repositories import HEADERS

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0084_remove_claude_code.up.sql', '0084_remove_claude_code.down.sql'
LATER = '0114_drop_agent_resource_tables.down.sql'  # 0084 down reads the capability switches
TOOLS = '0115_tools_always_on.down.sql'  # ka.agent_tools, which 0084 fills
GROUNDED = '0133_document_search.down.sql'  # agents.documents_grounded, which 0084 reads
FLAGS = ('repositories_enabled', 'documents_enabled', 'documents_grounded', 'ssh_enabled')


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def columns(conn, table: str) -> set[str]:
    return {
        row['column_name']
        for row in conn.execute(
            """SELECT column_name FROM information_schema.columns
        WHERE table_schema='ka' AND table_name=%s""",
            (table,),
        )
    }


def claude_code_agent(conn, agent_id: str, flags: dict[str, bool]) -> None:
    conn.execute(
        f"""INSERT INTO ka.agents(id,name,system_prompt,runtime,{','.join(FLAGS)})
        VALUES (%s,%s,'You help.','claude_code',%s,%s,%s,%s)""",
        (agent_id, agent_id, *(flags[f] for f in FLAGS)),
    )


def documents_session(conn, project, actor, model, agent_id: str | None, *, runtime: str) -> str:
    """A Generate documents session in the down state (ka.agent_sessions_cc) with input 1."""
    session = uuid4()
    conn.execute(
        """INSERT INTO ka.agent_sessions_cc(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
        context_snapshot,document_name,folder,runtime,agent_config)
        VALUES (%s,%s,%s,%s,%s,'Payments','{}','{}','Payments','payments',%s,%s)""",
        (session, project, agent_id, actor, model, runtime, Jsonb({'id': 'ad-hoc'}) if runtime == 'openai' else None),
    )
    conn.execute(
        "INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%s,1,%s,'Document it')",
        (session, actor),
    )
    return session


def test_claude_code_agents_become_native_and_their_sessions_go(store):
    with store.connect() as conn:
        project, actor = store.project(conn, 'shop'), store.actor(conn, 'admin-id', 'admin@example.test')
        model = a_model(conn, actor)
        run(conn, '0134_rag_gone.down.sql')  # 0114 down reads rag_collections
        run(conn, GROUNDED)
        run(conn, TOOLS)
        run(conn, LATER)
        run(conn, DOWN)
        combinations = {}
        for values in itertools.product((False, True), repeat=4):
            flags = dict(zip(FLAGS, values))
            agent_id = 'cc-' + ''.join('1' if v else '0' for v in values)
            claude_code_agent(conn, agent_id, flags)
            combinations[agent_id] = flags
        claude_code_agent(conn, 'cc-chosen', dict.fromkeys(FLAGS, True))
        conn.execute("INSERT INTO ka.agent_tools(agent_id,tool) VALUES ('cc-chosen','read_file')")
        claude = documents_session(conn, project, actor, model, 'cc-1111', runtime='claude_code')
        add_review(conn, claude, actor, changes={})
        native = documents_session(conn, project, actor, model, None, runtime='openai')
        run(conn, UP)
        conn.commit()

        for agent_id, flags in combinations.items():
            config = {
                'code': {'enabled': flags['repositories_enabled']},
                'documents': {'enabled': flags['documents_enabled'], 'grounded': flags['documents_grounded']},
                'ssh': {'enabled': flags['ssh_enabled']},
            }
            tools = {
                row['tool'] for row in conn.execute('SELECT tool FROM ka.agent_tools WHERE agent_id=%s', (agent_id,))
            }
            assert tools == {e['tool'] for e in implied_tools(config)['items'] if e['mode'] == 'on'}, agent_id
        assert [row['tool'] for row in conn.execute("SELECT tool FROM ka.agent_tools WHERE agent_id='cc-chosen'")] == [
            'read_file'
        ]

        assert [row['id'] for row in conn.execute('SELECT id FROM ka.agent_sessions')] == [native]
        for table in ('agent_session_inputs', 'agent_session_reviews'):
            assert [row['session_id'] for row in conn.execute(f'SELECT session_id FROM ka.{table}')] == (
                [native] if table == 'agent_session_inputs' else []
            )
        assert conn.execute("SELECT to_regclass('ka.agent_sessions_cc') AS r").fetchone()['r'] is None
        assert not {'runtime', 'claude_code_tools'} & columns(conn, 'agents')
        assert not {'runtime', 'claude_session_id', 'cost_usd'} & columns(conn, 'agent_sessions')
        names = [
            row['name']
            for row in conn.execute("""SELECT conname AS name FROM pg_constraint
            WHERE conrelid='ka.agent_sessions'::regclass UNION ALL
            SELECT indexname FROM pg_indexes WHERE schemaname='ka' AND tablename='agent_sessions'""")
        ]
        assert names and not [name for name in names if '_cc' in name], names
        assert 'agent_sessions_ad_hoc' in names

        with pytest.raises(psycopg.errors.CheckViolation, match='agent_sessions_ad_hoc'), conn.transaction():
            add_chat_session(conn, project, actor, None, model)
        with pytest.raises(psycopg.errors.NotNullViolation), conn.transaction():
            conn.execute('UPDATE ka.agent_sessions SET agent_config=NULL WHERE id=%s', (native,))

        run(conn, DOWN)
        conn.commit()
        assert conn.execute('SELECT runtime FROM ka.agent_sessions_cc WHERE id=%s', (native,)).fetchone() == {
            'runtime': 'openai'
        }
        assert {row['runtime'] for row in conn.execute('SELECT runtime FROM ka.agents')} == {'openai'}
        run(conn, UP)
        conn.commit()
        assert [row['id'] for row in conn.execute('SELECT id FROM ka.agent_sessions')] == [native]


def test_sessions_hold_status_folder_config_history_and_ordered_inputs(store):
    """The native session checks that 0070 and 0074 created survive the rename under their new names."""
    with store.connect() as conn:
        project, actor = store.project(conn, 'shop'), store.actor(conn, 'admin-id', 'admin@example.test')
        session = add_ad_hoc_session(conn, project, actor, a_model(conn, actor), message='Document it')
        assert conn.execute(
            'SELECT status,turns,draft,input_tokens,history FROM ka.agent_sessions WHERE id=%s', (session,)
        ).fetchone() == {'status': 'starting', 'turns': 0, 'draft': None, 'input_tokens': 0, 'history': []}
        for statement, error, name in (
            (
                "INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%(s)s,1,%(a)s,'Again')",
                psycopg.errors.UniqueViolation,
                'agent_session_inputs_session_id_seq_key',
            ),
            (
                "UPDATE ka.agent_session_inputs SET status='delivered' WHERE session_id=%(s)s",
                psycopg.errors.CheckViolation,
                'agent_session_inputs_check',
            ),
            (
                "UPDATE ka.agent_session_inputs SET status='later' WHERE session_id=%(s)s",
                psycopg.errors.CheckViolation,
                'agent_session_inputs_status_check',
            ),
            (
                "UPDATE ka.agent_sessions SET status='closed' WHERE id=%(s)s",
                psycopg.errors.CheckViolation,
                'agent_sessions_check',
            ),
            (
                "UPDATE ka.agent_sessions SET status='busy' WHERE id=%(s)s",
                psycopg.errors.CheckViolation,
                'agent_sessions_status_check',
            ),
            (
                "UPDATE ka.agent_sessions SET folder='Payments' WHERE id=%(s)s",
                psycopg.errors.CheckViolation,
                'agent_sessions_folder_check',
            ),
            (
                "UPDATE ka.agent_sessions SET folder='services/x' WHERE id=%(s)s",
                psycopg.errors.CheckViolation,
                'agent_sessions_folder_check',
            ),
            (
                "UPDATE ka.agent_sessions SET agent_config='[]' WHERE id=%(s)s",
                psycopg.errors.CheckViolation,
                'agent_sessions_agent_config',
            ),
            (
                "UPDATE ka.agent_sessions SET history='{}' WHERE id=%(s)s",
                psycopg.errors.CheckViolation,
                'agent_sessions_history',
            ),
        ):
            with pytest.raises(error, match=name), conn.transaction():
                conn.execute(statement, {'s': session, 'a': actor})
        conn.execute(
            """UPDATE ka.agent_session_inputs SET status='delivered',turn=1,delivered_at=now()
            WHERE session_id=%s""",
            (session,),
        )
        conn.execute(
            """UPDATE ka.agent_sessions SET history='[{"role": "user", "content": "Hi"}]',
            status='closed',closed_at=now() WHERE id=%s""",
            (session,),
        )


def test_a_session_pinned_with_claude_code_keys_still_loads(store, tmp_path):
    """Configs pinned before the removal keep their runtime and claude_code_tools keys; the session page still loads."""
    client = _client(tmp_path, repository_store=store)
    with store.connect() as conn:
        project, actor = store.project(conn, 'shop'), store.actor(conn, 'admin-id', 'admin@example.test')
        session = add_chat_session(
            conn,
            project,
            actor,
            'search',
            a_model(conn, actor),
            message='Hello',
            config={'id': 'search', 'runtime': 'claude_code', 'claude_code_tools': ['Read', 'Grep']},
        )
    response = client.get(f'/api/sessions/{session}', headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()['config']['claude_code_tools'] == ['Read', 'Grep']
