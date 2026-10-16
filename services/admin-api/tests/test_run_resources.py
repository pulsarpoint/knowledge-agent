"""Run resources: resolve gives a run exactly the resources it was given, validated against the configuration's
selection (here a saved agent's, loaded from its access object; the ad-hoc runs use it); nothing passed means nothing,
never "all". 0076 records them with each run."""

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from knowledge_admin.agents.runner import load_credentials
from knowledge_admin.knowledge_agents.config import get_agent_config
from knowledge_admin.knowledge_agents.run_resources import RunResourceError, RunResources, resolve
from access_helpers import give
from test_generations import native_agent, sessions  # noqa: F401  (fixture)
from test_operations import setup as ssh_setup
from test_repositories import HEADERS

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0076_run_resources.up.sql', '0076_run_resources.down.sql'
TABLES = {'agent_sessions_cc', 'agent_sessions', 'operations_runs'}  # as 0076 left them (before 0081 and 0084)
NOTHING = {'code_project_ids': [], 'collection_ids': [], 'server_ids': [], 'service_ids': []}


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


def effective(s, requested: RunResources | None, **kwargs) -> dict:
    with s.store.connect() as conn:
        return resolve(
            conn,
            s.store.project(conn, 'shop'),
            get_agent_config(s.analyst, conninfo=s.store.conninfo),
            requested,
            **kwargs,
        )


def refusal(s, requested: RunResources | None, **kwargs) -> str:
    with pytest.raises(RunResourceError) as raised:
        effective(s, requested, **kwargs)
    return str(raised.value)


def run_resource_columns(conn) -> set[str]:
    return {
        row['table_name']
        for row in conn.execute("""SELECT table_name FROM information_schema.columns
        WHERE table_schema='ka' AND column_name='run_resources'""")
    }


def test_runs_record_their_resources_and_down_drops_only_the_records(store):
    with store.connect() as conn:
        for later in ('0084_remove_claude_code.down.sql', '0081_test_sessions.down.sql'):  # newest first
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


def test_a_run_gets_exactly_its_resources_and_nothing_means_nothing(per_run):
    s = per_run
    given = effective(
        s, RunResources(code_project_ids=[s.repo], collection_ids=[s.runbooks], server_ids=[s.host['id']])
    )
    assert [p['name'] for p in given['code']['projects']] == ['Payments API'] and given['code']['all_projects'] is False
    assert [c['name'] for c in given['documents']['collections']] == ['runbooks']
    assert given['documents']['all_collections'] is False
    [server] = given['ssh']['servers']
    assert (server['id'], bool(server['private_key_ref'])) == (s.host['id'], True)
    assert given['services']['items'] == []
    assert given['run_resources'] == {
        'code_project_ids': [s.repo],
        'collection_ids': [s.runbooks],
        'server_ids': [s.host['id']],
        'service_ids': [],
    }
    nothing = effective(s, None)
    assert (
        nothing['code']['projects'],
        nothing['documents']['collections'],
        nothing['ssh']['servers'],
        nothing['services']['items'],
        nothing['run_resources'],
    ) == ([], [], [], [], NOTHING)
    assert nothing['code']['enabled'] and nothing['documents']['enabled']  # their tools say nothing was given
    allow(s, code=[s.repo, s.worker], collections=[s.runbooks, s.private])  # any of them may be passed, only those
    given = effective(s, RunResources(code_project_ids=[s.worker], collection_ids=[s.private]))
    assert [(p['name'], p['folder_paths']) for p in given['code']['projects']] == [
        ('Payments worker', [f'{s.data}/code/worker'])
    ]
    assert [c['name'] for c in given['documents']['collections']] == ['private']
    assert given['documents']['all_collections'] is False and given['code']['all_projects'] is False
    nothing = effective(s, None)
    assert (nothing['code']['projects'], nothing['documents']['collections']) == ([], [])


