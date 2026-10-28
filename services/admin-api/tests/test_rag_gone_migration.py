"""0134: the RAG tables, their jobs and the collection access entries go; down gives back the 0133 schema (no data)."""

from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from session_rows import a_model
from test_api import _client  # noqa: F401
from test_llm import HEADERS

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0134_rag_gone.up.sql', '0134_rag_gone.down.sql'
RAG_TABLES = (
    'rag_vectors',
    'rag_chunks',
    'rag_file_log',
    'rag_files',
    'rag_collection_paths',
    'rag_collections',
    'rag_folders',
    'rag_workspaces',
)


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def schema(conn) -> dict:
    """The ka schema as the catalog describes it: columns, constraints, indexes, triggers, functions and comments."""

    def rows(query):
        return sorted(tuple(row.values()) for row in conn.execute(query))

    return {
        'columns': rows(
            """SELECT c.relname, row_number() OVER (PARTITION BY c.oid ORDER BY a.attnum), a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
            pg_get_expr(d.adbin, d.adrelid), a.attgenerated, col_description(a.attrelid, a.attnum)
            FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
            WHERE n.nspname = 'ka' AND c.relkind IN ('r', 'v') AND a.attnum > 0 AND NOT a.attisdropped"""
        ),
        'constraints': rows(
            """SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) FROM pg_constraint
            WHERE connamespace = 'ka'::regnamespace AND conrelid <> 0"""
        ),
        'indexes': rows("SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'ka'"),
        'triggers': rows(
            """SELECT tgrelid::regclass::text, tgname, pg_get_triggerdef(t.oid) FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid WHERE c.relnamespace = 'ka'::regnamespace AND NOT tgisinternal"""
        ),
        'functions': rows(
            """SELECT proname, pg_get_functiondef(p.oid) FROM pg_proc p
            WHERE pronamespace = 'ka'::regnamespace AND prokind = 'f'"""
        ),
        'comments': rows(
            """SELECT relname, obj_description(c.oid, 'pg_class') FROM pg_class c
            WHERE relnamespace = 'ka'::regnamespace AND relkind IN ('r', 'v')"""
        ),
    }


FOLDER_TABLES = ('documents', 'live_documents', 'agent_sessions')


def ignoring_folder_order(described: dict) -> dict:
    """The schema without the column positions of FOLDER_TABLES: 0144 down adds their folder column back at the end
    (PostgreSQL cannot put a column back in its place)."""
    columns = sorted((row[0], None, *row[2:]) if row[0] in FOLDER_TABLES else row for row in described['columns'])
    return {**described, 'columns': columns}


@pytest.fixture
def at_0133(postgres):
    """A fresh database migrated up to 0133 only."""
    database = f'test_{uuid4().hex}'
    with psycopg.connect(postgres, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    try:
        with psycopg.connect(f'{postgres} dbname={database}', row_factory=dict_row) as conn:
            for migration in sorted(MIGRATIONS.glob('*.up.sql')):
                if migration.name[:4] <= '0133':
                    conn.execute(migration.read_text(), prepare=False)
            yield schema(conn)
    finally:
        with psycopg.connect(postgres, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))


def test_down_gives_back_the_0133_schema(store, at_0133):
    with store.connect() as conn:
        run(conn, '0146_run_retries.down.sql')  # newer migrations first
        run(
            conn, '0145_write_with_an_agent.down.sql'
        )  # 0145 drops agent_sessions.document_name, which 0144 down checks
        run(conn, '0144_no_document_folders.down.sql')
        run(conn, '0143_code_project_dependencies.down.sql')
        run(conn, '0142_test_is_chat.down.sql')
        run(conn, '0140_code_project_prompt.down.sql')
        run(conn, '0139_skill_folders.down.sql')
        run(conn, '0138_previous_output.down.sql')
        run(conn, '0136_output_schemas.down.sql')
        run(conn, '0135_agent_disabled_tools.down.sql')  # (after 0135 down)
        run(conn, DOWN)
        assert ignoring_folder_order(schema(conn)) == ignoring_folder_order(at_0133)


def tables(conn) -> set[str]:
    return {
        row['relname']
        for row in conn.execute(
            "SELECT relname FROM pg_class WHERE relnamespace='ka'::regnamespace AND relkind='r' AND relname LIKE 'rag%%'"
        )
    }


def resources(*types: str) -> list[dict]:
    return [{'type': type_, 'id': str(uuid4())} for type_ in types]


