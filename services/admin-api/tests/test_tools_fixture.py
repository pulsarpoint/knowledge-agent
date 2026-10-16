"""ka.tools holds the tool attributes from tools/tools.json; the admin-api start replaces it with the fixture."""

import threading
from pathlib import Path

import psycopg
import pytest
from knowledge_admin.tools.fixture import FIXTURE, load_fixture, sync_tools

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
ROWS = 'SELECT name,position,tool_group,label,summary FROM ka.tools ORDER BY position'


def rows(store):
    with store.connect() as conn:
        return conn.execute(ROWS).fetchall()


def test_sync_inserts_every_entry_in_order(store):
    sync_tools(store)
    found = rows(store)
    entries = load_fixture()
    assert [row['name'] for row in found] == [entry['name'] for entry in entries]
    assert [row['position'] for row in found] == list(range(len(entries)))
    assert found[0] == {
        'name': entries[0]['name'],
        'position': 0,
        'tool_group': entries[0]['group'],
        'label': entries[0]['label'],
        'summary': entries[0]['summary'],
    }


def test_a_second_sync_updates_changed_and_removes_missing_entries(store):
    sync_tools(store)
    entries = load_fixture()
    changed = [dict(entries[0], label='Renamed'), *entries[2:]]
    sync_tools(store, changed)
    found = rows(store)
    assert [row['name'] for row in found] == [entry['name'] for entry in changed]
    assert found[0]['label'] == 'Renamed'
    sync_tools(store, changed)
    assert rows(store) == found


def test_an_unknown_group_fails_and_keeps_the_table(store):
    sync_tools(store)
    before = rows(store)
    with pytest.raises(psycopg.errors.CheckViolation):
        sync_tools(store, [dict(load_fixture()[0], group='toys')])
    assert rows(store) == before


def test_the_fixture_has_no_needs():
    assert all('needs' not in entry for entry in load_fixture())


def test_sync_twice_concurrently(store):
    errors = []

    def run():
        try:
            sync_tools(store)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(rows(store)) == len(load_fixture())


def test_the_fixture_is_packaged_next_to_the_code():
    assert FIXTURE.name == 'tools.json' and FIXTURE.parent.name == 'tools' and FIXTURE.is_file()


def test_migration_round_trip(store):
    with store.connect() as conn:
        conn.execute((MIGRATIONS / '0108_tools.down.sql').read_text(), prepare=False)
        assert conn.execute("SELECT to_regclass('ka.tools') AS t").fetchone()['t'] is None
        conn.execute((MIGRATIONS / '0108_tools.up.sql').read_text(), prepare=False)
        assert conn.execute('SELECT count(*) AS n FROM ka.tools').fetchone()['n'] == 0
