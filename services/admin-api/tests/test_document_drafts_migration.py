"""0098: document drafts, what drafts and versions were built from, and document links."""

from pathlib import Path

import psycopg
import pytest

from old_documents import DOCUMENT_MODEL, before_0104, old_document  # noqa: F401  (fixture)

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0098_document_drafts.up.sql', '0098_document_drafts.down.sql'


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


pytestmark = pytest.mark.usefixtures('before_0104')


def world(conn, store):
    project = store.project(conn, 'shop')
    runbook = old_document(conn, project, 'runbook', 'Runbook', '# Runbook')
    overview = old_document(conn, project, 'overview', 'Overview', '# Overview')
    return project, runbook, overview


def draft(conn, project, **columns):
    values = {'project_id': project, 'title': 'Draft', 'markdown': '# Draft', 'origin': 'manual', **columns}
    names, marks = ','.join(values), ','.join(['%s'] * len(values))
    return conn.execute(
        f'INSERT INTO ka.document_drafts({names}) VALUES ({marks}) RETURNING id', tuple(values.values())
    ).fetchone()['id']


def test_a_draft_revises_a_document_or_names_a_new_one(store):
    with store.connect() as conn:
        project, runbook, _ = world(conn, store)
        draft(conn, project, document_id=runbook['id'], base_version_id=runbook['version_id'])
        draft(conn, project, slug='disk-layout', folder='servers/web-1')
        for columns in (
            {},
            {'document_id': runbook['id'], 'slug': 'runbook'},
            {'document_id': runbook['id'], 'folder': 'docs'},
            {'folder': 'docs'},
            {'slug': 'x', 'origin': 'robot'},
            {'slug': 'x', 'folder': 'Servers/Web-1'},
        ):
            with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
                draft(conn, project, **columns)


def test_a_promoted_draft_has_its_version_and_a_decided_draft_is_frozen(store):
    with store.connect() as conn:
        project, runbook, _ = world(conn, store)
        sid = draft(conn, project, document_id=runbook['id'], base_version_id=runbook['version_id'])
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE ka.document_drafts SET status='promoted',decided_at=now() WHERE id=%s", (sid,))
        conn.execute("UPDATE ka.document_drafts SET status='discarded',decided_at=now() WHERE id=%s", (sid,))
        for change in ("title='Changed'", "status='open',decided_at=NULL", 'revision=revision+1'):
            with pytest.raises(psycopg.errors.CheckViolation, match='promoted or discarded'), conn.transaction():
                conn.execute(f'UPDATE ka.document_drafts SET {change} WHERE id=%s', (sid,))
        conn.execute('UPDATE ka.document_drafts SET base_version_id=NULL WHERE id=%s', (sid,))  # ON DELETE SET NULL
        other = draft(conn, project, document_id=runbook['id'], base_version_id=runbook['version_id'])
        conn.execute(
            """UPDATE ka.document_drafts SET status='promoted',decided_at=now(),promoted_version_id=%s
            WHERE id=%s""",
            (runbook['version_id'], other),
        )


def test_sources_belong_to_a_draft_or_a_version_and_keep_their_snapshot(store):
    with store.connect() as conn:
        project, runbook, overview = world(conn, store)
        sid = draft(conn, project, document_id=runbook['id'], base_version_id=runbook['version_id'])
        add = """INSERT INTO ka.document_sources(project_id,draft_id,version_id,kind,source_document_id,source_version_id,
            source_title,source_version) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"""
        conn.execute(add, (project, sid, None, 'read', overview['id'], overview['version_id'], 'Overview', 1))
        with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
            conn.execute(add, (project, sid, None, 'read', overview['id'], overview['version_id'], 'Overview', 1))
        for owner in ((None, None), (sid, runbook['version_id'])):
            with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
                conn.execute(add, (project, *owner, 'read', runbook['id'], None, 'Runbook', 1))
        conn.execute(
            add, (project, None, runbook['version_id'], 'read', overview['id'], overview['version_id'], 'Overview', 1)
        )
        conn.execute('DELETE FROM ka.documents WHERE id=%s', (overview['id'],))
        rows = conn.execute("""SELECT source_document_id,source_version_id,source_title,source_version
            FROM ka.document_sources""").fetchall()
        assert (
            rows
            == [
                {'source_document_id': None, 'source_version_id': None, 'source_title': 'Overview', 'source_version': 1}
            ]
            * 2
        )


def test_links_refuse_self_and_duplicates_and_go_with_their_documents(store):
    with store.connect() as conn:
        project, runbook, overview = world(conn, store)
        add = 'INSERT INTO ka.document_links(project_id,from_document_id,to_document_id) VALUES (%s,%s,%s)'
        conn.execute(add, (project, runbook['id'], overview['id']))
        conn.execute(add, (project, overview['id'], runbook['id']))
        with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
            conn.execute(add, (project, runbook['id'], overview['id']))
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(add, (project, runbook['id'], runbook['id']))
        draft(conn, project, document_id=overview['id'], base_version_id=overview['version_id'])
        promoted = draft(conn, project, document_id=overview['id'], base_version_id=overview['version_id'])
        conn.execute(
            """UPDATE ka.document_drafts SET status='promoted',decided_at=now(),promoted_version_id=%s
            WHERE id=%s""",
            (overview['version_id'], promoted),
        )
        conn.execute('DELETE FROM ka.documents WHERE id=%s', (overview['id'],))
        assert conn.execute('SELECT count(*) AS n FROM ka.document_links').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM ka.document_drafts').fetchone()['n'] == 0


def test_down_refuses_while_open_drafts_sources_or_links_exist_then_round_trips(store):
    with store.connect() as conn:
        project, runbook, overview = world(conn, store)
        sid = draft(conn, project, document_id=runbook['id'], base_version_id=runbook['version_id'])
        conn.commit()  # the migration files carry their own BEGIN/COMMIT
        with pytest.raises(psycopg.errors.RaiseException, match='would be lost'):
            run(conn, DOWN)
        conn.rollback()
        conn.execute("UPDATE ka.document_drafts SET status='discarded',decided_at=now() WHERE id=%s", (sid,))
        conn.execute(
            'INSERT INTO ka.document_links(project_id,from_document_id,to_document_id) VALUES (%s,%s,%s)',
            (project, runbook['id'], overview['id']),
        )
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match='would be lost'):
            run(conn, DOWN)
        conn.rollback()
        conn.execute('DELETE FROM ka.document_links')
        conn.commit()
        run(conn, DOWN)
        assert conn.execute("SELECT to_regclass('ka.document_drafts') IS NULL AS gone").fetchone()['gone']
        run(conn, UP)
        assert conn.execute("SELECT to_regclass('ka.document_links') IS NOT NULL AS back").fetchone()['back']
        run(conn, DOCUMENT_MODEL[1])
