"""0143: ka.code_project_dependencies (a code project depends on another code project; drafts like the other inventory
tables); down drops the table and gives back the 0142 schema."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row
from test_rag_gone_migration import ignoring_folder_order, schema

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0143_code_project_dependencies.up.sql', '0143_code_project_dependencies.down.sql'


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


@pytest.fixture
def at_0142(postgres):
    database = f'test_{uuid4().hex}'
    with psycopg.connect(postgres, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    try:
        with psycopg.connect(f'{postgres} dbname={database}', row_factory=dict_row) as conn:
            for migration in sorted(MIGRATIONS.glob('*.up.sql')):
                if migration.name[:4] <= '0142':
                    conn.execute(migration.read_text(), prepare=False)
            yield schema(conn)
    finally:
        with psycopg.connect(postgres, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))


def code_project(conn, project, name: str):
    return conn.execute(
        'INSERT INTO ka.repositories(project_id,slug,name) VALUES (%s,%s,%s) RETURNING id', (project, name, name)
    ).fetchone()['id']


def depend(conn, project, start, end, **columns):
    names = ['project_id', 'from_code_project_id', 'to_code_project_id', *columns]
    conn.execute(
        f'INSERT INTO ka.code_project_dependencies({",".join(names)}) VALUES ({",".join(["%s"] * len(names))})',
        (project, start, end, *columns.values()),
    )


def test_down_gives_back_the_0142_schema_and_up_again(store, at_0142):
    with store.connect() as conn:
        run(conn, '0144_no_document_folders.down.sql')
        run(conn, DOWN)
        assert ignoring_folder_order(schema(conn)) == ignoring_folder_order(at_0142)
        run(conn, UP)
        assert conn.execute("SELECT to_regclass('ka.code_project_dependencies') AS t").fetchone()['t'] is not None


def test_the_table_refuses_self_duplicates_and_bad_values_and_cascades(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        app, lib, other = (code_project(conn, project, name) for name in ('app', 'lib', 'other'))
        depend(conn, project, app, lib)
        depend(conn, project, lib, app)  # a cycle is allowed
        row = conn.execute('SELECT status,revision,description FROM ka.code_project_dependencies LIMIT 1').fetchone()
        assert row == {'status': 'confirmed', 'revision': 1, 'description': ''}
        for start, end, columns, error in (
            (app, app, {}, psycopg.errors.CheckViolation),
            (app, lib, {}, psycopg.errors.UniqueViolation),
            (app, other, {'status': 'proposed'}, psycopg.errors.CheckViolation),
            (app, other, {'description': 'x' * 4001}, psycopg.errors.CheckViolation),
        ):
            with pytest.raises(error), conn.transaction():
                depend(conn, project, start, end, **columns)
        conn.execute('DELETE FROM ka.repositories WHERE id=%s', (lib,))
        assert conn.execute('SELECT count(*) AS n FROM ka.code_project_dependencies').fetchone()['n'] == 0
