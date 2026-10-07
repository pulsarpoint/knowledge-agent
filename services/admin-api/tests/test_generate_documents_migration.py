
"""0073: the one-shot generation goes with its tables and history; documents and their export layout stay."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from knowledge_admin.documents import export_input

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0073_remove_documentation_generation.up.sql', '0073_remove_documentation_generation.down.sql'
GONE = ('documentation_generations', 'documentation_workflows', 'documentation_workflow_jobs',
        'documentation_workflow_details')


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def count(conn, sql: str, *params) -> int:
    return conn.execute(f'SELECT count(*) AS n FROM {sql}', params).fetchone()['n']


def generated_document(conn, project) -> dict:
    """What the one-shot generation left behind (before 0073): a succeeded job with its phase layout, its events and
    run record, a workflow, and a document version it wrote."""
    job, document, version = uuid4(), uuid4(), uuid4()
    conn.execute("INSERT INTO ka.jobs(id,project_id,kind) VALUES (%s,%s,'documentation_generate')", (job, project))
    conn.execute("""INSERT INTO ka.documentation_generations(job_id,project_id,mode,instructions,agent_snapshot,
        prompt_snapshot,source_snapshot) VALUES (%s,%s,'create','Write','{}','[]','{}')""", (job, project))
    conn.execute('INSERT INTO ka.documents(id,project_id,slug,current_version_id) VALUES (%s,%s,%s,%s)',
                 (document, project, f'payments-api-{document.hex[:6]}', version))
    conn.execute("""INSERT INTO ka.document_versions(id,document_id,project_id,version,title,description,purpose,audience,
        markdown,content_hash,source_snapshot,origin,generation_id) VALUES (%s,%s,%s,1,'Payments API','','api','both',
        '# API',encode(sha256(convert_to('# API','UTF8')),'hex'),%s,'generated',%s)""",
        (version, document, project, Jsonb({'code_projects': []}), job))
    metadata = {'phase': 'api', 'service_id': str(uuid4()), 'service_name': 'Payments', 'job_id': str(job)}
    conn.execute("UPDATE ka.jobs SET status='succeeded',started_at=now(),finished_at=now(),result=%s WHERE id=%s",
                 (Jsonb({'documents': [], 'phase_metadata': metadata}), job))
    conn.execute("INSERT INTO ka.job_events(job_id,event_type,message) VALUES (%s,'succeeded','Documents saved')", (job,))
    conn.execute("""INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
        VALUES (%s,%s,1,'start','search','{}')""", (project, f'doc:{job}:abc'))
    workflow = uuid4()
    conn.execute("""INSERT INTO ka.documentation_workflows(id,project_id,name,scope)
        VALUES (%s,%s,'Payments documentation','service')""", (workflow, project))
    conn.execute('INSERT INTO ka.documentation_workflow_jobs(workflow_id,job_id,project_id) VALUES (%s,%s,%s)',
                 (workflow, job, project))
    return {'job': job, 'document': document, 'version': version, 'metadata': metadata, 'workflow': workflow}


def unrelated_history(conn, project, workflow) -> dict:
    """What 0073 must leave alone: run records of the other run kinds, jobs of other kinds with their events, and a
    graph job a workflow linked to."""
    jobs = {kind: uuid4() for kind in ('code_index', 'repository_sync')}
    for kind, job in jobs.items():  # one repository each: a repository runs one job at a time
        repository = uuid4()
        conn.execute("INSERT INTO ka.repositories(id,project_id,slug,name) VALUES (%s,%s,%s,'Payments')",
                     (repository, project, f'payments-{repository.hex[:6]}'))
        conn.execute('INSERT INTO ka.jobs(id,project_id,kind,repository_id) VALUES (%s,%s,%s,%s)',
                     (job, project, kind, repository))
        conn.execute("INSERT INTO ka.job_events(job_id,event_type,message) VALUES (%s,'queued','Queued')", (job,))
    conn.execute('INSERT INTO ka.documentation_workflow_jobs(workflow_id,job_id,project_id) VALUES (%s,%s,%s)',
                 (workflow, jobs['code_index'], project))
    keys = [f'cc:{uuid4()}', f'run:{uuid4()}', f'board:{uuid4()}']
    for key in keys:
        conn.execute("""INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
            VALUES (%s,%s,1,'start','search','{}')""", (project, key))
    return {'jobs': list(jobs.values()), 'run_keys': keys}


def test_up_removes_the_generation_its_history_and_runs_but_keeps_documents(store):
    with store.connect() as conn:
        run(conn, DOWN)
        conn.commit()
        project = store.project(conn, 'shop')
        made = generated_document(conn, project)
        kept = unrelated_history(conn, project, made['workflow'])
        conn.commit()
        run(conn, UP)
        conn.commit()
        for name in GONE:
            assert conn.execute('SELECT to_regclass(%s) AS r', (f'ka.{name}',)).fetchone()['r'] is None, name
        for job in kept['jobs']:  # other job kinds survive, even one a workflow linked to
            assert count(conn, 'ka.jobs WHERE id=%s', job) == 1
            assert count(conn, 'ka.job_events WHERE job_id=%s', job) == 1
        for key in kept['run_keys']:  # only doc: run records go
            assert count(conn, 'ka.agent_run_events WHERE run_key=%s', key) == 1, key
        assert count(conn, 'ka.jobs WHERE id=%s', made['job']) == 0
        assert count(conn, 'ka.job_events WHERE job_id=%s', made['job']) == 0
        assert count(conn, "ka.agent_run_events WHERE starts_with(run_key,'doc:')") == 0
        version = conn.execute('SELECT origin,generation_id FROM ka.document_versions WHERE id=%s',
                               (made['version'],)).fetchone()
        assert (version['origin'], version['generation_id']) == ('generated', made['job'])  # history stays
        # The service phase layout moved onto the document: its export folder does not change.
        assert export_input(conn, made['document'])['phase_metadata'] == {**made['metadata'], 'source_commits': {}}
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                conn.execute("INSERT INTO ka.jobs(project_id,kind) VALUES (%s,'documentation_generate')", (project,))


def test_new_workspaces_still_get_the_api_documentation_prompt(store):
    with store.connect() as conn:  # the seed no longer touches the dropped workflows
        project = store.project(conn, 'fresh-after-0073')
        prompt = conn.execute('''SELECT v.version FROM ka.documentation_prompts p
            JOIN ka.documentation_prompt_versions v ON v.id=p.active_version_id
            WHERE p.id=md5('knowledge-agent-v3:documentation:api:' || %s::text)::uuid''', (project,)).fetchone()
        assert prompt['version'] == 3


def test_down_restores_the_empty_schema_and_keeps_documents(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.commit()
        run(conn, DOWN)
        conn.commit()
        made = generated_document(conn, project)
        conn.commit()
        run(conn, UP)
        conn.commit()
        run(conn, DOWN)  # a version written by a removed run does not block the rollback
        conn.commit()
        for name in GONE:
            assert conn.execute('SELECT to_regclass(%s) AS r', (f'ka.{name}',)).fetchone()['r'] is not None, name
        assert count(conn, 'ka.documentation_generations') == 0
        assert count(conn, 'ka.document_versions WHERE id=%s', made['version']) == 1
        assert conn.execute("""SELECT 1 FROM information_schema.columns WHERE table_schema='ka'
            AND table_name='documents' AND column_name='phase_metadata'""").fetchone() is None
        run(conn, UP)
        conn.commit()
