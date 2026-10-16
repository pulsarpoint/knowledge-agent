"""The per_run fixture (a saved agent other tests narrow), the credentials a run's access references, and the
run_resources record: 0076 recorded the resources of each run, 0116 drops it from agent_sessions (a run's access
object describes what it was given)."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from knowledge_admin.agents.runner import load_credentials
from knowledge_admin.knowledge_agents.config import get_agent_config, load_entries
from access_helpers import access_of, give
from test_generations import native_agent, sessions  # noqa: F401  (fixture)
from test_operations import setup as ssh_setup

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0076_run_resources.up.sql', '0076_run_resources.down.sql'
TABLES = {'agent_sessions_cc', 'agent_sessions', 'operations_runs'}  # as 0076 left them (before 0081 and 0084)


@pytest.fixture
def per_run(sessions):
    """A native per-run agent, Code analyst, that may be given the Payments API code project, the runbooks collection,
    Test host (SSH allowed, saved key, its own instructions) and the Payments service, which is also deployed on Test
    host. Not in its selection: Payments worker and the private collection. prod-1 does not allow SSH."""
    client, store = sessions.client, sessions.store
    host, key = ssh_setup(client)
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute("INSERT INTO ka.rag_workspaces(project_id,settings) VALUES (%s,'{}')", (project,))
        collections = {
            name: str(
                conn.execute(
                    """INSERT INTO ka.rag_collections(project_id,name,description)
            VALUES (%s,%s,%s) RETURNING id""",
                    (project, name, f'The {name} documents'),
                ).fetchone()['id']
            )
            for name in ('runbooks', 'private')
        }
        conn.execute(
            """INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,runtime_name,port)
            VALUES (%s,%s,%s,'Staging','payments-api',8080)""",
            (project, sessions.service, host['id']),
        )
    analyst = native_agent(
        client,
        'Code analyst',
        model_id=sessions.gpt,
    )
    found = SimpleNamespace(
        **vars(sessions),
        analyst=analyst,
        host=host,
        key=key,
        runbooks=collections['runbooks'],
        private=collections['private'],
    )
    allow(found)
    return found


def allow(s, code=None, collections=None, servers=None, services=None) -> None:
    """The analyst's access object: by default its selection (Payments API, runbooks, Test host, Payments)."""
    given = {
        'code_project': [s.repo] if code is None else code,
        'collection': [s.runbooks] if collections is None else collections,
        'server': [s.host['id']] if servers is None else servers,
        'service': [s.service] if services is None else services,
    }
    with s.store.connect() as conn:
        give(conn, s.analyst, [(type_, item) for type_, ids in given.items() for item in ids])


def run_resource_columns(conn) -> set[str]:
    return {
        row['table_name']
        for row in conn.execute("""SELECT table_name FROM information_schema.columns
        WHERE table_schema='ka' AND column_name='run_resources'""")
    }


def test_runs_record_their_resources_and_down_drops_only_the_records(store):
    with store.connect() as conn:
        for later in (
            '0116_drop_run_resources.down.sql',
            '0084_remove_claude_code.down.sql',
            '0081_test_sessions.down.sql',
        ):  # newest first
            conn.execute((MIGRATIONS / later).read_text(), prepare=False)
        conn.commit()
        assert run_resource_columns(conn) == TABLES
        for name in (
            'agent_sessions_cc_run_resources',
            'agent_sessions_run_resources',
            'operations_runs_run_resources',
        ):
            definition = conn.execute(
                'SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conname=%s', (name,)
            ).fetchone()['d']
            assert "jsonb_typeof(run_resources) = 'object'" in definition
        conn.execute((MIGRATIONS / DOWN).read_text(), prepare=False)
        conn.commit()
        assert run_resource_columns(conn) == set()
        conn.execute((MIGRATIONS / UP).read_text(), prepare=False)
        conn.commit()
        assert run_resource_columns(conn) == TABLES


def test_0116_drops_the_record_and_down_brings_it_back_empty(store):
    with store.connect() as conn:
        assert run_resource_columns(conn) == set()
        conn.execute((MIGRATIONS / '0116_drop_run_resources.down.sql').read_text(), prepare=False)
        conn.commit()
        assert run_resource_columns(conn) == {'agent_sessions'}
        nullable = conn.execute(
            """SELECT is_nullable FROM information_schema.columns
            WHERE table_schema='ka' AND table_name='agent_sessions' AND column_name='run_resources'"""
        ).fetchone()['is_nullable']
        assert nullable == 'YES'
        conn.execute((MIGRATIONS / '0116_drop_run_resources.up.sql').read_text(), prepare=False)
        conn.commit()
        assert run_resource_columns(conn) == set()


def test_credentials_are_decrypted_only_for_the_servers_a_run_was_given(per_run):
    s = per_run
    with s.store.connect() as conn:
        [host] = load_entries(conn, s.store.project(conn, 'shop'), {'server': [s.host['id']]})
    config = get_agent_config(s.analyst, conninfo=s.store.conninfo)
    key_id = host['private_key_ref']['id']
    assert key_id not in load_credentials(s.store, {**config, 'access': access_of([])})
    assert load_credentials(s.store, {**config, 'access': access_of([host])})[key_id] == s.key['private_key']
