"""0067 opens collection folders to any visible path under DATA_ROOT, moves Jira paths from rag-data/jira to
jira, and follows saved agent versions to the single /work/data mount."""
from pathlib import Path

import pytest
from psycopg.types.json import Jsonb

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0067_data_root_folders.up.sql', '0067_data_root_folders.down.sql'
SAVED = {'code': {'projects': [{'folder_paths': ['/work/code/payments']}]},
         'documents': {'collections': [{'name': 'jira', 'paths': [
             '/work/rag-data/jira', '/work/rag-data/jira2', '/work/rag-data/demo', '/work/code-index/x',
             '/work/generated-documents', '/work/docs/runbooks']}]}}
MOVED = {'code': {'projects': [{'folder_paths': ['/work/data/code/payments']}]},
         'documents': {'collections': [{'name': 'jira', 'paths': [
             '/work/data/jira', '/work/rag-data/jira2', '/work/rag-data/demo', '/work/data/code-index/x',
             '/work/data/generated-documents', '/work/data/docs/runbooks']}]}}
FILES_BEFORE = {'jira': 'rag-data/jira/DEMO-1.md', 'lookalike': 'rag-data/jira2/x.md', 'whole': 'rag-data/jira/DEMO-1.md'}
BEFORE = {'folders': {'jira': 'rag-data/jira', 'lookalike': 'rag-data/jira2', 'whole': 'rag-data'},
          'files': FILES_BEFORE, 'log': FILES_BEFORE}
FILES_AFTER = {**FILES_BEFORE, 'jira': 'jira/DEMO-1.md'}
AFTER = {'folders': {**BEFORE['folders'], 'jira': 'jira'}, 'files': FILES_AFTER, 'log': FILES_AFTER}


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def state(conn) -> dict:
    def by_name(sql: str) -> dict:
        return {r['name']: r['value'] for r in conn.execute(sql).fetchall()}
    return {
        'folders': by_name("""SELECT c.name, p.source_path AS value FROM ka.rag_collection_paths p
            JOIN ka.rag_collections c ON c.id=p.collection_id WHERE c.name IN ('jira','lookalike','whole')"""),
        'files': by_name("""SELECT c.name, f.path AS value FROM ka.rag_files f
            JOIN ka.rag_collections c ON c.id=f.collection_id"""),
        'log': by_name("""SELECT c.name, l.path AS value FROM ka.rag_file_log l
            JOIN ka.rag_collections c ON c.id=l.collection_id"""),
    }


@pytest.fixture
def at_0066(store):
    """Rolled back to 0066 with the jira folder, a look-alike, a whole rag-data folder that shares the Jira file,
    their file log rows and a saved agent version."""
    with store.connect() as conn:
        run(conn, '0083_remove_claude_code.down.sql')
        run(conn, '0081_test_sessions.down.sql')
        run(conn, '0080_clone_agents.down.sql')  # restores ka.agent_versions
        run(conn, DOWN)
        project = store.project(conn, 'shop')
        conn.execute("""INSERT INTO ka.rag_workspaces(project_id,settings) VALUES (%s,'{"max_file_bytes":2000000}')
            ON CONFLICT (project_id) DO NOTHING""", (project,))
        ids = {name: conn.execute("INSERT INTO ka.rag_collections(project_id,name) VALUES (%s,%s) RETURNING id",
                                  (project, name)).fetchone()['id'] for name in ('jira', 'lookalike', 'whole')}
        job = conn.execute("""INSERT INTO ka.jobs(project_id,kind,rag_collection_id,input,status,started_at,finished_at)
            VALUES (%s,'rag_collection_sync',%s,'{}','succeeded',now(),now()) RETURNING id""",
            (project, ids['jira'])).fetchone()['id']
        for name, source in BEFORE['folders'].items():
            path_id = conn.execute("""INSERT INTO ka.rag_collection_paths(collection_id,source_path)
                VALUES (%s,%s) RETURNING id""", (ids[name], source)).fetchone()['id']
            conn.execute("""INSERT INTO ka.rag_files(project_id,collection_id,path_id,path,content_hash,processing_hash,
                embedding_profile,job_id,indexed_at) VALUES (%s,%s,%s,%s,repeat('a',64),repeat('b',64),'',%s,now())""",
                (project, ids[name], path_id, FILES_BEFORE[name], job))
            conn.execute("INSERT INTO ka.rag_file_log(collection_id,path_id,path,status) VALUES (%s,%s,%s,'indexed')",
                         (ids[name], path_id, FILES_BEFORE[name]))
        actor = store.actor(conn, 'migration-admin', 'migration@test')
        version = conn.execute("""INSERT INTO ka.agent_versions(agent,version,label,config,created_by)
            VALUES ('search',1000,'Saved before 0067',%s,%s) RETURNING id""", (Jsonb(SAVED), actor)).fetchone()['id']
        yield conn, ids, version


def test_up_rewrites_only_rag_data_jira_paths_and_down_restores_them(at_0066):
    conn, _, _ = at_0066
    assert state(conn) == BEFORE
    run(conn, UP)
    assert state(conn) == AFTER
    run(conn, DOWN)
    assert state(conn) == BEFORE


def test_collection_folders_may_be_any_visible_path(at_0066):
    conn, ids, _ = at_0066
    insert = "INSERT INTO ka.rag_collection_paths(collection_id,source_path) VALUES (%s,%s)"
    with pytest.raises(Exception, match='rag_collection_paths_source_path_check'):
        with conn.transaction():
            conn.execute(insert, (ids['lookalike'], 'exports/tools'))
    run(conn, UP)
    conn.execute(insert, (ids['lookalike'], 'exports/tools'))
    for bad in ('.hidden', 'a/../b', '/abs', 'a//b', 'a/', 'a b', 'x' * 501):
        with pytest.raises(Exception, match='rag_collection_paths_source_path_check'):
            with conn.transaction():
                conn.execute(insert, (ids['lookalike'], bad))
    run(conn, DOWN)  # a folder added after the upgrade is kept; new ones outside the five areas are refused again
    assert conn.execute("SELECT count(*) AS n FROM ka.rag_collection_paths WHERE source_path='exports/tools'").fetchone()['n'] == 1
    with pytest.raises(Exception, match='rag_collection_paths_source_path_check'):
        with conn.transaction():
            conn.execute(insert, (ids['whole'], 'other/x'))


def test_saved_agent_versions_follow_the_new_mount(at_0066):
    conn, _, version = at_0066
    def config():
        return conn.execute("SELECT config FROM ka.agent_versions WHERE id=%s", (version,)).fetchone()['config']
    run(conn, UP)
    assert config() == MOVED
    run(conn, DOWN)
    assert config() == SAVED