def test_resources_outside_the_selection_the_workspace_or_the_agents_kinds_are_refused(per_run):
    s = per_run
    other = s.client.post(
        '/api/servers', headers=HEADERS, json={'name': 'other-host', 'hostname': 'other.test', 'ssh_allowed': True}
    ).json()
    unknown = str(uuid4())
    assert (
        refusal(s, RunResources(code_project_ids=[s.worker]))
        == 'Code project Payments worker is not allowed for this agent.'
    )
    assert refusal(s, RunResources(collection_ids=[s.private])) == 'Collection private is not allowed for this agent.'
    assert refusal(s, RunResources(server_ids=[other['id']])) == 'Server other-host is not allowed for this agent.'
    assert refusal(s, RunResources(code_project_ids=[unknown])) == (
        f'Code project {unknown} is not available: it was removed, archived or disabled.'
    )
    assert refusal(s, RunResources(server_ids=[s.server['id']])) == (
        'Server prod-1 is not available: it was removed, archived or disabled.'
    )  # SSH not allowed
    allow(s, code=[s.repo, s.worker])  # the agent still uses code projects when one is archived
    with s.store.connect() as conn:
        conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id=%s', (s.repo,))
    assert refusal(s, RunResources(code_project_ids=[s.repo])) == (
        'Code project Payments API is not available: it was removed, archived or disabled.'
    )
    with s.store.connect() as conn:
        conn.execute('UPDATE ka.repositories SET archived_at=NULL WHERE id=%s', (s.repo,))
    allow(s, collections=[])
    assert refusal(s, RunResources(collection_ids=[s.runbooks])) == 'This agent does not use document collections.'


def test_services_and_environments_bring_their_code_projects_and_servers(per_run):
    s = per_run
    assert (
        refusal(s, RunResources(service_ids=[s.service]))
        == 'Code project Payments worker is not allowed for this agent.'
    )
    allow(s, code=[s.repo, s.worker])
    assert effective(s, RunResources(service_ids=[s.service]))['run_resources'] == {
        'code_project_ids': [s.repo, s.worker],
        'collection_ids': [],
        'server_ids': [s.host['id']],
        'service_ids': [s.service],
    }  # prod-1 does not allow SSH: skipped
    allow(s, code=[s.repo, s.worker], services=[])
    assert refusal(s, RunResources(service_ids=[s.service])) == 'This agent does not use services.'
    assert effective(s, None, environment_ids=[UUID(s.environment)])['run_resources'] == {
        'code_project_ids': [s.repo, s.worker],
        'collection_ids': [],
        'server_ids': [s.host['id']],
        'service_ids': [],
    }


def test_credentials_are_decrypted_only_for_the_servers_a_run_was_given(per_run):
    s = per_run
    without = effective(s, RunResources(code_project_ids=[s.repo]))
    with_host = effective(s, RunResources(server_ids=[s.host['id']]))
    key_id = with_host['ssh']['servers'][0]['private_key_ref']['id']
    assert key_id not in load_credentials(s.store, without)
    assert load_credentials(s.store, with_host)[key_id] == s.key['private_key']


def test_a_brought_server_without_a_usable_connection_is_left_out_but_a_chosen_one_is_refused(per_run):
    s = per_run  # prod-1 allows SSH here, but has no username: the agent's snapshot lists it as not enabled
    with s.store.connect() as conn:
        conn.execute('UPDATE ka.servers SET ssh_allowed=true WHERE id=%s', (s.server['id'],))
    allow(s, code=[s.repo, s.worker], servers=[s.host['id'], s.server['id']])
    brought = effective(s, None, environment_ids=[UUID(s.environment)])['run_resources']['server_ids']
    assert s.server['id'] not in brought
    assert 'Server prod-1 is not available' in refusal(s, RunResources(server_ids=[s.server['id']]))


def test_a_run_records_what_was_chosen_apart_from_what_it_brings(per_run):
    s = per_run  # an environment brings Payments (and its code projects and Test host): chosen records only it
    allow(s, code=[s.repo, s.worker])
    environment = UUID(s.environment)
    given = effective(s, RunResources(code_project_ids=[s.repo]), environment_ids=[environment, environment])
    assert given['chosen_resources'] == {
        **NOTHING,
        'code_project_ids': [s.repo],
        'environment_ids': [s.environment],
    }
    assert given['run_resources'] == {
        'code_project_ids': [s.repo, s.worker],
        'collection_ids': [],
        'server_ids': [s.host['id']],
        'service_ids': [s.service],
    }  # unchanged: the tools still reach what the environment brought
    service = effective(s, RunResources(service_ids=[s.service]))
    assert service['chosen_resources'] == {**NOTHING, 'service_ids': [s.service], 'environment_ids': []}
    assert service['run_resources']['server_ids'] == [s.host['id']]
    assert effective(s, None)['chosen_resources'] == {**NOTHING, 'environment_ids': []}
