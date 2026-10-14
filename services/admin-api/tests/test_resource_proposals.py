"""Proposals across a resource's runs (GET /api/discovery/proposals?tag=&status=) and document proposals from service
and environment runs: accepted, they are built for that service or environment; published, attached to it."""

from discovery_world import world  # noqa: F401  (fixture)
from test_discovery_review import decide, propose, publish
from test_discovery_runs import config_of, ssh_ready
from test_repositories import HEADERS

TOOLS = ['get_resource', 'get_document']


def start(world, started_from: dict, tools: list[str]) -> str:
    created = world.client.post(
        '/api/sessions',
        headers=HEADERS,
        json={
            'started_from': started_from,
            'model_id': world.model,
            'system_prompt': 'You propose.',
            'message': 'Propose documents.',
            'tools': [*TOOLS, *tools],
            'tags': ['proposals'],
        },
    )
    assert created.status_code == 201, created.text
    return created.json()['id']


def across(world, tag: str, status: str | None = None) -> list[dict]:
    params = {'tag': tag, **({'status': status} if status else {})}
    response = world.client.get('/api/discovery/proposals', headers=HEADERS, params=params)
    assert response.status_code == 200, response.text
    return response.json()['runs']


def attached(world, kind: str, entry_id) -> set[str]:
    response = world.client.get(
        '/api/inventory/documents', headers=HEADERS, params={'entry_kind': kind, 'entry_id': str(entry_id)}
    )
    assert response.status_code == 200, response.text
    return {item['title'] for item in response.json()['documents']}


def test_the_proposals_of_a_servers_runs_are_grouped_by_run_newest_first_and_filtered_by_status(world):
    ssh_ready(world)
    tag = f'servers/{world.server["id"]}'
    first = start(world, {'server_id': world.server['id']}, ['propose_document'])
    propose(config_of(world, first), 'propose_document', title='Disk layout', markdown='# Disks', description='')
    second = start(world, {'server_id': world.server['id']}, ['propose_service', 'propose_document'])
    propose(config_of(world, second), 'propose_service', name='redis', deployment={'runtime': 'systemd'})
    propose(config_of(world, second), 'propose_document', title='Backups', markdown='# Backups', description='')
    start(world, {'server_id': world.server['id']}, [])  # no proposals: not listed
    other = start(world, {'service_id': str(world.nginx)}, ['propose_document'])
    propose(config_of(world, other), 'propose_document', title='Health', markdown='# Health', description='')
    with world.store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET created_at=now()+interval '1 minute' WHERE id=%s", (second,))
    runs = across(world, tag)
    assert [run['session_id'] for run in runs] == [second, first]
    assert runs[0]['title'] == 'Propose documents.' and runs[0]['started_by'] and runs[0]['started_at']
    assert [item['payload'].get('name') or item['payload']['title'] for item in runs[0]['proposals']] == [
        'redis',
        'Backups',
    ]
    assert [item['payload']['title'] for item in runs[1]['proposals']] == ['Disk layout']
    accepted = decide(world, runs[1]['proposals'][0], 'accept')
    assert accepted.status_code == 200, accepted.text
    decide(world, runs[0]['proposals'][1], 'reject')
    assert [run['session_id'] for run in across(world, tag, 'accepted')] == [first]
    rejected = across(world, tag, 'rejected')
    assert [(run['session_id'], len(run['proposals'])) for run in rejected] == [(second, 1)]
    assert [run['session_id'] for run in across(world, tag, 'pending')] == [second]
    assert [run['session_id'] for run in across(world, f'services/{world.nginx}')] == [other]
    assert across(world, 'services/none') == []
    assert world.client.get('/api/discovery/proposals', headers=HEADERS).status_code == 400
    bad = world.client.get('/api/discovery/proposals', headers=HEADERS, params={'tag': tag, 'status': 'maybe'})
    assert bad.status_code == 400


def test_a_document_from_a_service_run_is_built_for_and_attached_to_the_service(world):
    sid = start(world, {'service_id': str(world.nginx)}, ['propose_document'])
    propose(config_of(world, sid), 'propose_document', title='Health checks', markdown='# Health', description='How')
    [run] = across(world, f'services/{world.nginx}')
    accepted = decide(world, run['proposals'][0], 'accept')
    assert accepted.status_code == 200, accepted.text
    draft = accepted.json()['result_id']
    with world.store.connect() as conn:
        row = conn.execute('SELECT folder,slug FROM ka.documents WHERE id=%s', (draft,)).fetchone()
        links = conn.execute(
            'SELECT server_id,service_id,environment_id,connection FROM ka.document_resources WHERE document_id=%s',
            (draft,),
        ).fetchall()
    assert (row['folder'], row['slug']) == ('service-documents/nginx', 'nginx-health-checks')
    assert [(link['server_id'], link['service_id'], link['environment_id'], link['connection']) for link in links] == [
        (None, world.nginx, None, 'built_for')
    ]
    assert publish(world, accepted) == draft
    assert attached(world, 'service', world.nginx) == {'nginx runbook', 'Health checks'}
    assert attached(world, 'server', world.server['id']) == {'web-1 overview'}


def test_a_document_from_an_environment_run_is_built_for_and_attached_to_the_environment(world):
    sid = start(world, {'environment_id': str(world.environment)}, ['propose_document'])
    config = config_of(world, sid)
    propose(config, 'propose_document', title='Overview', markdown='# Production', description='The environment')
    propose(config, 'propose_document', title='Proxy', markdown='# nginx here', description='', about_service='nginx')
    [run] = across(world, f'environments/{world.environment}')
    overview, proxy = run['proposals']
    accepted = decide(world, overview, 'accept')
    assert accepted.status_code == 200, accepted.text
    draft = accepted.json()['result_id']
    with world.store.connect() as conn:
        row = conn.execute('SELECT folder,slug FROM ka.documents WHERE id=%s', (draft,)).fetchone()
        links = conn.execute(
            'SELECT server_id,service_id,environment_id,connection FROM ka.document_resources WHERE document_id=%s',
            (draft,),
        ).fetchall()
    assert (row['folder'], row['slug']) == ('environments/production', 'production-overview')
    assert [(link['server_id'], link['service_id'], link['environment_id'], link['connection']) for link in links] == [
        (None, None, world.environment, 'built_for')
    ]
    assert publish(world, accepted) == draft
    assert attached(world, 'environment', world.environment) == {'Overview'}
    assert attached(world, 'server', world.server['id']) == {'web-1 overview'}
    about = decide(world, proxy, 'accept')  # about a service: built for the service, in the environment's folder
    assert about.status_code == 200, about.text
    publish(world, about)
    assert attached(world, 'service', world.nginx) == {'nginx runbook', 'Proxy'}
    assert attached(world, 'environment', world.environment) == {'Overview'}