def test_up_drops_the_tables_their_jobs_and_the_collection_access(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    server = client.post('/api/servers', headers=HEADERS, json={'name': 'Host', 'hostname': 'host.test'}).json()
    with store.connect() as conn:
        run(conn, DOWN)
        project = store.project(conn, 'shop')
        actor = conn.execute('SELECT id FROM ka.principals LIMIT 1').fetchone()['id']
        conn.execute("INSERT INTO ka.rag_workspaces(project_id,settings) VALUES (%s,'{}')", (project,))
        collection = conn.execute(
            "INSERT INTO ka.rag_collections(project_id,name) VALUES (%s,'runbooks') RETURNING id", (project,)
        ).fetchone()['id']
        folder = conn.execute(
            "INSERT INTO ka.rag_folders(project_id,path) VALUES (%s,'docs') RETURNING id", (project,)
        ).fetchone()['id']
        rag_jobs = [
            conn.execute(
                f"""INSERT INTO ka.jobs(project_id,kind,{column},status,started_at,finished_at)
                VALUES (%s,%s,%s,'succeeded',now(),now()) RETURNING id""",
                (project, kind, target),
            ).fetchone()['id']
            for kind, column, target in (
                ('rag_collection_sync', 'rag_collection_id', collection),
                ('rag_folder_index', 'rag_folder_id', folder),
            )
        ]
        for job in rag_jobs:
            conn.execute("INSERT INTO ka.job_events(job_id,event_type,message) VALUES (%s,'log','synced')", (job,))
        kept_job = conn.execute(
            "INSERT INTO ka.jobs(project_id,kind,server_id) VALUES (%s,'server_test',%s) RETURNING id",
            (project, UUID(server['id'])),
        ).fetchone()['id']
        given = resources('collection', 'service', 'collection', 'code_project')
        agent = f'agent-{uuid4().hex[:6]}'
        conn.execute(
            'INSERT INTO ka.agents(id,name,system_prompt,access) VALUES (%s,%s,%s,%s)',
            (agent, 'Agent', 'Be brief.', Jsonb({'resources': given, 'roles': {'content_editor': False}})),
        )
        session = uuid4()
        conn.execute(
            """INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
            context_snapshot,purpose,agent_config,access) VALUES (%s,%s,%s,%s,%s,'Run','{}','{}','chat',%s,%s)""",
            (
                session,
                project,
                agent,
                actor,
                a_model(conn, actor),
                Jsonb({'name': 'Agent', 'access': {'resources': given}}),
                Jsonb({'resources': given, 'roles': {'content_editor': True}}),
            ),
        )
        run(conn, UP)

        assert tables(conn) == set()
        jobs = conn.execute('SELECT id FROM ka.jobs').fetchall()
        assert [row['id'] for row in jobs] == [kept_job]
        assert (
            conn.execute('SELECT count(*) AS n FROM ka.job_events WHERE job_id=ANY(%s)', (rag_jobs,)).fetchone()['n']
            == 0
        )
        columns = {
            row['column_name']
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='ka' AND table_name='jobs'"
            )
        }
        assert not {'rag_collection_id', 'rag_folder_id'} & columns
        kept = [item for item in given if item['type'] != 'collection']
        assert conn.execute('SELECT access FROM ka.agents WHERE id=%s', (agent,)).fetchone()['access'] == {
            'resources': kept,
            'roles': {'content_editor': False},
        }
        row = conn.execute('SELECT access,agent_config FROM ka.agent_sessions WHERE id=%s', (session,)).fetchone()
        assert row['access'] == {'resources': kept, 'roles': {'content_editor': True}}
        assert row['agent_config'] == {'name': 'Agent', 'access': {'resources': kept}}
        with pytest.raises(psycopg.errors.CheckViolation, match='jobs_(kind_)?check'):
            conn.execute(
                "INSERT INTO ka.jobs(project_id,kind,server_id) VALUES (%s,'rag_collection_sync',%s)",
                (project, UUID(server['id'])),
            )


def test_a_model_change_no_longer_touches_rag(store, tmp_path):
    from test_embedding_models import EMBEDDING
    from test_llm import create

    model = create(_client(tmp_path, repository_store=store), EMBEDDING)
    with store.connect() as conn:
        assert tables(conn) == set()
        conn.execute("UPDATE ka.llm_models SET name='Renamed' WHERE id=%s", (model['id'],))  # the trigger still runs


def test_up_down_up(store):
    with store.connect() as conn:
        run(conn, DOWN)
        run(conn, UP)
        assert tables(conn) == set()
