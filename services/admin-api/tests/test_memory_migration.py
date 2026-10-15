"""0111: memory entries, their earlier versions, the memory tool group and event source."""

from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def a_memory(conn, project, **values) -> dict:
    row = {'project_id': project, 'name': 'payments/deploy', 'content': 'Deploy with make release.', **values}
    return conn.execute(
        f'INSERT INTO ka.memories({",".join(row)}) VALUES ({",".join(["%s"] * len(row))}) RETURNING *',
        tuple(row.values()),
    ).fetchone()


def test_defaults_and_full_text(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        row = a_memory(conn, project, metadata=Jsonb({'service': 'payments-api'}))
        assert (row['revision'], row['embedding'], row['embedding_profile']) == (1, None, None)
        found = conn.execute(
            """SELECT count(*) AS n FROM ka.memories WHERE search_vector @@ websearch_to_tsquery('simple', %s)""",
            ('payments-api deploy release',),
        ).fetchone()
        assert found['n'] == 1  # name (split at /), metadata values and content are all searchable
        assert conn.execute('SELECT ka.jsonb_values_text(\'{"a":"x","b":"y"}\') AS t').fetchone()['t'] == 'x y'


@pytest.mark.parametrize(
    'values',
    [
        {'name': 'Payments'},  # uppercase
        {'name': '/payments'},  # must start with a letter or digit
        {'name': 'a' * 201},
        {'content': ''},
        {'content': 'x' * 20001},
        {'metadata': Jsonb(['a'])},
        {'embedding_profile': 'p'},  # a profile without a vector
    ],
)
def test_checks(store, values):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            a_memory(conn, project, **values)


def test_a_name_is_unique_in_a_project(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        a_memory(conn, project)
        conn.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            a_memory(conn, project)


def test_memory_tool_group_and_event_source(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute(
            """INSERT INTO ka.tools(name,position,tool_group,label,summary,needs)
            VALUES ('probe_memory',999,'memory','Probe','Probe.',NULL)"""
        )
        conn.execute(
            """INSERT INTO ka.events(project_id,source,type,subject,payload,dedupe_key,occurred_at)
            VALUES (%s,'memory','memory.saved','payments/deploy','{}','memory:probe',now())""",
            (project,),
        )


def test_down_and_up_again(store):
    with store.connect() as conn:
        a_memory(conn, store.project(conn, 'shop'))
        run(conn, '0111_memory.down.sql')
        assert conn.execute("SELECT to_regclass('ka.memories') IS NULL AS gone").fetchone()['gone']
        run(conn, '0111_memory.up.sql')
        assert conn.execute("SELECT to_regclass('ka.memory_versions') IS NOT NULL AS ok").fetchone()['ok']
