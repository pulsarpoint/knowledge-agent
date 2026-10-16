"""propose_service and propose_document: checked and stored as pending proposals of the run."""

import asyncio
from uuid import uuid4

import pydantic
import pytest

from discovery_world import world  # noqa: F401  (fixture)
from knowledge_admin.capabilities.discovery import NOT_DISCOVERY, resolve_environment
from knowledge_admin.tools.catalog import CATALOG, catalog_listing
from test_discovery_runs import config_of, ssh_ready, start

SERVICE = {
    'name': 'redis',
    'service_role': 'cache',
    'description': 'Session cache',
    'deployment': {'runtime': 'systemd', 'runtime_name': 'redis-server', 'port': 6379},
}


@pytest.fixture
def run(world):
    ssh_ready(world)
    sid = start(world).json()['id']
    return world, sid, config_of(world, sid)


def propose(config, tool, **arguments):
    spec = CATALOG[tool]
    return asyncio.run(spec.run({'config': config}, spec.arguments.model_validate(arguments)))


def proposals(world, sid):
    with world.store.connect() as conn:
        return conn.execute(
            'SELECT * FROM ka.discovery_proposals WHERE session_id=%s ORDER BY number', (sid,)
        ).fetchall()


def test_a_new_service_takes_the_servers_only_environment(run):
    world, sid, config = run
    assert (
        propose(config, 'propose_service', **SERVICE, depends_on=['nginx'], built_from=['shop'])
        == 'Proposal 1: new service redis.'
    )
    [row] = proposals(world, sid)
    assert row['kind'] == 'service' and row['service_id'] is None and row['status'] == 'pending'
    deployment = row['payload']['deployment']
    assert deployment['environment'] == 'Production' and deployment['environment_id'] == str(world.environment)
    assert deployment['port'] == 6379 and row['payload']['service_role'] == 'cache'
    assert row['payload']['depends_on'] == [{'id': str(world.nginx), 'name': 'nginx'}]
    assert row['payload']['built_from'] == [{'id': str(world.shop), 'name': 'Shop'}]


def test_an_existing_service_is_an_update_and_a_repeat_replaces_the_pending_proposal(run):
    world, sid, config = run
    update = {
        'name': 'NGINX',
        'description': 'Reverse proxy',
        'deployment': {'runtime_name': 'nginx', 'deployed_version': '1.24'},
    }
    assert propose(config, 'propose_service', **update) == 'Proposal 1: update of service nginx.'
    assert (
        propose(config, 'propose_service', **{**update, 'owner': 'platform'})
        == 'Proposal 1: update of service nginx (replaces your earlier proposal).'
    )
    [row] = proposals(world, sid)
    assert row['service_id'] == world.nginx and row['revision'] == 2 and row['payload']['owner'] == 'platform'
    assert row['payload']['matched_revision'] == 1


def test_unknown_names_and_self_dependencies_are_refused_with_the_valid_names(run):
    world, sid, config = run
    with pytest.raises(ValueError, match='Unknown services: postgres. Known services: nginx.'):
        propose(config, 'propose_service', **SERVICE, depends_on=['postgres'])
    with pytest.raises(ValueError, match='Unknown code projects: api. Known code projects: Shop.'):
        propose(config, 'propose_service', **SERVICE, built_from=['api'])
    with pytest.raises(ValueError, match='A service cannot depend on itself.'):
        propose(config, 'propose_service', **{**SERVICE, 'name': 'nginx'}, depends_on=['nginx'])
    with pytest.raises(pydantic.ValidationError):
        propose(config, 'propose_service', **{**SERVICE, 'deployment': {'port': 70000}})
    assert proposals(world, sid) == []


def test_the_environment_rules():
    production, staging = {'id': uuid4(), 'name': 'Production'}, {'id': uuid4(), 'name': 'Staging'}
    assert resolve_environment([production], None) == {
        'environment_id': str(production['id']),
        'environment': 'Production',
    }
    assert resolve_environment([production, staging], 'staging')['environment'] == 'Staging'
    with pytest.raises(ValueError, match=r'several environments \(Production, Staging\)'):
        resolve_environment([production, staging], None)
    with pytest.raises(ValueError, match="Choose one of this server's environments: Production."):
        resolve_environment([production], 'qa')
    with pytest.raises(ValueError, match='in no environment'):
        resolve_environment([], None)
    assert resolve_environment([], 'lab') == {'environment_id': None, 'environment': 'lab'}


def test_documents_attach_to_the_server_a_service_or_a_proposed_service(run):
    world, sid, config = run
    assert (
        propose(config, 'propose_document', title='Web-1 overview', markdown='# web-1')
        == 'Proposal 1: new version of document "Web-1 overview" about server web-1.'
    )
    assert (
        propose(config, 'propose_document', title='Runbook', markdown='# nginx', about_service='nginx')
        == 'Proposal 2: new document "Runbook" about service nginx.'
    )
    propose(config, 'propose_service', **SERVICE)
    assert (
        propose(config, 'propose_document', title='Redis', markdown='# redis', about_service='redis')
        == 'Proposal 4: new document "Redis" about proposed service redis.'
    )
    with pytest.raises(ValueError, match='No service named mysql. Propose it with propose_service first'):
        propose(config, 'propose_document', title='MySQL', markdown='# x', about_service='mysql')
    rows = proposals(world, sid)
    assert rows[0]['document_id'] is not None and rows[0]['service_id'] is None
    assert rows[1]['service_id'] == world.nginx and rows[1]['document_id'] is None
    assert rows[3]['service_proposal_id'] == rows[2]['id']


