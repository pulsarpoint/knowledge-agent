"""Accepting and rejecting discovery proposals."""
import asyncio

import pytest

from discovery_world import world  # noqa: F401  (fixture)
from knowledge_admin.servers.discovery import (ACCEPT_SERVICE_FIRST, DECIDED, DOCUMENT_ADDED, SERVICE_ADDED, SERVICE_CHANGED,
                                       SERVICE_GONE, STALE)
from knowledge_admin.servers.discovery import DOCUMENT_CHANGED
from knowledge_admin.documents.api import DocumentInput, save_document
from knowledge_admin.tools.catalog import CATALOG
from test_discovery_runs import config_of, ssh_ready, start
from test_repositories import HEADERS

REDIS = {'name': 'redis', 'service_role': 'cache', 'description': 'Session cache', 'owner': 'web team',
         'deployment': {'runtime': 'systemd', 'runtime_name': 'redis-server', 'port': 6379}}


@pytest.fixture
def run(world):
    ssh_ready(world)
    sid = start(world).json()['id']
    return world, sid, config_of(world, sid)


def propose(config, tool, **arguments):
    spec = CATALOG[tool]
    return asyncio.run(spec.run({'config': config}, spec.arguments.model_validate(arguments)))


def listed(world, sid):
    response = world.client.get(f'/api/discovery/{sid}/proposals', headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()['proposals']


def decide(world, proposal, action, **body):
    return world.client.post(f"/api/discovery/proposals/{proposal['id']}/{action}", headers=HEADERS,
                             json={'revision': proposal['revision'], **body})


def service(world, name):
    return next((row for row in world.client.get('/api/services', headers=HEADERS).json()['services'] if row['name'] == name), None)


def test_accepting_a_new_service_creates_it_with_its_deployment_and_connections(run):
    world, sid, config = run
    propose(config, 'propose_service', **REDIS, depends_on=['nginx'], built_from=['Shop'])
    [proposal] = listed(world, sid)
    accepted = decide(world, proposal, 'accept')
    assert accepted.status_code == 200, accepted.text
    redis = service(world, 'redis')
    assert redis['service_role'] == 'cache' and redis['owner'] == 'web team'
    [deployment] = redis['deployments']
    assert deployment['server_id'] == world.server['id'] and deployment['port'] == 6379
    assert [item['name'] for item in redis['dependencies']] == ['nginx']
    assert [item['name'] for item in redis['repositories']] == ['Shop']
    [proposal] = listed(world, sid)
    assert proposal['status'] == 'accepted' and proposal['result_id'] == redis['id']
    again = decide(world, proposal, 'accept')
    assert again.status_code == 409 and again.json()['error']['message'] == DECIDED


def test_an_update_shows_its_changes_and_applies_only_the_kept_fields(run):
    world, sid, config = run
    propose(config, 'propose_service', name='nginx', description='Reverse proxy and TLS', owner='platform',
            service_type='builtin', deployment={'runtime_name': 'nginx', 'deployed_version': '1.24.0'})
    [proposal] = listed(world, sid)
    assert proposal['current']['description'] == 'Front proxy for the shop'
    assert proposal['changes'] == ['service_type', 'description', 'owner']
    assert proposal['current_deployment']['runtime_name'] == 'nginx'
    accepted = decide(world, proposal, 'accept', fields=['description'])
    assert accepted.status_code == 200, accepted.text
    nginx = service(world, 'nginx')
    assert nginx['description'] == 'Reverse proxy and TLS' and nginx['owner'] == 'web team' and nginx['service_type'] == 'unclassified'
    [deployment] = nginx['deployments']
    assert deployment['deployed_version'] == '1.24.0' and deployment['port'] == 80 and deployment['runtime'] == 'systemd'
    assert [item['name'] for item in nginx['repositories']] == ['Shop']


def test_unticking_every_field_still_updates_the_deployment_and_adds_connections(run):
    world, sid, config = run
    with world.store.connect() as conn:
        conn.execute("INSERT INTO ka.services(project_id,name) VALUES (%s,'postgres')", (world.project,))
    # nginx's only deployment on web-1 in Production is the one this proposal updates, even with another runtime name
    propose(config, 'propose_service', name='nginx', description='Changed', depends_on=['postgres'],
            deployment={'runtime_name': 'nginx-canary', 'port': 8080})
    [proposal] = listed(world, sid)
    assert decide(world, proposal, 'accept', fields=[]).status_code == 200
    nginx = service(world, 'nginx')
    assert nginx['description'] == 'Front proxy for the shop'
    [deployment] = nginx['deployments']
    assert (deployment['runtime_name'], deployment['port'], deployment['runtime']) == ('nginx-canary', 8080, 'systemd')
    assert [item['name'] for item in nginx['dependencies']] == ['postgres']


def test_a_service_changed_since_the_proposal_is_refused(run):
    world, sid, config = run
    propose(config, 'propose_service', name='nginx', description='Changed', deployment={'runtime_name': 'nginx'})
    with world.store.connect() as conn:
        conn.execute('UPDATE ka.services SET revision=revision+1 WHERE id=%s', (world.nginx,))
    [proposal] = listed(world, sid)
    refused = decide(world, proposal, 'accept')
    assert refused.status_code == 409 and refused.json()['error']['message'] == SERVICE_CHANGED


def test_a_new_service_another_run_added_meanwhile_becomes_an_update(run):
    world, sid, config = run
    propose(config, 'propose_service', **REDIS)
    other = start(world).json()['id']
    propose(config_of(world, other), 'propose_service', **REDIS)
    assert decide(world, listed(world, other)[0], 'accept').status_code == 200
    [proposal] = listed(world, sid)
    refused = decide(world, proposal, 'accept')
    assert refused.status_code == 409 and refused.json()['error']['message'] == SERVICE_ADDED.format(name='redis')
    [proposal] = listed(world, sid)
    assert proposal['status'] == 'pending' and proposal['service_id'] == service(world, 'redis')['id']
    assert decide(world, proposal, 'accept').status_code == 200
    assert [row['name'] for row in world.client.get('/api/services', headers=HEADERS).json()['services']].count('redis') == 1


def test_documents_are_created_attached_and_versioned(run):
    world, sid, config = run
    propose(config, 'propose_document', title='Disk layout', markdown='# Disks', description='Mounts')
    propose(config, 'propose_document', title='web-1 overview', markdown='# web-1, updated')
    new, version = listed(world, sid)
    accepted = decide(world, new, 'accept')
    assert accepted.status_code == 200, accepted.text
    document_id = accepted.json()['result_id']
    attached = world.client.get('/api/inventory/documents', headers=HEADERS,
                                params={'entry_kind': 'server', 'entry_id': world.server['id']}).json()['documents']
    assert {item['title'] for item in attached} == {'web-1 overview', 'Disk layout'}
    with world.store.connect() as conn:
        row = conn.execute('''SELECT d.folder,d.slug,v.origin,v.markdown FROM ka.documents d JOIN ka.document_versions v
            ON v.id=d.current_version_id WHERE d.id=%s''', (document_id,)).fetchone()
    assert (row['folder'], row['slug'], row['origin']) == ('servers/web-1', 'web-1-disk-layout', 'generated')
    assert decide(world, version, 'accept').status_code == 200
    with world.store.connect() as conn:
        latest = conn.execute('''SELECT d.revision,v.markdown FROM ka.documents d JOIN ka.document_versions v ON v.id=d.current_version_id
            WHERE d.id=%s''', (version['document_id'],)).fetchone()
    assert latest['revision'] == 2 and latest['markdown'] == '# web-1, updated'


def test_a_title_whose_slug_is_taken_gets_a_free_slug(run):
    world, sid, config = run
    for markdown in ('# one', '# two'):
        with world.store.connect() as conn:  # detach the first, so the second is a new document, not a new version
            conn.execute("DELETE FROM ka.inventory_documents WHERE server_id=%s AND document_id IN (SELECT id FROM ka.document_details WHERE title='Disk layout')",
                         (world.server['id'],))
        other = start(world).json()['id']
        propose(config_of(world, other), 'propose_document', title='Disk layout', markdown=markdown)
        [proposal] = listed(world, other)
        assert proposal['document_id'] is None
        assert decide(world, proposal, 'accept').status_code == 200
    with world.store.connect() as conn:
        slugs = [row['slug'] for row in conn.execute("SELECT slug FROM ka.documents WHERE folder='servers/web-1' ORDER BY slug")]
    assert slugs == ['web-1-disk-layout', 'web-1-disk-layout-2']


def test_a_document_title_another_run_attached_meanwhile_becomes_a_new_version(run):
    world, sid, config = run
    propose(config, 'propose_document', title='Disk layout', markdown='# mine')
    other = start(world).json()['id']
    propose(config_of(world, other), 'propose_document', title='Disk layout', markdown='# theirs')
    assert decide(world, listed(world, other)[0], 'accept').status_code == 200
    [proposal] = listed(world, sid)
    refused = decide(world, proposal, 'accept')
    assert refused.status_code == 409 and refused.json()['error']['message'] == DOCUMENT_ADDED.format(title='Disk layout')
    [proposal] = listed(world, sid)
    assert proposal['document_id'] is not None and decide(world, proposal, 'accept').status_code == 200


def test_a_document_about_a_proposed_service_waits_for_it_and_goes_when_it_is_rejected(run):
    world, sid, config = run
    propose(config, 'propose_service', **REDIS)
    propose(config, 'propose_document', title='Redis', markdown='# redis', about_service='redis')
    service_proposal, document = listed(world, sid)
    refused = decide(world, document, 'accept')
    assert refused.status_code == 400 and refused.json()['error']['message'] == ACCEPT_SERVICE_FIRST
    rejected = decide(world, service_proposal, 'reject')
    assert rejected.status_code == 200 and rejected.json()['rejected_documents'] == 1
    assert [item['status'] for item in listed(world, sid)] == ['rejected', 'rejected']


def test_a_document_about_an_accepted_proposed_service_attaches_to_the_new_service(run):
    world, sid, config = run
    propose(config, 'propose_service', **REDIS)
    propose(config, 'propose_document', title='Redis', markdown='# redis', about_service='redis')
    service_proposal, document = listed(world, sid)
    assert decide(world, service_proposal, 'accept').status_code == 200
    assert decide(world, listed(world, sid)[1], 'accept').status_code == 200
    redis = service(world, 'redis')
    attached = world.client.get('/api/inventory/documents', headers=HEADERS,
                                params={'entry_kind': 'service', 'entry_id': redis['id']}).json()['documents']
    assert [item['title'] for item in attached] == ['Redis']


def test_a_stale_revision_and_unknown_ids_are_refused(run):
    world, sid, config = run
    propose(config, 'propose_service', **REDIS)
    propose(config, 'propose_service', **{**REDIS, 'owner': 'ops'})  # replaces: revision 2
    [proposal] = listed(world, sid)
    stale = decide(world, {**proposal, 'revision': 1}, 'reject')
    assert stale.status_code == 409 and stale.json()['error']['message'] == STALE
    assert world.client.get('/api/discovery/00000000-0000-0000-0000-000000000000/proposals', headers=HEADERS).status_code == 404
    assert decide(world, {'id': '00000000-0000-0000-0000-000000000000', 'revision': 1}, 'reject').status_code == 404


def test_accepting_one_proposal_does_not_strand_another_update_of_a_service_it_linked(run):
    world, sid, config = run
    propose(config, 'propose_service', **REDIS, depends_on=['nginx'])
    propose(config, 'propose_service', name='nginx', description='Changed', deployment={'runtime_name': 'nginx'})
    redis, nginx = listed(world, sid)
    assert decide(world, redis, 'accept').status_code == 200
    nginx = listed(world, sid)[1]
    refused = decide(world, nginx, 'accept')
    assert refused.status_code == 409 and refused.json()['error']['message'] == SERVICE_CHANGED
    nginx = listed(world, sid)[1]
    assert nginx['status'] == 'pending'
    assert decide(world, nginx, 'accept').status_code == 200


def test_a_document_about_a_service_deleted_since_is_refused(run):
    world, sid, config = run
    propose(config, 'propose_service', **REDIS)
    propose(config, 'propose_document', title='Redis', markdown='# redis', about_service='redis')
    service_proposal, document = listed(world, sid)
    assert decide(world, service_proposal, 'accept').status_code == 200
    with world.store.connect() as conn:
        conn.execute("DELETE FROM ka.service_deployments WHERE service_id IN (SELECT id FROM ka.services WHERE name='redis')")
        conn.execute("DELETE FROM ka.services WHERE name='redis'")
    refused = decide(world, listed(world, sid)[1], 'accept')
    assert refused.status_code == 400 and refused.json()['error']['message'] == SERVICE_GONE.format(name='redis')


def test_an_update_ignores_archived_code_projects(run):
    world, sid, config = run
    propose(config, 'propose_service', name='nginx', description='Changed', deployment={'runtime_name': 'nginx'})
    with world.store.connect() as conn:
        conn.execute("UPDATE ka.repositories SET archived_at=now() WHERE name='Shop'")
    [proposal] = listed(world, sid)
    accepted = decide(world, proposal, 'accept')
    assert accepted.status_code == 200, accepted.text
    assert service(world, 'nginx')['description'] == 'Changed'
    with world.store.connect() as conn:  # the link to the archived code project stays
        assert conn.execute("SELECT 1 FROM ka.inventory_links WHERE kind='built_from' AND from_service_id=%s AND to_code_project_id=%s",
                            (world.nginx, world.shop)).fetchone() is not None


def test_a_blocked_export_folder_moves_new_documents_to_another_folder(run):
    world, sid, config = run
    propose(config, 'propose_document', title='Disk layout', markdown='# Disks')
    [proposal] = listed(world, sid)
    with world.store.connect() as conn:
        conn.execute("UPDATE ka.documents SET folder='servers',slug='web-1' WHERE slug='web-1-overview'")
    accepted = decide(world, proposal, 'accept')
    assert accepted.status_code == 200, accepted.text
    with world.store.connect() as conn:
        row = conn.execute('SELECT folder,slug FROM ka.documents WHERE id=%s', (accepted.json()['result_id'],)).fetchone()
    assert row['folder'] == 'servers/web-1-2' and row['slug'] == 'web-1-disk-layout'


def test_a_folderless_servers_document_moves_new_documents_to_another_root(run):
    world, sid, config = run
    propose(config, 'propose_document', title='Disk layout', markdown='# Disks')
    [proposal] = listed(world, sid)
    with world.store.connect() as conn:
        conn.execute("UPDATE ka.documents SET folder=NULL,slug='servers' WHERE slug='web-1-overview'")
    accepted = decide(world, proposal, 'accept')
    assert accepted.status_code == 200, accepted.text
    with world.store.connect() as conn:
        row = conn.execute('SELECT folder FROM ka.documents WHERE id=%s', (accepted.json()['result_id'],)).fetchone()
    assert row['folder'] == 'server-documents/web-1'


def test_a_new_version_of_a_document_changed_since_the_proposal_is_refused_once(run):
    world, sid, config = run
    propose(config, 'propose_document', title='web-1 overview', markdown='# mine')
    [proposal] = listed(world, sid)
    assert proposal['payload']['matched_document_revision'] == 1
    with world.store.connect() as conn:
        save_document(conn, world.project, None, DocumentInput(slug='web-1-overview', title='web-1 overview', description='x',
                      markdown='# theirs', revision=1), proposal['document_id'])
    refused = decide(world, proposal, 'accept')
    assert refused.status_code == 409 and refused.json()['error']['message'] == DOCUMENT_CHANGED
    [proposal] = listed(world, sid)
    assert proposal['payload']['matched_document_revision'] == 2
    assert decide(world, proposal, 'accept').status_code == 200


def test_a_document_converted_to_a_new_version_stores_the_revision(run):
    world, sid, config = run
    propose(config, 'propose_document', title='Disk layout', markdown='# mine')
    other = start(world).json()['id']
    propose(config_of(world, other), 'propose_document', title='Disk layout', markdown='# theirs')
    assert decide(world, listed(world, other)[0], 'accept').status_code == 200
    assert decide(world, listed(world, sid)[0], 'accept').status_code == 409
    [proposal] = listed(world, sid)
    assert proposal['payload']['matched_document_revision'] == 1
