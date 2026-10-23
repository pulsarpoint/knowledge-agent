"""0131: proposals are gone (documents and inventory are drafted). The table is dropped, down recreates it empty; the
seeded Server discovery, Write a document and Revise a document prompts get a new version only where nobody changed
their text, and down removes only those untouched new versions."""

from pathlib import Path
from uuid import uuid4

from discovery_world import world  # noqa: F401  (fixture)

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0131_proposals_gone.up.sql', '0131_proposals_gone.down.sql'
NAMES = ('Server discovery', 'Write a document', 'Revise a document')
PROMPT = """SELECT p.description,p.revision,p.next_version,v.version,v.content,
    (SELECT count(*) FROM ka.documentation_prompt_versions x WHERE x.prompt_id=p.id)::int AS versions
    FROM ka.documentation_prompts p JOIN ka.documentation_prompt_versions v ON v.id=p.active_version_id
    WHERE p.project_id=%s AND p.name=%s"""
SHAPE = ('description', 'version', 'versions', 'next_version', 'revision', 'content')


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def table_exists(conn) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema='ka' AND table_name='discovery_proposals'"
        ).fetchone()
        is not None
    )


def prompt(conn, project, name):
    return conn.execute(PROMPT, (project, name)).fetchone()


def shape(row) -> dict:
    return {key: row[key] for key in SHAPE}


def edit(conn, project, name) -> None:
    """A person saves a new version of a prompt with their own text and activates it."""
    row = conn.execute(
        """SELECT p.id,p.next_version,v.content FROM ka.documentation_prompts p
        JOIN ka.documentation_prompt_versions v ON v.id=p.active_version_id WHERE p.project_id=%s AND p.name=%s""",
        (project, name),
    ).fetchone()
    version = conn.execute(
        """INSERT INTO ka.documentation_prompt_versions(id,prompt_id,version,content,activated_at)
        VALUES (gen_random_uuid(),%s,%s,%s,now()) RETURNING id""",
        (row['id'], row['next_version'], row['content'] + ' Check the backups too.'),
    ).fetchone()['id']
    conn.execute(
        """UPDATE ka.documentation_prompts SET active_version_id=%s,next_version=next_version+1,revision=revision+1
        WHERE id=%s""",
        (version, row['id']),
    )


def test_the_table_is_gone_and_a_new_workspace_gets_the_draft_prompts(world):
    with world.store.connect() as conn:
        assert not table_exists(conn)
        project = world.store.project(conn, f'shop-{uuid4().hex[:6]}')
        discovery = prompt(conn, project, 'Server discovery')
        assert (discovery['version'], discovery['versions']) == (1, 1)
        for tool in ('save_inventory_draft', 'new_document', 'update_document', 'edit_document', 'list_documents'):
            assert tool in discovery['content']
        assert 'propose' not in discovery['content'] and 'proposal' not in discovery['description']
        assert 'update_document' in prompt(conn, project, 'Write a document')['content']
        assert 'save_document' not in prompt(conn, project, 'Write a document')['content']
        assert 'update_document' in prompt(conn, project, 'Revise a document')['content']
        assert 'save_document' not in prompt(conn, project, 'Revise a document')['content']


def test_up_rewrites_only_unchanged_prompts_and_down_restores_only_the_untouched_rewrites(world):
    with world.store.connect() as conn:
        conn.commit()
        run(conn, DOWN)
        assert table_exists(conn)
        assert conn.execute('SELECT count(*) AS n FROM ka.discovery_proposals').fetchone()['n'] == 0
        untouched, edited, later = (world.store.project(conn, f'shop-{uuid4().hex[:6]}') for _ in range(3))
        before = {name: prompt(conn, untouched, name) for name in NAMES}
        assert 'propose_document' in before['Server discovery']['content']
        assert 'save_document' in before['Write a document']['content']
        for name in NAMES:
            edit(conn, edited, name)
        edited_before = {name: shape(prompt(conn, edited, name)) for name in NAMES}
        conn.commit()
        run(conn, UP)
        assert not table_exists(conn)
        for name in NAMES:
            after = prompt(conn, untouched, name)
            assert (after['version'], after['versions'], after['next_version']) == (2, 2, 3)
            assert after['revision'] == before[name]['revision'] + 1
            assert 'propose_document' not in after['content'] and 'save_document' not in after['content']
            assert shape(prompt(conn, edited, name)) == edited_before[name]
        for name in NAMES:
            edit(conn, later, name)
        later_after = {name: shape(prompt(conn, later, name)) for name in NAMES}
        conn.commit()
        run(conn, DOWN)
        keys = ('description', 'version', 'versions', 'next_version', 'content')
        for name in NAMES:
            restored = prompt(conn, untouched, name)
            assert {k: restored[k] for k in keys} == {k: before[name][k] for k in keys}
            assert shape(prompt(conn, edited, name)) == edited_before[name]
            assert shape(prompt(conn, later, name)) == later_after[name]
        fresh = world.store.project(conn, f'shop-{uuid4().hex[:6]}')
        assert prompt(conn, fresh, 'Server discovery')['content'] == before['Server discovery']['content']
        conn.commit()
        run(conn, UP)
