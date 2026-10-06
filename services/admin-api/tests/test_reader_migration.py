"""Migration 0060 adds grounding and reader sessions; 0071 replaces the Reader assistant setting with published agents."""
from pathlib import Path

import psycopg
import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def test_reader_assistant_schema(store):
    with store.connect() as conn:
        grounded = conn.execute("SELECT id FROM ka.agents WHERE documents_grounded ORDER BY id").fetchall()
        assert [row['id'] for row in grounded] == ['search']
        assert [row['id'] for row in conn.execute('SELECT id FROM ka.agents WHERE reader_enabled')] == ['search']
        assert conn.execute("SELECT to_regclass('ka.reader_settings') AS t").fetchone()['t'] is None
        column = conn.execute("""SELECT column_default FROM information_schema.columns
            WHERE table_schema='ka' AND table_name='agent_sessions' AND column_name='channel'""").fetchone()
        assert column['column_default'] == "'admin'::text"
        assert conn.execute("SELECT repositories_enabled FROM ka.agents WHERE id='search'").fetchone()['repositories_enabled'] is False


def sql(name):
    return (MIGRATIONS / name).read_text()


def test_down_then_up_is_clean(store):
    with store.connect() as conn:
        conn.execute(sql('0071_reader_agents.down.sql'), prepare=False)
        conn.execute(sql('0060_reader_assistant.down.sql'), prepare=False)
        assert conn.execute("SELECT to_regclass('ka.reader_settings') AS t").fetchone()['t'] is None
        conn.execute(sql('0060_reader_assistant.up.sql'), prepare=False)
        assert conn.execute('SELECT count(*) AS n FROM ka.reader_settings').fetchone()['n'] == 1
        conn.execute(sql('0071_reader_agents.up.sql'), prepare=False)
        assert [row['id'] for row in conn.execute('SELECT id FROM ka.agents WHERE reader_enabled')] == ['search']


def test_reader_agents_down_keeps_the_first_published_agent(store):
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET reader_enabled=true WHERE id='code'")
        conn.execute(sql('0071_reader_agents.down.sql'), prepare=False)
        chosen = conn.execute('SELECT knowledge_agent_id FROM ka.reader_settings').fetchone()['knowledge_agent_id']
        expected = conn.execute("SELECT id FROM ka.agents WHERE id IN ('code','search') ORDER BY lower(name),id LIMIT 1").fetchone()['id']
        assert chosen == expected
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO ka.reader_settings(singleton, knowledge_agent_id) VALUES (false, 'search')")
