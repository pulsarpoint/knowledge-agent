"""0066 removes folder owners and code-project search settings, and adds the file log and auto-sync."""

from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0066_rag_managed_collections.up.sql', '0066_rag_managed_collections.down.sql'


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def columns(conn, table: str) -> set[str]:
    return {
        r['column_name']
        for r in conn.execute(
            """SELECT column_name FROM information_schema.columns
        WHERE table_schema='ka' AND table_name=%s""",
            (table,),
        ).fetchall()
    }


@pytest.fixture
def at_0065(store):
    """A database rolled back to 0065 with owned and unowned folders, an indexed file and a searchable code project."""
    with store.connect() as conn:
        run(conn, DOWN)
        project = conn.execute('SELECT id FROM ka.projects LIMIT 1').fetchone()
        if project is None:
            project = conn.execute("INSERT INTO ka.projects(slug,name) VALUES ('t','T') RETURNING id").fetchone()
        project = project['id']
        conn.execute(
            """INSERT INTO ka.rag_workspaces(project_id,settings) VALUES (%s,'{"max_file_bytes":2000000}')
            ON CONFLICT (project_id) DO NOTHING""",
            (project,),
        )
        ids = {
            name: conn.execute(
                """INSERT INTO ka.rag_collections(project_id,name,synced_revision,last_synced_at)
            VALUES (%s,%s,1,now()) RETURNING id""",
                (project, name),
            ).fetchone()['id']
            for name in ('documents', 'jira', 'payments')
        }
        paths = {}
        for name, source, owner in (
            ('documents', 'generated-documents', 'documents'),
            ('jira', 'rag-data/jira', 'jira'),
            ('payments', 'code/payments', None),
            ('payments', 'docs/runbooks', None),
        ):
            paths[source] = conn.execute(
                """INSERT INTO ka.rag_collection_paths(collection_id,source_path,owner_kind)
                VALUES (%s,%s,%s) RETURNING id""",
                (ids[name], source, owner),
            ).fetchone()['id']
        job = conn.execute(
            """INSERT INTO ka.jobs(project_id,kind,rag_collection_id,input,status,started_at,finished_at)
            VALUES (%s,'rag_collection_sync',%s,'{}','succeeded',now(),now()) RETURNING id""",
            (project, ids['jira']),
        ).fetchone()['id']
        conn.execute(
            """INSERT INTO ka.rag_files(project_id,collection_id,path_id,path,content_hash,processing_hash,
            embedding_profile,job_id,indexed_at) VALUES (%s,%s,%s,'rag-data/jira/DEMO-1.md',repeat('a',64),repeat('b',64),'',%s,
            '2026-10-01T00:00:00Z')""",
            (project, ids['jira'], paths['rag-data/jira'], job),
        )
        conn.execute(
            """INSERT INTO ka.repositories(project_id,slug,name,search_collection,search_error)
            VALUES (%s,'repository-1','Payments','payments','RAG service is unavailable. Retry later.')""",
            (project,),
        )
        yield conn, project, ids, paths


def test_owners_and_search_columns_go_and_every_connection_stays(at_0065):
    conn, _, ids, _ = at_0065
    run(conn, UP)
    assert not {'owner_kind', 'owner_id', 'owner_key'} & columns(conn, 'rag_collection_paths')
    assert not {'search_collection', 'search_error'} & columns(conn, 'repositories')
    assert conn.execute("SELECT to_regclass('ka.rag_collection_paths_owner') AS i").fetchone()['i'] is None
    kept = {
        (r['name'], r['source_path'])
        for r in conn.execute("""SELECT c.name, p.source_path FROM ka.rag_collection_paths p
        JOIN ka.rag_collections c ON c.id=p.collection_id""").fetchall()
    }
    assert kept == {
        ('documents', 'generated-documents'),
        ('jira', 'rag-data/jira'),
        ('payments', 'code/payments'),
        ('payments', 'docs/runbooks'),
    }
    row = conn.execute('SELECT * FROM ka.rag_collections WHERE id=%s', (ids['jira'],)).fetchone()
    assert (row['auto_sync'], row['poll_interval_minutes'], row['last_polled_at'], row['synced_revision']) == (
        False,
        5,
        None,
        1,
    )


def test_indexed_files_start_in_the_log_and_cascade_with_their_folder(at_0065):
    conn, _, _, paths = at_0065
    run(conn, UP)
    log = conn.execute('SELECT * FROM ka.rag_file_log').fetchall()
    assert [(r['path'], r['status'], r['size'], r['mtime'], r['fingerprint'], r['content_hash']) for r in log] == [
        ('rag-data/jira/DEMO-1.md', 'indexed', None, None, None, 'a' * 64)
    ]
    assert log[0]['path_id'] == paths['rag-data/jira'] and log[0]['indexed_at'] is not None
    conn.execute('DELETE FROM ka.rag_collection_paths WHERE id=%s', (paths['rag-data/jira'],))
    assert conn.execute('SELECT count(*) AS n FROM ka.rag_file_log').fetchone()['n'] == 0


def test_log_and_polling_rules_are_enforced(at_0065):
    conn, _, ids, paths = at_0065
    run(conn, UP)
    insert = 'INSERT INTO ka.rag_file_log(collection_id,path_id,path,status) VALUES (%s,%s,%s,%s)'
    for values, constraint in (
        (
            (ids['jira'], paths['rag-data/jira'], 'rag-data/jira/DEMO-1.md', 'indexed'),
            'rag_file_log_collection_id_path_key',
        ),
        ((ids['jira'], paths['rag-data/jira'], 'rag-data/jira/x.md', 'pending'), 'rag_file_log_status_check'),
        (
            (ids['payments'], paths['rag-data/jira'], 'rag-data/jira/y.md', 'failed'),
            'rag_file_log_path_id_collection_id_fkey',
        ),
    ):
        with pytest.raises(Exception, match=constraint):
            with conn.transaction():
                conn.execute(insert, values)
    for minutes in (0, 1441):
        with pytest.raises(Exception, match='rag_collections_poll_interval_minutes_check'):
            with conn.transaction():
                conn.execute(
                    'UPDATE ka.rag_collections SET poll_interval_minutes=%s WHERE id=%s', (minutes, ids['jira'])
                )


def test_rollback_restores_empty_owner_and_search_columns(at_0065):
    conn, _, ids, paths = at_0065
    run(conn, UP)
    run(conn, DOWN)
    owners = {
        r['source_path']: (r['owner_kind'], r['owner_id'], r['owner_key'])
        for r in conn.execute('SELECT * FROM ka.rag_collection_paths').fetchall()
    }
    assert owners == {source: (None, None, None) for source in paths}
    row = conn.execute('SELECT search_collection, search_error FROM ka.repositories').fetchone()
    assert (row['search_collection'], row['search_error']) == (None, None)
    assert conn.execute("SELECT to_regclass('ka.rag_file_log') AS t").fetchone()['t'] is None
    assert not {'auto_sync', 'poll_interval_minutes', 'last_polled_at'} & columns(conn, 'rag_collections')
    conn.execute(
        "INSERT INTO ka.rag_collection_paths(collection_id,source_path,owner_kind) VALUES (%s,'rag-data/x','jira')",
        (ids['jira'],),
    )
    with pytest.raises(Exception, match='rag_collection_paths_owner'):
        with conn.transaction():
            conn.execute(
                "INSERT INTO ka.rag_collection_paths(collection_id,source_path,owner_kind) VALUES (%s,'rag-data/y','jira')",
                (ids['payments'],),
            )
