"""0085: agents can be offered as experts; a caller's expert:<id> rows are never given to subagents. Down removes the
column and the rows and restores the old check."""
from pathlib import Path

import psycopg
import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0085_agent_experts.up.sql', '0085_agent_experts.down.sql'


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def an_agent(conn, name: str) -> str:
    return conn.execute("""INSERT INTO ka.agents(id,name,description,when_to_use,enabled,system_prompt)
        VALUES (gen_random_uuid()::text,%s,'','',true,'Prompt') RETURNING id""", (name,)).fetchone()['id']


def test_expert_column_defaults_off_and_expert_rows_are_never_for_subagents(store):
    with store.connect() as conn:
        caller, expert = an_agent(conn, 'Caller'), an_agent(conn, 'Payments')
        assert conn.execute('SELECT expert FROM ka.agents WHERE id=%s', (expert,)).fetchone()['expert'] is False
        conn.execute("INSERT INTO ka.agent_tools(agent_id,tool,mode,subagent_allowed) VALUES (%s,%s,'off',false)",
                     (caller, f'expert:{expert}'))
    with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
        conn.execute("INSERT INTO ka.agent_tools(agent_id,tool,mode,subagent_allowed) VALUES (%s,%s,'off',true)",
                     (caller, f'expert:{caller}'))


def test_down_removes_the_column_and_the_rows_and_up_restores_the_column(store):
    with store.connect() as conn:
        caller, expert = an_agent(conn, 'Caller'), an_agent(conn, 'Payments')
        conn.execute('UPDATE ka.agents SET expert=true WHERE id=%s', (expert,))
        conn.execute("INSERT INTO ka.agent_tools(agent_id,tool,mode,subagent_allowed) VALUES (%s,%s,'off',false)",
                     (caller, f'expert:{expert}'))
        run(conn, DOWN)
        assert conn.execute("""SELECT count(*) AS n FROM information_schema.columns WHERE table_schema='ka'
            AND table_name='agents' AND column_name='expert'""").fetchone()['n'] == 0
        assert conn.execute("SELECT count(*) AS n FROM ka.agent_tools WHERE tool LIKE 'expert:%'").fetchone()['n'] == 0
        run(conn, UP)
        assert conn.execute('SELECT expert FROM ka.agents WHERE id=%s', (expert,)).fetchone()['expert'] is False


def test_expert_calls_index_exists_after_up_and_is_gone_after_down(store):
    exists = "SELECT to_regclass('ka.agent_run_events_expert_calls') IS NOT NULL AS found"
    with store.connect() as conn:
        assert conn.execute(exists).fetchone()['found'] is True
        run(conn, DOWN)
        assert conn.execute(exists).fetchone()['found'] is False
        run(conn, UP)
        assert conn.execute(exists).fetchone()['found'] is True
