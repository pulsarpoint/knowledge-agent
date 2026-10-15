"""0078: an agent's tools, backfilled from today's capability rule; the checks; up and down. Rollbacks run 0084 down
first, as golang-migrate does."""

import itertools
from pathlib import Path

import psycopg
import pytest
from implied_tools import implied_tools

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0078_agent_tools.up.sql', '0078_agent_tools.down.sql'
LATER = '0084_remove_claude_code.down.sql'
COMBINATIONS = list(itertools.product([False, True], repeat=4))  # code, documents, grounded, ssh


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def add_agent(conn, agent_id, *, code=False, documents=False, grounded=False, ssh=False):
    conn.execute(
        """INSERT INTO ka.agents(id,name,repositories_enabled,documents_enabled,documents_grounded,ssh_enabled,
        system_prompt) VALUES (%s,%s,%s,%s,%s,%s,'Be brief.')""",
        (agent_id, agent_id, code, documents, grounded, ssh),
    )


def flags(code, documents, grounded, ssh):
    return {
        'code': {'enabled': code},
        'documents': {'enabled': documents, 'grounded': grounded},
        'ssh': {'enabled': ssh},
    }


def rows(conn, agent_id):
    return {
        row['tool']: row
        for row in conn.execute(
            'SELECT tool,mode,usage,subagent_allowed FROM ka.agent_tools WHERE agent_id=%s', (agent_id,)
        )
    }


def test_backfill_reproduces_todays_tools_for_every_capability_combination(store):
    with store.connect() as conn:
        run(conn, LATER)
        run(conn, DOWN)
        conn.commit()
        for index, combination in enumerate(COMBINATIONS):
            add_agent(conn, f'agent-{index}', **dict(zip(('code', 'documents', 'grounded', 'ssh'), combination)))
        conn.commit()
        run(conn, UP)
        conn.commit()
        for index, combination in enumerate(COMBINATIONS):
            expected = {entry['tool'] for entry in implied_tools(flags(*combination))['items'] if entry['mode'] == 'on'}
            saved = rows(conn, f'agent-{index}')
            assert set(saved) == expected, combination
            assert all(
                row['mode'] == 'on' and row['usage'] is None and row['subagent_allowed'] for row in saved.values()
            )
        # the seeded Search agent answers only from documents
        assert set(rows(conn, 'search')) == {'search_documents', 'read_document', 'report_not_covered'}


def test_checks_and_cascade(store):
    with store.connect() as conn:
        for statement, error in (
            (
                "INSERT INTO ka.agent_tools(agent_id,tool,mode) VALUES ('code','read_file','sometimes')",
                psycopg.errors.CheckViolation,
            ),
            ("INSERT INTO ka.agent_tools(agent_id,tool) VALUES ('code','')", psycopg.errors.CheckViolation),
            (
                "INSERT INTO ka.agent_tools(agent_id,tool,usage) VALUES ('code','x', repeat('a', 8001))",
                psycopg.errors.CheckViolation,
            ),
            ("INSERT INTO ka.agent_tools(agent_id,tool) VALUES ('code','run_subagent')", psycopg.errors.CheckViolation),
            (
                "INSERT INTO ka.agent_tools(agent_id,tool) VALUES ('code','expert:search')",
                psycopg.errors.CheckViolation,
            ),
            (
                "INSERT INTO ka.agent_tools(agent_id,tool) VALUES ('missing','read_file')",
                psycopg.errors.ForeignKeyViolation,
            ),
            ("INSERT INTO ka.agent_tools(agent_id,tool) VALUES ('code','read_file')", psycopg.errors.UniqueViolation),
        ):
            with pytest.raises(error), conn.transaction():
                conn.execute(statement)
        conn.execute("INSERT INTO ka.agent_tools(agent_id,tool,subagent_allowed) VALUES ('code','run_subagent',false)")
        conn.execute("DELETE FROM ka.agent_tools WHERE agent_id='code' AND tool='run_subagent'")


def test_down_drops_the_table_and_up_backfills_again(store):
    with store.connect() as conn:
        run(conn, LATER)
        run(conn, DOWN)
        conn.commit()
        assert conn.execute("SELECT to_regclass('ka.agent_tools') AS t").fetchone()['t'] is None
        run(conn, UP)
        conn.commit()
        assert rows(conn, 'code')