def test_proposal_tools_work_only_in_runs_started_from_a_resource_and_never_show_on_the_tools_tab(run):
    world, sid, config = run
    assert NOT_DISCOVERY == 'The proposal tools work only in a run started from a server, service or environment.'
    with pytest.raises(ValueError, match=NOT_DISCOVERY):
        propose({key: value for key, value in config.items() if key != 'session_id'}, 'propose_service', **SERVICE)
    with world.store.connect() as conn:  # another kind of run: its session is not a resource run
        other = conn.execute(
            """INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
            context_snapshot,purpose,document_name,folder,agent_config)
            SELECT gen_random_uuid(),project_id,NULL,actor_id,model_id,'Docs','{}','{}','documents','Docs','docs','{}'
            FROM ka.agent_sessions WHERE id=%s RETURNING id""",
            (sid,),
        ).fetchone()['id']
    with pytest.raises(ValueError, match=NOT_DISCOVERY):
        propose({**config, 'session_id': str(other)}, 'propose_service', **SERVICE)
    assert proposals(world, sid) == []
    listed = {tool['name'] for tool in catalog_listing()}
    assert {'propose_service', 'propose_document', 'get_resource', 'get_document'} <= listed


def test_repeated_documents_replace_their_pending_proposal_and_servers_and_services_stay_apart(run):
    world, sid, config = run
    propose(config, 'propose_service', **SERVICE)
    assert (
        propose(config, 'propose_document', title='Notes', markdown='# one')
        == 'Proposal 2: new document "Notes" about server web-1.'
    )
    assert (
        propose(config, 'propose_document', title='notes', markdown='# two')
        == 'Proposal 2: new document "notes" about server web-1 (replaces your earlier proposal).'
    )
    assert (
        propose(config, 'propose_document', title='Notes', markdown='# nginx', about_service='nginx')
        == 'Proposal 3: new document "Notes" about service nginx.'
    )
    assert (
        propose(config, 'propose_document', title='Notes', markdown='# nginx 2', about_service='NGINX')
        == 'Proposal 3: new document "Notes" about service nginx (replaces your earlier proposal).'
    )
    assert (
        propose(config, 'propose_document', title='Notes', markdown='# redis', about_service='redis')
        == 'Proposal 4: new document "Notes" about proposed service redis.'
    )
    assert (
        propose(config, 'propose_document', title='Notes', markdown='# redis 2', about_service='redis')
        == 'Proposal 4: new document "Notes" about proposed service redis (replaces your earlier proposal).'
    )
    rows = proposals(world, sid)
    assert [row['number'] for row in rows] == [1, 2, 3, 4]
    assert [row['revision'] for row in rows] == [1, 2, 2, 2]
    assert [row['payload'].get('markdown') for row in rows[1:]] == ['# two', '# nginx 2', '# redis 2']


def test_a_decided_proposal_is_never_overwritten_by_a_late_repeat(run):
    world, sid, config = run
    propose(config, 'propose_service', **SERVICE)
    propose(config, 'propose_document', title='Notes', markdown='# one')
    with world.store.connect() as conn:
        conn.execute("UPDATE ka.discovery_proposals SET status='rejected',decided_at=now() WHERE session_id=%s", (sid,))
    assert propose(config, 'propose_service', **{**SERVICE, 'description': 'Late'}) == 'Proposal 3: new service redis.'
    assert (
        propose(config, 'propose_document', title='Notes', markdown='# late')
        == 'Proposal 4: new document "Notes" about server web-1.'
    )
    rows = proposals(world, sid)
    assert [row['status'] for row in rows] == ['rejected', 'rejected', 'pending', 'pending']
    assert rows[0]['payload']['description'] == 'Session cache' and rows[1]['payload']['markdown'] == '# one'
    assert rows[0]['revision'] == 1 and rows[1]['revision'] == 1


def test_the_update_itself_rechecks_the_status(run, monkeypatch):
    from knowledge_admin.capabilities import discovery as tools

    world, sid, config = run
    propose(config, 'propose_service', **SERVICE)
    original = tools._save

    def decided_first(conn, session, kind, payload, existing, **kwargs):
        conn.execute(
            "UPDATE ka.discovery_proposals SET status='rejected',decided_at=now() WHERE id=%s", (existing['id'],)
        )
        return original(conn, session, kind, payload, existing, **kwargs)

    monkeypatch.setattr(tools, '_save', decided_first)
    assert propose(config, 'propose_service', **{**SERVICE, 'description': 'Late'}) == 'Proposal 2: new service redis.'
    rows = proposals(world, sid)
    assert [row['status'] for row in rows] == ['rejected', 'pending']
    assert rows[0]['payload']['description'] == 'Session cache'


def test_text_with_null_characters_is_refused_in_every_proposal_field():
    with pytest.raises(pydantic.ValidationError, match='Text cannot contain null characters.'):
        CATALOG['propose_service'].arguments.model_validate({**SERVICE, 'description': 'a\x00b'})
    with pytest.raises(pydantic.ValidationError, match='Text cannot contain null characters.'):
        CATALOG['propose_service'].arguments.model_validate({**SERVICE, 'deployment': {'runtime_name': 'x\x00'}})
    with pytest.raises(pydantic.ValidationError, match='Text cannot contain null characters.'):
        CATALOG['propose_service'].arguments.model_validate({**SERVICE, 'depends_on': ['nginx\x00']})
    with pytest.raises(pydantic.ValidationError, match='Text cannot contain null characters.'):
        CATALOG['propose_document'].arguments.model_validate({'title': 'T', 'markdown': 'a\x00'})
    with pytest.raises(pydantic.ValidationError, match='Text cannot contain null characters.'):
        CATALOG['propose_document'].arguments.model_validate({'title': 'T', 'markdown': 'a', 'about_service': '\x00'})
