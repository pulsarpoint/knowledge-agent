"""0077 removes the multi-agent board and the coordinator: their tables, board run records, the coordinator agent with
everything it owns, and every coordinator-only rule. Its down migration restores the empty schema only."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0077_remove_coordinator.up.sql', '0077_remove_coordinator.down.sql'
LATER = (
    '0084_remove_claude_code.down.sql',
    '0081_test_sessions.down.sql',
    '0080_clone_agents.down.sql',
    '0079_agent_system_prompt.down.sql',
)  # 0077 reads the prompt tables and versions
TABLES = ['agent_board_members', 'agent_board_tasks', 'coordinator_settings']
CHECKS = sorted(
    [
        'coordinator_always_enabled',
        'coordinator_delegates_resources',
        'claude_code_agent_is_not_the_coordinator',
        'per_run_agent_is_not_the_coordinator',
    ]
)
SEEDED = ['code', 'operations', 'search']


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def count(conn, sql, *args):
    return conn.execute(f'SELECT count(*) AS n FROM {sql}', args).fetchone()['n']


def schema(conn) -> dict:
    """What exists of the board and coordinator schema."""
    return {
        'tables': [t for t in TABLES if conn.execute('SELECT to_regclass(%s) AS r', (f'ka.{t}',)).fetchone()['r']],
        'column': count(
            conn,
            """information_schema.columns WHERE table_schema='ka' AND table_name='agents'
            AND column_name='is_coordinator'""",
        ),
        'checks': sorted(
            r['conname']
            for r in conn.execute(
                "SELECT conname FROM pg_constraint WHERE conrelid='ka.agents'::regclass AND conname = ANY(%s)",
                (CHECKS,),
            )
        ),
        'index': count(conn, "pg_indexes WHERE schemaname='ka' AND indexname='one_coordinator_agent'"),
        'trigger': count(conn, "pg_trigger WHERE tgname='protect_coordinator_agent'"),
        'function': count(
            conn,
            """pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='ka' AND p.proname='protect_coordinator_agent'""",
        ),
    }


GONE = {'tables': [], 'column': 0, 'checks': [], 'index': 0, 'trigger': 0, 'function': 0}
RESTORED = {'tables': TABLES, 'column': 1, 'checks': CHECKS, 'index': 1, 'trigger': 1, 'function': 1}


def agent_ids(conn) -> list[str]:
    return [row['id'] for row in conn.execute('SELECT id FROM ka.agents ORDER BY id')]


def board_and_coordinator(conn, project, actor) -> None:
    """As 0031 and the board made them: the coordinator with its prompt, a config row and its settings, a board task
    it delegated to the code agent with the member row, and run records of the board and of a test run."""
    conn.execute("""INSERT INTO ka.agents(id,name,description,when_to_use,is_coordinator)
        VALUES ('coordinator','Coordinator','Coordinate the selected specialists.','Every board task.',true)""")
    version = uuid4()
    conn.execute(
        "INSERT INTO ka.agent_prompts(agent,active_version_id,next_version) VALUES ('coordinator',%s,2)", (version,)
    )
    conn.execute(
        """INSERT INTO ka.agent_prompt_versions(id,agent,version,title,content,published_at)
        VALUES (%s,'coordinator',1,'Coordinator system prompt','Delegate and review evidence.',now())""",
        (version,),
    )
    conn.execute(
        """INSERT INTO ka.agent_configs(project_id,agent,instructions,inherit_skills)
        VALUES (%s,'coordinator','Prioritize evidence.',false)""",
        (project,),
    )
    conn.execute("INSERT INTO ka.coordinator_settings(agent_id,notes) VALUES ('coordinator','Prioritize evidence.')")
    root, child = uuid4(), uuid4()
    conn.execute(
        """INSERT INTO ka.agent_board_tasks(id,agent_id,agent_name,task,created_by,phase,status,finished_at)
        VALUES (%s,'coordinator','Coordinator','Investigate the incident.',%s,'done','completed',now())""",
        (root, actor),
    )
    conn.execute(
        """INSERT INTO ka.agent_board_tasks(id,agent_id,agent_name,task,created_by,parent_id,status,finished_at)
        VALUES (%s,'code','Code search agent','Read the logs.',%s,%s,'completed',now())""",
        (child, actor, root),
    )
    conn.execute("INSERT INTO ka.agent_board_members(task_id,agent_id) VALUES (%s,'code')", (root,))
    for key, agent in ((f'board:{root}', 'coordinator'), (f'run:{uuid4()}', 'code')):
        conn.execute(
            """INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,actor_id,payload)
            VALUES (%s,%s,1,'start',%s,%s,%s)""",
            (project, key, agent, actor, Jsonb({'kind': key.split(':')[0]})),
        )
    for agent in ('coordinator', 'code'):  # a chat and a run each; only the coordinator's go
        session = conn.execute(
            """INSERT INTO ka.agent_sessions(id,project_id,agent,actor_id,title,config,is_test)
            VALUES (%s,%s,%s,%s,'Chat','{}',false) RETURNING id""",
            (uuid4(), project, agent, actor),
        ).fetchone()['id']
        conn.execute(
            """INSERT INTO ka.operations_runs(id,session_id,project_id,actor_id,agent,task,context,status)
            VALUES (%s,%s,%s,%s,%s,'Check disk','{}','completed')""",
            (uuid4(), session, project, actor, agent),
        )
    conn.execute(
        """INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
        VALUES (%s,%s,'agent_board.submitted','agent_board_task',%s,'{}')""",
        (project, actor, root),
    )


def test_the_board_and_coordinator_schema_is_gone(store):
    with store.connect() as conn:
        assert schema(conn) == GONE
        assert agent_ids(conn) == SEEDED


def test_up_removes_board_records_and_the_coordinator_with_everything_it_owns(store):
    with store.connect() as conn:
        for name in LATER:
            run(conn, name)
        run(conn, DOWN)
        conn.commit()
        project, actor = store.project(conn, 'shop'), store.actor(conn, 'alice', 'alice@test')
        board_and_coordinator(conn, project, actor)
        conn.commit()
        run(conn, UP)
        conn.commit()
        assert schema(conn) == GONE
        assert agent_ids(conn) == SEEDED
        for table, column in (
            ('agent_prompts', 'agent'),
            ('agent_prompt_versions', 'agent'),
            ('agent_configs', 'agent'),
        ):
            assert count(conn, f"ka.{table} WHERE {column}='coordinator'") == 0, table
        assert count(conn, 'ka.agent_prompts WHERE agent = ANY(%s)', SEEDED) == 3  # other agents keep their prompts
        for table in ('agent_sessions', 'operations_runs'):
            assert [r['agent'] for r in conn.execute(f'SELECT agent FROM ka.{table}')] == ['code'], table
        assert [row['run_key'][:4] for row in conn.execute('SELECT run_key FROM ka.agent_run_events')] == ['run:']
        assert count(conn, "ka.audit_events WHERE action='agent_board.submitted'") == 1  # the audit log stays


def test_down_restores_the_empty_schema_so_older_rollbacks_still_run(store):
    with store.connect() as conn:
        for name in LATER:
            run(conn, name)
        run(conn, DOWN)
        conn.commit()
        assert schema(conn) == RESTORED
        assert count(conn, 'ka.agents WHERE is_coordinator') == 0  # the coordinator is not restored
        for table in TABLES:
            assert count(conn, f'ka.{table}') == 0, table
        with pytest.raises(psycopg.errors.CheckViolation, match='coordinator_always_enabled'):
            with conn.transaction():
                conn.execute("INSERT INTO ka.agents(id,name,is_coordinator,enabled) VALUES ('lead','Lead',true,false)")
        for name in (
            '0076_run_resources.down.sql',
            '0075_agent_resources.down.sql',
            '0075_agent_resources.up.sql',
            '0076_run_resources.up.sql',
            UP,
        ):
            run(conn, name)
            conn.commit()
        assert schema(conn) == GONE
