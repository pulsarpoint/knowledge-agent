"""0063 converts RAG folders into collections without reprocessing."""

from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def apply(conn, upto: int, start: int = 1):
    for path in sorted(MIGRATIONS.glob('*.up.sql')):
        number = int(path.name[:4])
        if start <= number <= upto:
            conn.execute(path.read_text(), prepare=False)


@pytest.fixture
def legacy(store):
    """A database at 0062 with folders, indexed files, vectors, grants and jobs."""
    with store.connect() as conn:
        yield from _seed(conn)


def _seed(conn):
    conn.execute((MIGRATIONS / '0066_rag_managed_collections.down.sql').read_text(), prepare=False)
    conn.execute((MIGRATIONS / '0065_rag_collection_paths.down.sql').read_text(), prepare=False)
    conn.execute((MIGRATIONS / '0063_rag_collections.down.sql').read_text(), prepare=False)
    project = conn.execute('SELECT id FROM ka.projects LIMIT 1').fetchone()
    if project is None:
        project = conn.execute("INSERT INTO ka.projects(slug,name) VALUES ('t','T') RETURNING id").fetchone()
    project = project['id']
    conn.execute(
        """INSERT INTO ka.rag_workspaces(project_id,settings) VALUES (%s,
        '{"chunk_size":1500,"chunk_overlap":100,"max_file_bytes":3000000,"extensions":[".md",".txt"],"embedding_enabled":true}')
        ON CONFLICT (project_id) DO UPDATE SET settings=EXCLUDED.settings""",
        (project,),
    )
    ids = {}
    for path in ('jira', 'demo', 'demo/payments', 'Old_Stuff'):
        ids[path] = conn.execute(
            'INSERT INTO ka.rag_folders(project_id,path,description) VALUES (%s,%s,%s) RETURNING id',
            (project, path, f'About {path}'),
        ).fetchone()['id']
    actor = conn.execute("INSERT INTO ka.principals(issuer,subject) VALUES ('t','t') RETURNING id").fetchone()['id']
    job = conn.execute(
        """INSERT INTO ka.jobs(project_id,kind,rag_folder_id,requested_by,input,status,started_at,finished_at)
        VALUES (%s,'rag_folder_index',%s,%s,'{}','succeeded',now(),now()) RETURNING id""",
        (project, ids['jira'], actor),
    ).fetchone()['id']
    queued = conn.execute(
        """INSERT INTO ka.jobs(project_id,kind,rag_folder_id,requested_by,input)
        VALUES (%s,'rag_folder_index',%s,%s,'{}') RETURNING id""",
        (project, ids['demo'], actor),
    ).fetchone()['id']
    for folder, path in (('jira', 'jira/DEMO-1.md'), ('demo/payments', 'demo/payments/operations.md')):
        file_id = conn.execute(
            """INSERT INTO ka.rag_files(project_id,folder_id,path,kind,content_hash,processing_hash,embedding_profile,job_id,indexed_at)
            VALUES (%s,%s,%s,'file',repeat('a',64),repeat('b',64),'',%s,'2026-10-01T10:00:00Z') RETURNING id""",
            (project, ids[folder], path, job),
        ).fetchone()['id']
        conn.execute(
            "INSERT INTO ka.rag_chunks(project_id,file_id,position,text,input_hash) VALUES (%s,%s,0,'Retry policy',repeat('c',64))",
            (project, file_id),
        )
    conn.execute(
        """INSERT INTO ka.rag_files(project_id,folder_id,path,kind,content_hash,processing_hash,embedding_profile,job_id)
        VALUES (%s,%s,'demo','folder',repeat('d',64),repeat('b',64),'',%s)""",
        (project, ids['demo'], job),
    )
    conn.execute(
        "INSERT INTO ka.agent_knowledge_folders(agent_id,folder_id) VALUES ('search',%s),('search',%s)",
        (ids['demo/payments'], ids['jira']),
    )
    yield conn, project, ids, queued


