"""0129: service proposals are gone (inventory findings are drafts); down restores the shape, not the data. The seeded
Server discovery prompt saves inventory findings with save_inventory_draft where nobody changed its text."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from discovery_world import world  # noqa: F401  (fixture)
from psycopg.types.json import Jsonb
from test_discovery_runs import ssh_ready, start

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0129_service_proposals_gone.up.sql', '0129_service_proposals_gone.down.sql'


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def columns(conn) -> set[str]:
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='ka' AND table_name='discovery_proposals'"
    )
    return {row['column_name'] for row in rows}


def test_only_document_proposals_remain_and_the_link_column_is_gone(world):
    ssh_ready(world)
    sid = start(world).json()['id']
    with world.store.connect() as conn:
        assert 'service_proposal_id' not in columns(conn)
        add = """INSERT INTO ka.discovery_proposals(project_id,session_id,server_id,number,kind,payload)
            VALUES (%s,%s,%s,1,%s,%s)"""
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(add, (world.project, sid, world.server['id'], 'service', Jsonb({'name': 'redis'})))
        conn.execute(add, (world.project, sid, world.server['id'], 'document', Jsonb({'title': 'x'})))


def test_down_restores_service_proposals_and_up_deletes_them_with_the_documents_about_them(world):
    ssh_ready(world)
    sid = start(world).json()['id']
    with world.store.connect() as conn:
        run(conn, DOWN)
        assert 'service_proposal_id' in columns(conn)
        add = """INSERT INTO ka.discovery_proposals(project_id,session_id,server_id,number,kind,payload,service_proposal_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id"""
        args = (world.project, sid, world.server['id'])
        service = conn.execute(add, (*args, 1, 'service', Jsonb({'name': 'redis'}), None)).fetchone()['id']
        conn.execute(add, (*args, 2, 'document', Jsonb({'title': 'Redis'}), service))
        conn.execute(add, (*args, 3, 'document', Jsonb({'title': 'Disks'}), None))
        run(conn, UP)
        rows = conn.execute('SELECT number,kind FROM ka.discovery_proposals ORDER BY number').fetchall()
        assert [(row['number'], row['kind']) for row in rows] == [(3, 'document')]
        assert 'service_proposal_id' not in columns(conn)


DISCOVERY = """SELECT p.description,p.revision,p.next_version,v.version,v.content,
    (SELECT count(*) FROM ka.documentation_prompt_versions x WHERE x.prompt_id=p.id)::int AS versions
    FROM ka.documentation_prompts p JOIN ka.documentation_prompt_versions v ON v.id=p.active_version_id
    WHERE p.project_id=%s AND p.name='Server discovery'"""
SHAPE = ('description', 'version', 'versions', 'next_version', 'revision', 'content')


def edit(conn, project) -> None:
    """A person saves a new version of the Server discovery prompt with their own text and activates it."""
    prompt = conn.execute(
        """SELECT p.id,p.next_version,v.content FROM ka.documentation_prompts p
        JOIN ka.documentation_prompt_versions v ON v.id=p.active_version_id
        WHERE p.project_id=%s AND p.name='Server discovery'""",
        (project,),
    ).fetchone()
    version = conn.execute(
        """INSERT INTO ka.documentation_prompt_versions(id,prompt_id,version,content,activated_at)
        VALUES (gen_random_uuid(),%s,%s,%s,now()) RETURNING id""",
        (prompt['id'], prompt['next_version'], prompt['content'] + ' Check the backups too.'),
    ).fetchone()['id']
    conn.execute(
        """UPDATE ka.documentation_prompts SET active_version_id=%s,next_version=next_version+1,revision=revision+1
        WHERE id=%s""",
        (version, prompt['id']),
    )


def shape(row) -> dict:
    return {key: row[key] for key in SHAPE}


def test_a_new_workspace_gets_the_server_discovery_prompt_that_saves_drafts(world):
    with world.store.connect() as conn:
        project = world.store.project(conn, f'shop-{uuid4().hex[:6]}')
        prompt = conn.execute(DISCOVERY, (project,)).fetchone()
    assert (prompt['version'], prompt['versions']) == (1, 1)
    assert 'propose_service' not in prompt['content']
    assert 'save_inventory_draft' in prompt['content'] and 'propose_document' in prompt['content']
    assert 'get_resource' in prompt['content'] and 'environment workspace' in prompt['content']
    assert 'proposals' not in prompt['description']


def test_up_rewrites_the_unchanged_seeded_prompt_only_and_down_restores_only_the_untouched_rewrite(world):
    with world.store.connect() as conn:
        conn.commit()
        run(conn, DOWN)
        # Created with 0106's seed: one untouched, one with an edited text, one whose rewrite a person edits later.
        untouched, edited, later = (world.store.project(conn, f'shop-{uuid4().hex[:6]}') for _ in range(3))
        before = conn.execute(DISCOVERY, (untouched,)).fetchone()
        assert 'propose_service' in before['content']
        edit(conn, edited)
        edited_before = conn.execute(DISCOVERY, (edited,)).fetchone()
        conn.commit()
        run(conn, UP)
        after = conn.execute(DISCOVERY, (untouched,)).fetchone()
        assert (after['version'], after['versions'], after['next_version']) == (2, 2, 3)
        assert after['revision'] == before['revision'] + 1
        assert 'propose_service' not in after['content'] and 'save_inventory_draft' in after['content']
        assert 'propose_document' in after['content']
        # The rest of the prompt is kept.
        for line in before['content'].splitlines():
            if line.startswith(('1.', '2.', '5.', '6.')):
                assert line in after['content'].splitlines()
        assert shape(conn.execute(DISCOVERY, (edited,)).fetchone()) == shape(edited_before)
        edit(conn, later)
        later_after = conn.execute(DISCOVERY, (later,)).fetchone()
        conn.commit()
        run(conn, DOWN)
        restored = conn.execute(DISCOVERY, (untouched,)).fetchone()
        assert {k: restored[k] for k in ('description', 'version', 'versions', 'next_version', 'content')} == {
            k: before[k] for k in ('description', 'version', 'versions', 'next_version', 'content')
        }
        assert shape(conn.execute(DISCOVERY, (edited,)).fetchone()) == shape(edited_before)
        assert shape(conn.execute(DISCOVERY, (later,)).fetchone()) == shape(later_after)
        # A workspace created after the down gets 0106's seed again.
        fresh = world.store.project(conn, f'shop-{uuid4().hex[:6]}')
        assert conn.execute(DISCOVERY, (fresh,)).fetchone()['content'] == before['content']
        conn.commit()
        run(conn, UP)
