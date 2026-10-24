"""0136: the output schema library, the handler columns that name a schema, and the run columns that keep its output."""

from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
SCHEMA = {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def a_schema(conn, project, name='Triage') -> dict:
    row = conn.execute(
        'INSERT INTO ka.output_schemas(project_id,name) VALUES (%s,%s) RETURNING *', (project, name)
    ).fetchone()
    conn.execute(
        'INSERT INTO ka.output_schema_versions(schema_id,version,schema) VALUES (%s,1,%s)', (row['id'], Jsonb(SCHEMA))
    )
    return row


def a_handler(conn, project, **values) -> dict:
    actor = conn.execute(
        "INSERT INTO ka.principals(issuer,subject) VALUES ('t',gen_random_uuid()::text) RETURNING id"
    ).fetchone()['id']
    row = {
        'project_id': project,
        'name': 'Triage',
        'type_pattern': '*',
        'action': 'emit',
        'emit': Jsonb({'type': 'a.b'}),
        'created_by': actor,
        **values,
    }
    return conn.execute(
        f'INSERT INTO ka.event_handlers({",".join(row)}) VALUES ({",".join(["%s"] * len(row))}) RETURNING *',
        tuple(row.values()),
    ).fetchone()


def test_defaults_and_unique_names(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        row = a_schema(conn, project)
        assert (row['current_version'], row['revision'], row['description']) == (1, 1, '')
    with pytest.raises(psycopg.errors.UniqueViolation), store.connect() as conn:
        a_schema(conn, project)


def test_a_version_is_a_json_object_and_unique(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        schema = a_schema(conn, project)
        conn.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                'INSERT INTO ka.output_schema_versions(schema_id,version,schema) VALUES (%s,2,%s)',
                (schema['id'], Jsonb([])),
            )
    with pytest.raises(psycopg.errors.UniqueViolation), store.connect() as conn:
        conn.execute(
            'INSERT INTO ka.output_schema_versions(schema_id,version,schema) VALUES (%s,1,%s)',
            (schema['id'], Jsonb(SCHEMA)),
        )


def test_a_handler_may_name_a_schema_and_blocks_its_deletion(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        schema = a_schema(conn, project)
        handler = a_handler(conn, project, output_schema_id=schema['id'], output_schema_version=1)
        assert (handler['output_schema_id'], handler['output_schema_version']) == (schema['id'], 1)
        assert a_handler(conn, project, name='Plain')['output_schema_id'] is None
    with pytest.raises(psycopg.errors.ForeignKeyViolation), store.connect() as conn:
        a_handler(conn, project, name='Missing version', output_schema_id=schema['id'], output_schema_version=9)
    with pytest.raises(psycopg.errors.ForeignKeyViolation), store.connect() as conn:
        conn.execute('DELETE FROM ka.output_schemas WHERE id=%s', (schema['id'],))


def test_a_version_needs_its_schema(store):
    with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
        a_handler(conn, store.project(conn, 'shop'), output_schema_version=1)


def test_a_deleted_schema_takes_its_versions(store):
    with store.connect() as conn:
        schema = a_schema(conn, store.project(conn, 'shop'))
        conn.execute('DELETE FROM ka.output_schemas WHERE id=%s', (schema['id'],))
        assert conn.execute('SELECT count(*) AS n FROM ka.output_schema_versions').fetchone()['n'] == 0


def test_sessions_have_the_output_columns(store):
    with store.connect() as conn:
        columns = {
            r['column_name']
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='ka' AND table_name='agent_sessions'"
            )
        }
    assert {'output', 'output_error', 'output_schema_id', 'output_schema_version'} <= columns


def test_down_removes_everything_and_up_restores_it(store):
    with store.connect() as conn:
        run(conn, '0136_output_schemas.down.sql')
        assert conn.execute("SELECT to_regclass('ka.output_schemas') AS t").fetchone()['t'] is None
        columns = conn.execute(
            """SELECT count(*) AS n FROM information_schema.columns WHERE table_schema='ka'
            AND column_name IN ('output_schema_id','output_schema_version','output','output_error')"""
        ).fetchone()['n']
        assert columns == 0
        run(conn, '0136_output_schemas.up.sql')
        assert conn.execute("SELECT to_regclass('ka.output_schemas') AS t").fetchone()['t'] is not None


def test_a_handler_run_may_be_skipped_and_down_removes_skipped_runs(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        handler = a_handler(conn, project)
        event = conn.execute(
            """INSERT INTO ka.events(project_id,source,type,subject,payload,dedupe_key,occurred_at)
            VALUES (%s,'run','run.output','A',%s,'k',now()) RETURNING id""",
            (project, Jsonb({'chain': 3})),
        ).fetchone()['id']
        conn.execute(
            """INSERT INTO ka.event_handler_runs(event_id,handler_id,handler_name,status,message)
            VALUES (%s,%s,'Triage','skipped','Stopped.')""",
            (event, handler['id']),
        )
        run(conn, '0136_output_schemas.down.sql')
        assert (
            conn.execute("SELECT count(*) AS n FROM ka.event_handler_runs WHERE status='skipped'").fetchone()['n'] == 0
        )
        run(conn, '0136_output_schemas.up.sql')