def test_folders_become_collections_and_index_survives(legacy):
    conn, project, _, queued = legacy
    apply(conn, 63, start=63)
    rows = {
        r['name']: r
        for r in conn.execute('SELECT * FROM ka.rag_collections WHERE project_id=%s', (project,)).fetchall()
    }
    assert set(rows) == {'jira', 'demo', 'old-stuff', 'documents'}
    assert rows['jira']['source_path'] == 'rag-data/jira' and rows['jira']['description'] == 'About jira'
    assert rows['old-stuff']['source_path'] == 'rag-data/Old_Stuff'
    assert rows['jira']['include'] == ['**/*.md', '**/*.txt'] and rows['jira']['embeddings_enabled'] is True
    assert (rows['jira']['chunk_size'], rows['jira']['chunk_overlap']) == (1500, 100)
    # Migrated collections keep their last sync time but need a refresh (old processing fingerprint).
    assert rows['jira']['last_synced_at'] is not None and rows['jira']['synced_revision'] is None
    assert rows['documents']['source_path'] == 'generated-documents'
    assert (
        rows['documents']['exclude'] == ['*/v[0-9]*/**', 'services/*/*/v[0-9]*/**']
        and rows['documents']['last_synced_at'] is None
    )
    files = {
        r['path']: r['collection_id'] for r in conn.execute('SELECT path,collection_id FROM ka.rag_files').fetchall()
    }
    assert files == {'DEMO-1.md': rows['jira']['id'], 'payments/operations.md': rows['demo']['id']}
    assert conn.execute('SELECT count(*) AS n FROM ka.rag_chunks').fetchone()['n'] == 2
    assert conn.execute('SELECT settings FROM ka.rag_workspaces WHERE project_id=%s', (project,)).fetchone()[
        'settings'
    ] == {'max_file_bytes': 3000000}
    assert conn.execute('SELECT status FROM ka.jobs WHERE id=%s', (queued,)).fetchone()['status'] == 'cancelled'


def test_grants_and_job_rules(legacy):
    conn, project, _, _ = legacy
    apply(conn, 63, start=63)
    granted = {
        r['name']
        for r in conn.execute("""SELECT c.name FROM ka.agent_collections a JOIN ka.rag_collections c ON c.id=a.collection_id
        WHERE a.agent_id='search'""").fetchall()
    }
    assert granted == {'demo', 'jira'}
    agent = conn.execute("SELECT * FROM ka.agents WHERE id='search'").fetchone()
    assert 'all_collections' in agent and 'all_folders' not in agent and 'include_subfolders' not in agent
    collection = conn.execute("SELECT id FROM ka.rag_collections WHERE name='jira'").fetchone()['id']
    conn.execute(
        "INSERT INTO ka.jobs(project_id,kind,rag_collection_id,input) VALUES (%s,'rag_collection_sync',%s,'{}')",
        (project, collection),
    )
    with pytest.raises(Exception, match='rag_collection_one_active_job'):
        conn.execute(
            "INSERT INTO ka.jobs(project_id,kind,rag_collection_id,input) VALUES (%s,'rag_collection_sync',%s,'{}')",
            (project, collection),
        )


def test_rollback_restores_folders(legacy):
    conn, project, ids, _ = legacy
    apply(conn, 63, start=63)
    conn.execute((MIGRATIONS / '0063_rag_collections.down.sql').read_text(), prepare=False)
    paths = {
        r['path']: r['folder_path']
        for r in conn.execute("""SELECT f.path, d.path AS folder_path FROM ka.rag_files f
        JOIN ka.rag_folders d ON d.id=f.folder_id""").fetchall()
    }
    assert paths == {'jira/DEMO-1.md': 'jira', 'demo/payments/operations.md': 'demo'}
    assert {
        r['folder_id']
        for r in conn.execute("SELECT folder_id FROM ka.agent_knowledge_folders WHERE agent_id='search'").fetchall()
    } == {ids['jira'], ids['demo']}
    settings = conn.execute('SELECT settings FROM ka.rag_workspaces WHERE project_id=%s', (project,)).fetchone()[
        'settings'
    ]
    assert (
        settings['max_file_bytes'] == 3000000
        and settings['chunk_size'] == 1500
        and settings['embedding_enabled'] is True
    )
