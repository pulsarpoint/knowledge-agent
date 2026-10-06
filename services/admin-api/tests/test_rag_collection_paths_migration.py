"""0065 moves each collection's folder into rag_collection_paths and indexes files under full paths."""
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0065_rag_collection_paths.up.sql', '0065_rag_collection_paths.down.sql'


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


@pytest.fixture
def at_0064(store):
    """A database rolled back to 0064 with five collections, two indexed files and their chunks."""
    with store.connect() as conn:
        run(conn, '0066_rag_managed_collections.down.sql')
        run(conn, DOWN)
        project = conn.execute("SELECT id FROM ka.projects LIMIT 1").fetchone()
        if project is None:
            project = conn.execute("INSERT INTO ka.projects(slug,name) VALUES ('t','T') RETURNING id").fetchone()
        project = project['id']
        conn.execute("""INSERT INTO ka.rag_workspaces(project_id,settings) VALUES (%s,'{"max_file_bytes":2000000}')
            ON CONFLICT (project_id) DO NOTHING""", (project,))
        ids = {}
        for name, source in (('jira', 'rag-data/jira'), ('jira-copy', 'rag-data/jira'), ('documents', 'generated-documents'),
                             ('payments', 'code/payments'), ('gone', 'rag-data/gone')):
            ids[name] = conn.execute("""INSERT INTO ka.rag_collections(project_id,name,source_path,synced_revision,last_synced_at)
                VALUES (%s,%s,%s,1,now()) RETURNING id""", (project, name, source)).fetchone()['id']
        conn.execute("UPDATE ka.rag_collections SET deleted_at=now() WHERE id=%s", (ids['gone'],))
        conn.execute("UPDATE ka.rag_collections SET created_at=now() + interval '1 minute' WHERE id=%s", (ids['jira-copy'],))
        for name, path in (('jira', 'DEMO-1.md'), ('payments', 'src/retry.py')):
            job = conn.execute("""INSERT INTO ka.jobs(project_id,kind,rag_collection_id,input,status,started_at,finished_at)
                VALUES (%s,'rag_collection_sync',%s,'{}','succeeded',now(),now()) RETURNING id""", (project, ids[name])).fetchone()['id']
            file_id = conn.execute("""INSERT INTO ka.rag_files(project_id,collection_id,path,content_hash,processing_hash,embedding_profile,job_id)
                VALUES (%s,%s,%s,repeat('a',64),repeat('b',64),'',%s) RETURNING id""", (project, ids[name], path, job)).fetchone()['id']
            conn.execute("INSERT INTO ka.rag_chunks(project_id,file_id,position,text,input_hash) VALUES (%s,%s,0,'Retry policy',repeat('c',64))",
                         (project, file_id))
        yield conn, project, ids


def test_each_collection_gets_its_folder_as_a_path_with_owners(at_0064):
    conn, _, ids = at_0064
    run(conn, UP)
    paths = {r['name']: (r['source_path'], r['owner_kind'], r['owner_id'], r['owner_key']) for r in conn.execute(
        """SELECT c.name, p.source_path, p.owner_kind, p.owner_id, p.owner_key FROM ka.rag_collection_paths p
        JOIN ka.rag_collections c ON c.id=p.collection_id""").fetchall()}
    assert paths == {
        'jira': ('rag-data/jira', 'jira', None, None),
        'jira-copy': ('rag-data/jira', None, None, None),  # one owner per path: the older collection keeps it
        'documents': ('generated-documents', 'documents', None, None),
        'payments': ('code/payments', None, None, None),
        'gone': ('rag-data/gone', None, None, None)}
    assert 'source_path' not in conn.execute("SELECT * FROM ka.rag_collections LIMIT 1").fetchone()
    row = conn.execute("SELECT revision,synced_revision FROM ka.rag_collections WHERE id=%s", (ids['jira'],)).fetchone()
    assert row['synced_revision'] == row['revision']  # same folder, same files: no refresh needed
    columns = {r['column_name'] for r in conn.execute("""SELECT column_name FROM information_schema.columns
        WHERE table_schema='ka' AND table_name='repositories'""").fetchall()}
    assert {'search_collection', 'search_error'} <= columns


