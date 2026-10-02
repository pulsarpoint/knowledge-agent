"""Migration 0060 adds grounding, reader sessions and the Reader assistant setting."""
from pathlib import Path

import psycopg
import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def test_reader_assistant_schema(store):
    with store.connect() as conn:
        grounded = conn.execute("SELECT id FROM ka.agents WHERE documents_grounded ORDER BY id").fetchall()
        assert [row['id'] for row in grounded] == ['search']
        assert conn.execute('SELECT knowledge_agent_id, revision FROM ka.reader_settings').fetchone() == {
            'knowledge_agent_id': 'search', 'revision': 1}
        column = conn.execute("""SELECT column_default FROM information_schema.columns
            WHERE table_schema='ka' AND table_name='agent_sessions' AND column_name='channel'""").fetchone()
        assert column['column_default'] == "'admin'::text"


def test_reader_settings_row_is_a_singleton_and_blocks_agent_delete(store):
    with store.connect() as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO ka.reader_settings(singleton, knowledge_agent_id) VALUES (false, 'search')")
    with store.connect() as conn:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute("UPDATE ka.reader_settings SET knowledge_agent_id='missing'")


def test_down_then_up_is_clean(store):
    down = (MIGRATIONS / '0060_reader_assistant.down.sql').read_text()
    up = (MIGRATIONS / '0060_reader_assistant.up.sql').read_text()
    with store.connect() as conn:
        conn.execute(down, prepare=False)
        assert conn.execute("SELECT to_regclass('ka.reader_settings') AS t").fetchone()['t'] is None
        conn.execute(up, prepare=False)
        assert conn.execute('SELECT count(*) AS n FROM ka.reader_settings').fetchone()['n'] == 1
