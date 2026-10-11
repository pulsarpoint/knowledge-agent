"""Discovery runs: the server page's data, starting a run (SSH set up and allowed), what the run pins, and the list."""
from uuid import UUID, uuid4

import asyncssh
import pytest

from discovery_world import world  # noqa: F401  (fixture)
from knowledge_admin.discovery import DISCOVERY_FINAL, NOT_ALLOWED, NOT_CONFIGURED
from knowledge_admin.tools.catalog import effective_tools
from test_profiles import create, update
from test_repositories import HEADERS


def ssh_ready(world, server=None):
    server = server or world.server
    key = asyncssh.generate_private_key('ssh-ed25519').export_private_key().decode()
    current = world.client.get(f"/api/servers/{server['id']}/ssh", headers=HEADERS).json()
    saved = world.client.put(f"/api/servers/{server['id']}/ssh", headers=HEADERS, json={
        'revision': current['revision'], 'username': 'ops', 'port': 22, 'private_key': key, 'known_hosts': ''})
    assert saved.status_code == 200, saved.text


def start(world, server=None, **change):
    server = server or world.server
    return world.client.post(f"/api/servers/{server['id']}/discovery", headers=HEADERS,
                             json={'model_id': world.model, 'prompt': 'Explore web-1.', **change})


def config_of(world, session_id):
    with world.store.connect() as conn:
        return conn.execute('SELECT agent_config FROM ka.agent_sessions WHERE id=%s', (session_id,)).fetchone()['agent_config']


def test_the_server_page_shows_environments_deployments_and_readiness(world):
    page = world.client.get(f"/api/servers/{world.server['id']}", headers=HEADERS)
    assert page.status_code == 200, page.text
    body = page.json()
    assert body['server']['name'] == 'web-1' and [e['name'] for e in body['environments']] == ['Production']
    [deployment] = body['deployments']
    assert deployment['service_name'] == 'nginx' and deployment['environment'] == 'Production' and deployment['port'] == 80
    assert body['discovery'] == {'ready': False, 'reason': NOT_CONFIGURED}
    ssh_ready(world)
    assert world.client.get(f"/api/servers/{world.server['id']}", headers=HEADERS).json()['discovery'] == {'ready': True, 'reason': None}
    assert world.client.get(f'/api/servers/{uuid4()}', headers=HEADERS).status_code == 404


def test_a_run_needs_ssh_set_up_and_allowed(world):
    refused = start(world)
    assert refused.status_code == 400 and refused.json()['error']['message'] == NOT_CONFIGURED
    ssh_ready(world)
    with world.store.connect() as conn:
        conn.execute('UPDATE ka.servers SET ssh_allowed=false WHERE id=%s', (world.server['id'],))
    refused = start(world)
    assert refused.status_code == 400 and refused.json()['error']['message'] == NOT_ALLOWED
    assert world.client.post(f'/api/servers/{uuid4()}/discovery', headers=HEADERS,
                             json={'model_id': world.model, 'prompt': 'x'}).status_code == 404
    assert start(world, prompt='  ').status_code == 400


def test_a_run_pins_ssh_to_its_server_and_queues_the_prompt(world):
    ssh_ready(world)
    created = start(world, prompt='  Explore web-1.  ')
    assert created.status_code == 201, created.text
    sid = created.json()['id']
    config = config_of(world, sid)
    assert [server['id'] for server in config['ssh']['servers']] == [world.server['id']]
    assert config['discovery'] == {'session_id': sid, 'server_id': world.server['id']}
    assert config['project_id'] == str(world.project)
    assert '10.0.0.7' in config['system_prompt']['content'] and world.server['id'] in config['system_prompt']['content']
    rows = {item['tool']: item for item in config['tools']['items']}
    assert not rows['propose_service']['subagent_allowed'] and not rows['propose_document']['subagent_allowed']
    offered = {spec.name for spec, _ in effective_tools(config, config['ssh']['servers'])}
    assert {'ssh_run', 'get_server_info_basic', 'propose_service', 'propose_document'} <= offered
    with world.store.connect() as conn:
        session = conn.execute('SELECT purpose,server_id,agent_id,title FROM ka.agent_sessions WHERE id=%s', (sid,)).fetchone()
        [first] = conn.execute('SELECT text FROM ka.agent_session_inputs WHERE session_id=%s', (sid,)).fetchall()
    assert session['purpose'] == 'discovery' and str(session['server_id']) == world.server['id'] and session['agent_id'] is None
    assert session['title'] == 'Discovery · web-1'
    assert first['text'] == f'Explore web-1.\n\n{DISCOVERY_FINAL}'


def test_two_runs_on_one_server_can_run_at_once_and_are_listed(world):
    ssh_ready(world)
    first, second = start(world).json()['id'], start(world, prompt='Look at the databases.').json()['id']
    with world.store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET status='working' WHERE id=%s", (first,))
        conn.execute("""INSERT INTO ka.discovery_proposals(project_id,session_id,server_id,number,kind,payload,status,decided_at,result_id)
            VALUES (%s,%s,%s,1,'service','{"name":"a"}','pending',NULL,NULL),
                   (%s,%s,%s,2,'service','{"name":"b"}','rejected',now(),NULL)""",
            (world.project, first, world.server['id'], world.project, first, world.server['id']))
    runs = world.client.get(f"/api/servers/{world.server['id']}/discovery", headers=HEADERS).json()['runs']
    assert [run['id'] for run in runs] == [second, first]
    assert (runs[1]['pending'], runs[1]['accepted'], runs[1]['rejected']) == (1, 0, 1)
    assert runs[1]['status'] == 'working' and runs[0]['model_name'] == 'GPT'


def test_a_run_offers_no_experts_so_ssh_stays_on_its_server(world):
    expert = update(world.client, create(world.client, 'Payments'), expert=True, when_to_use='Payments.', model_id=world.model)
    assert expert.status_code == 200, expert.text
    ssh_ready(world)
    config = config_of(world, start(world).json()['id'])
    assert config['experts'] == []
    assert not [item for item in config['tools']['items'] if item['tool'].startswith(('ask_', 'expert:'))]