def test_indexed_files_move_to_full_paths_and_cascade_with_their_path(at_0064):
    conn, _, ids = at_0064
    run(conn, UP)
    files = {r['path']: (r['collection_id'], r['source_path']) for r in conn.execute("""SELECT f.path, f.collection_id, p.source_path
        FROM ka.rag_files f JOIN ka.rag_collection_paths p ON p.id=f.path_id""").fetchall()}
    assert files == {'rag-data/jira/DEMO-1.md': (ids['jira'], 'rag-data/jira'),
                     'code/payments/src/retry.py': (ids['payments'], 'code/payments')}
    conn.execute("DELETE FROM ka.rag_collection_paths WHERE collection_id=%s", (ids['payments'],))
    assert conn.execute("SELECT count(*) AS n FROM ka.rag_files").fetchone()['n'] == 1
    assert conn.execute("SELECT count(*) AS n FROM ka.rag_chunks").fetchone()['n'] == 1


def test_path_rules_are_enforced(at_0064):
    conn, _, ids = at_0064
    run(conn, UP)
    insert = "INSERT INTO ka.rag_collection_paths(collection_id,source_path,owner_kind,owner_id,owner_key) VALUES (%s,%s,%s,%s,%s)"
    for values, constraint in (
            ((ids['payments'], 'rag-data/other', 'jira', None, None), 'rag_collection_paths_owner'),
            ((ids['payments'], '../etc', None, None, None), 'rag_collection_paths_source_path_check'),
            ((ids['payments'], 'code/x', None, ids['jira'], None), 'rag_collection_paths_check'),
            ((ids['payments'], 'code/payments', None, None, None), 'rag_collection_paths_collection_id_source_path_key'),
            ((ids['payments'], 'code/y', 'wiki', None, None), 'rag_collection_paths_owner_kind_check')):
        with pytest.raises(Exception, match=constraint):
            with conn.transaction():
                conn.execute(insert, values)
    checks = {r['conname'] for r in conn.execute("""SELECT conname FROM pg_constraint
        WHERE conrelid='ka.repositories'::regclass AND contype='c'""").fetchall()}
    assert {'repositories_search_collection_check', 'repositories_search_error_check'} <= checks


def test_rollback_refuses_collections_with_several_paths(at_0064):
    conn, _, ids = at_0064
    run(conn, UP)
    conn.execute("INSERT INTO ka.rag_collection_paths(collection_id,source_path) VALUES (%s,'docs/runbooks')", (ids['payments'],))
    with pytest.raises(Exception, match='Collection payments has more than one path'):
        with conn.transaction():
            run(conn, DOWN)
    conn.execute("DELETE FROM ka.rag_collection_paths WHERE source_path='docs/runbooks'")
    run(conn, DOWN)
    rows = {r['name']: r['source_path'] for r in conn.execute("SELECT name, source_path FROM ka.rag_collections").fetchall()}
    assert rows == {'jira': 'rag-data/jira', 'jira-copy': 'rag-data/jira', 'documents': 'generated-documents',
                    'payments': 'code/payments', 'gone': 'rag-data/gone'}
    assert {r['path'] for r in conn.execute("SELECT path FROM ka.rag_files").fetchall()} == {'DEMO-1.md', 'src/retry.py'}
    assert conn.execute("SELECT to_regclass('ka.rag_collection_paths') AS t").fetchone()['t'] is None


def test_rollback_gives_pathless_collections_a_rag_data_folder(at_0064):
    conn, project, _ = at_0064
    run(conn, UP)
    conn.execute("INSERT INTO ka.rag_collections(project_id,name) VALUES (%s,'fresh')", (project,))
    run(conn, DOWN)
    assert conn.execute("SELECT source_path FROM ka.rag_collections WHERE name='fresh'").fetchone()['source_path'] == 'rag-data/fresh'
