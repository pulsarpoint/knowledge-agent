"""0096: prompts carry tags; input_kind is retired but kept for the seed functions."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0096_prompt_tags.up.sql', '0096_prompt_tags.down.sql'


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def add(conn, project, name, **columns):
    columns = {'project_id': project, 'name': name, **columns}
    return conn.execute(
        f'INSERT INTO ka.documentation_prompts({",".join(columns)}) VALUES ({",".join(["%s"] * len(columns))}) RETURNING *',
        list(columns.values()),
    ).fetchone()


def test_seeded_prompts_of_a_new_workspace_are_tagged(store):
    with store.connect() as conn:
        project = store.project(conn, f'shop-{uuid4().hex[:6]}')  # a new project runs the seed triggers
        rows = conn.execute(
            "SELECT input_kind,tags FROM ka.documentation_prompts WHERE project_id=%s AND NOT (tags && ARRAY['server-discovery','document-revision','new-document'])",
            (project,),
        ).fetchall()
    assert rows, 'the seed functions create prompts for a new workspace'
    assert all(row['tags'] == ['documentation', row['input_kind']] for row in rows)


def test_untagged_inserts_get_tags_and_tagged_ones_keep_theirs(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        assert add(conn, project, 'Old style', input_kind='server')['tags'] == ['documentation', 'server']
        assert add(conn, project, 'No kind')['tags'] == ['documentation', 'mixed']
        assert add(conn, project, 'Tagged', tags=['server-discovery'])['tags'] == ['server-discovery']


@pytest.mark.parametrize(
    'tags',
    [
        ['Server'],
        ['-x'],
        ['a' * 41],
        ['has space'],
        ['a', 'a'],
        [f't{i}' for i in range(11)],
        ['a,b'],
        [''],
        ['a', ''],
        ['a', None],
        ['a' * 40 + ',' + 'b' * 40],
    ],
)
def test_bad_tags_are_refused(store, tags):
    with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
        add(conn, store.project(conn, 'shop'), f'Bad {uuid4().hex[:6]}', tags=tags)


def test_down_and_up_backfill_from_input_kind(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        run(conn, DOWN)
        assert (
            conn.execute("""SELECT count(*) AS n FROM information_schema.columns WHERE table_schema='ka'
            AND table_name='documentation_prompts' AND column_name='tags'""").fetchone()['n']
            == 0
        )
        prompt = conn.execute(
            """INSERT INTO ka.documentation_prompts(project_id,name,input_kind)
            VALUES (%s,'Runbook','service') RETURNING id""",
            (project,),
        ).fetchone()['id']
        run(conn, UP)
        assert conn.execute('SELECT tags FROM ka.documentation_prompts WHERE id=%s', (prompt,)).fetchone()['tags'] == [
            'documentation',
            'service',
        ]
        added = add(conn, project, 'After the round trip', input_kind='server')
        assert added['tags'] == ['documentation', 'server']
