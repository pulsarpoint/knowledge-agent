"""Accepting and rejecting discovery proposals."""

import asyncio
from uuid import uuid4

import pytest

from discovery_world import world  # noqa: F401  (fixture)
from knowledge_admin.servers.discovery import (
    ACCEPT_SERVICE_FIRST,
    DECIDED,
    DOCUMENT_ADDED,
    RESOURCE_GONE,
    SERVICE_ADDED,
    SERVICE_CHANGED,
    SERVICE_GONE,
    STALE,
)
from knowledge_admin.servers.discovery import DOCUMENT_CHANGED
from knowledge_admin.tools.catalog import CATALOG
from knowledge_admin.documents.api import document_slug
from live_documents import live_document, rewrite_live_document
from test_discovery_runs import config_of, ssh_ready, start
from test_repositories import HEADERS

REDIS = {
    'name': 'redis',
    'service_role': 'cache',
    'description': 'Session cache',
    'owner': 'web team',
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


def listed(world, sid):
    response = world.client.get(f'/api/discovery/{sid}/proposals', headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()['proposals']


def decide(world, proposal, action, **body):
    return world.client.post(
        f'/api/discovery/proposals/{proposal["id"]}/{action}',
        headers=HEADERS,
        json={'revision': proposal['revision'], **body},
    )


def publish(world, accepted):
    """Publish (or promote) the draft an accepted document proposal made; returns the live document's id."""
    draft = accepted.json()['proposal']['draft_id']
    url = f'/api/documentation/drafts/{draft}'
    revision = world.client.get(url, headers=HEADERS).json()['revision']
    published = world.client.post(f'{url}/promote', headers=HEADERS, json={'revision': revision})
    assert published.status_code == 200, published.text
    return published.json()['document_id']


def service(world, name):
    return next(
        (row for row in world.client.get('/api/services', headers=HEADERS).json()['services'] if row['name'] == name),
        None,
    )


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
    propose(
        config,
        'propose_service',
        name='nginx',
        description='Reverse proxy and TLS',
        owner='platform',
        service_type='builtin',
        deployment={'runtime_name': 'nginx', 'deployed_version': '1.24.0'},
    )
    [proposal] = listed(world, sid)
    assert proposal['current']['description'] == 'Front proxy for the shop'
    assert proposal['changes'] == ['service_type', 'description', 'owner']
    assert proposal['current_deployment']['runtime_name'] == 'nginx'
    accepted = decide(world, proposal, 'accept', fields=['description'])
    assert accepted.status_code == 200, accepted.text
    nginx = service(world, 'nginx')
    assert (
        nginx['description'] == 'Reverse proxy and TLS'
        and nginx['owner'] == 'web team'
        and nginx['service_type'] == 'unclassified'
    )
    [deployment] = nginx['deployments']
    assert (
        deployment['deployed_version'] == '1.24.0' and deployment['port'] == 80 and deployment['runtime'] == 'systemd'
    )
    assert [item['name'] for item in nginx['repositories']] == ['Shop']


def test_unticking_every_field_still_updates_the_deployment_and_adds_connections(run):
    world, sid, config = run
    with world.store.connect() as conn:
        conn.execute("INSERT INTO ka.services(project_id,name) VALUES (%s,'postgres')", (world.project,))
    # nginx's only deployment on web-1 in Production is the one this proposal updates, even with another runtime name
    propose(
        config,
        'propose_service',
        name='nginx',
        description='Changed',
        depends_on=['postgres'],
        deployment={'runtime_name': 'nginx-canary', 'port': 8080},
    )
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
    assert [row['name'] for row in world.client.get('/api/services', headers=HEADERS).json()['services']].count(
        'redis'
    ) == 1


def test_documents_become_drafts_attached_when_published_and_promoted_as_new_versions(run):
    world, sid, config = run
    propose(config, 'propose_document', title='Disk layout', markdown='# Disks', description='Mounts')
    propose(config, 'propose_document', title='web-1 overview', markdown='# web-1, updated')
    new, version = listed(world, sid)
    accepted = decide(world, new, 'accept')
    assert accepted.status_code == 200, accepted.text
    document_id = accepted.json()['result_id']

    def attached():
        found = world.client.get(f'/api/inventory/server/{world.server["id"]}/documents', headers=HEADERS).json()
        return [
            *([found['inventory_document']] if found['inventory_document'] else []),
            *(
                item
                for item in found['documents']
                if item['resource']['id'] == str(world.server['id'])
                and item['id'] != (found['inventory_document'] or {}).get('id')
            ),
        ]

    # web-1 had no inventory document: the brand-new draft is it, shown unpublished; no other document yet.
    found = world.client.get(f'/api/inventory/server/{world.server["id"]}/documents', headers=HEADERS).json()
    assert (found['inventory_document']['id'], found['inventory_document']['published']) == (document_id, False)
    own = [item['title'] for item in found['documents'] if item['resource']['id'] == world.server['id']]
    assert own == ['web-1 overview']  # the brand-new draft is not listed; nginx's runbook belongs to a child
    with world.store.connect() as conn:
        row = conn.execute(
            'SELECT folder,slug,origin,markdown,is_draft,version FROM ka.documents WHERE id=%s', (document_id,)
        ).fetchone()
        links = conn.execute(
            """SELECT server_id,service_id,connection,added_how,removed_at FROM ka.document_resources
            WHERE document_id=%s""",
            (document_id,),
        ).fetchall()
    assert (row['folder'], row['slug'], row['origin']) == ('servers/web-1', 'web-1-disk-layout', 'generated')
    assert (row['is_draft'], row['version']) == (True, None)  # a brand-new draft
    # A new document is built for its server (no service): a locked link added by discovery.
    assert [
        (str(link['server_id']), link['service_id'], link['connection'], link['added_how'], link['removed_at'])
        for link in links
    ] == [(world.server['id'], None, 'built_for', 'discovery', None)]
    assert publish(world, accepted) == document_id  # published: the same id, still the inventory document
    assert {item['title'] for item in attached()} == {'web-1 overview', 'Disk layout'}
    accepted = decide(world, version, 'accept')
    assert accepted.status_code == 200, accepted.text
    assert publish(world, accepted) == version['document_id']
    with world.store.connect() as conn:
        latest = conn.execute(
            'SELECT revision,version,markdown FROM ka.documents WHERE id=%s', (version['document_id'],)
        ).fetchone()
        archived = conn.execute(
            'SELECT version,markdown FROM ka.document_archive WHERE document_id=%s', (version['document_id'],)
        ).fetchall()
    assert latest['revision'] == 2 and latest['version'] == 2 and latest['markdown'] == '# web-1, updated'
    assert [(item['version'], item['markdown']) for item in archived] == [(1, '# web-1 overview\n\nBody.')]


def test_a_title_whose_slug_is_taken_gets_a_free_slug(run):
    world, sid, config = run
    for markdown in ('# one', '# two'):
        with world.store.connect() as conn:  # detach the first, so the second is a new document, not a new version
            conn.execute(
                "DELETE FROM ka.document_resources WHERE server_id=%s AND document_id IN (SELECT id FROM ka.document_details WHERE title='Disk layout')",
                (world.server['id'],),
            )
        other = start(world).json()['id']
        propose(config_of(world, other), 'propose_document', title='Disk layout', markdown=markdown)
        [proposal] = listed(world, other)
        assert proposal['document_id'] is None
        assert decide(world, proposal, 'accept').status_code == 200
    with world.store.connect() as conn:
        slugs = [
            row['slug']
            for row in conn.execute("SELECT slug FROM ka.documents WHERE folder='servers/web-1' ORDER BY slug")
        ]
    assert slugs == ['web-1-disk-layout', 'web-1-disk-layout-2']


def test_a_document_title_another_run_attached_meanwhile_becomes_a_new_version(run):
    world, sid, config = run
    give_inventory_document(world)  # so the other run's document is an ordinary one
    propose(config, 'propose_document', title='Disk layout', markdown='# mine')
    other = start(world).json()['id']
    propose(config_of(world, other), 'propose_document', title='Disk layout', markdown='# theirs')
    publish(world, decide(world, listed(world, other)[0], 'accept'))
    [proposal] = listed(world, sid)
    refused = decide(world, proposal, 'accept')
    assert refused.status_code == 409 and refused.json()['error']['message'] == DOCUMENT_ADDED.format(
        title='Disk layout'
    )
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
    publish(world, decide(world, listed(world, sid)[1], 'accept'))
    redis = service(world, 'redis')
    found = world.client.get(f'/api/inventory/service/{redis["id"]}/documents', headers=HEADERS).json()
    attached = [
        *([found['inventory_document']] if found['inventory_document'] else []),
        *(
            item
            for item in found['documents']
            if item['resource']['id'] == str(redis['id'])
            and item['id'] != (found['inventory_document'] or {}).get('id')
        ),
    ]
    assert [item['title'] for item in attached] == ['Redis']
    with world.store.connect() as conn:
        links = conn.execute(
            """SELECT r.server_id,r.service_id,r.connection,r.added_how FROM ka.document_resources r
            JOIN ka.document_details d ON d.id=r.document_id WHERE d.title='Redis'""",
        ).fetchall()
    # A new document about a service is built for the service, not the server.
    assert [(link['server_id'], str(link['service_id']), link['connection'], link['added_how']) for link in links] == [
        (None, redis['id'], 'built_for', 'discovery')
    ]


def test_a_document_about_a_service_accepts_without_the_runs_resource(run):
    world, sid, config = run
    propose(config, 'propose_service', **REDIS)
    propose(config, 'propose_document', title='Redis', markdown='# redis', about_service='redis')
    propose(config, 'propose_document', title='Disk layout', markdown='# Disks')
    service_proposal, about_redis, about_server = listed(world, sid)
    assert decide(world, service_proposal, 'accept').status_code == 200
    with world.store.connect() as conn:  # the run's resource is gone (and so is the proposal's server)
        conn.execute(
            """UPDATE ka.agent_sessions SET agent_config=jsonb_set(agent_config,'{started_from,id}',to_jsonb(%s::text))
            WHERE id=%s""",
            (str(uuid4()), sid),
        )
        conn.execute('UPDATE ka.discovery_proposals SET server_id=NULL WHERE session_id=%s', (sid,))
    accepted = decide(world, listed(world, sid)[1], 'accept')
    assert accepted.status_code == 200, accepted.text
    with world.store.connect() as conn:
        row = conn.execute('SELECT folder FROM ka.documents WHERE id=%s', (accepted.json()['result_id'],)).fetchone()
    assert row['folder'] == 'service-documents/redis'  # no run resource to pick the folder: the service's own
    refused = decide(world, listed(world, sid)[2], 'accept')
    assert refused.status_code == 400 and refused.json()['error']['message'] == RESOURCE_GONE


def test_a_stale_revision_and_unknown_ids_are_refused(run):
    world, sid, config = run
    propose(config, 'propose_service', **REDIS)
    propose(config, 'propose_service', **{**REDIS, 'owner': 'ops'})  # replaces: revision 2
    [proposal] = listed(world, sid)
    stale = decide(world, {**proposal, 'revision': 1}, 'reject')
    assert stale.status_code == 409 and stale.json()['error']['message'] == STALE
    assert (
        world.client.get('/api/discovery/00000000-0000-0000-0000-000000000000/proposals', headers=HEADERS).status_code
        == 404
    )
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
        conn.execute(
            "DELETE FROM ka.service_deployments WHERE service_id IN (SELECT id FROM ka.services WHERE name='redis')"
        )
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
        assert (
            conn.execute(
                "SELECT 1 FROM ka.inventory_links WHERE kind='built_from' AND from_service_id=%s AND to_code_project_id=%s",
                (world.nginx, world.shop),
            ).fetchone()
            is not None
        )


def test_a_blocked_export_folder_moves_new_documents_to_another_folder(run):
    world, sid, config = run
    propose(config, 'propose_document', title='Disk layout', markdown='# Disks')
    [proposal] = listed(world, sid)
    with world.store.connect() as conn:
        conn.execute("UPDATE ka.documents SET folder='servers',slug='web-1' WHERE slug='web-1-overview'")
    accepted = decide(world, proposal, 'accept')
    assert accepted.status_code == 200, accepted.text
    with world.store.connect() as conn:
        row = conn.execute(
            'SELECT folder,slug FROM ka.documents WHERE id=%s', (accepted.json()['result_id'],)
        ).fetchone()
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
    rewrite_live_document(world.store, proposal['document_id'], '# theirs')
    refused = decide(world, proposal, 'accept')
    assert refused.status_code == 409 and refused.json()['error']['message'] == DOCUMENT_CHANGED
    [proposal] = listed(world, sid)
    assert proposal['payload']['matched_document_revision'] == 2
    assert decide(world, proposal, 'accept').status_code == 200


def test_a_document_converted_to_a_new_version_stores_the_revision(run):
    world, sid, config = run
    give_inventory_document(world)  # so the other run's document is an ordinary one
    propose(config, 'propose_document', title='Disk layout', markdown='# mine')
    other = start(world).json()['id']
    propose(config_of(world, other), 'propose_document', title='Disk layout', markdown='# theirs')
    theirs = publish(world, decide(world, listed(world, other)[0], 'accept'))
    assert decide(world, listed(world, sid)[0], 'accept').status_code == 409
    [proposal] = listed(world, sid)
    live = world.client.get(f'/api/documentation/documents/{theirs}', headers=HEADERS).json()
    assert proposal['document_id'] == theirs and proposal['payload']['matched_document_revision'] == live['revision']


# Inventory documents: discovery never changes an existing one.


def server_documents(world) -> dict:
    response = world.client.get(f'/api/inventory/server/{world.server["id"]}/documents', headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def give_inventory_document(world, title='web-1 inventory') -> str:
    """A live document tagged inventory built for web-1."""
    with world.store.connect() as conn:
        saved = live_document(
            conn,
            world.project,
            document_slug(title),
            title,
            f'# {title}\n\nBody.',
            server_ids=[world.server['id']],
            tags=['inventory'],
        )
    return str(saved['id'])


def snapshot(world, document_id) -> dict:
    with world.store.connect() as conn:
        row = conn.execute(
            "SELECT title,markdown,revision,version,'inventory'=ANY(tags) AS inventory,is_draft FROM ka.documents WHERE id=%s",
            (document_id,),
        ).fetchone()
        drafts = conn.execute('SELECT count(*) AS n FROM ka.documents WHERE draft_of=%s', (document_id,)).fetchone()
    return {**row, 'drafts': drafts['n']}


def test_a_document_for_a_resource_without_an_inventory_document_is_its_inventory_draft(run):
    world, sid, config = run
    propose(config, 'propose_document', title='Disk layout', markdown='# Disks', description='Mounts')
    [proposal] = listed(world, sid)
    accepted = decide(world, proposal, 'accept')
    assert accepted.status_code == 200, accepted.text
    draft = accepted.json()['result_id']
    assert snapshot(world, draft)['inventory'] is True
    inventory = server_documents(world)['inventory_document']
    assert (inventory['id'], inventory['is_draft']) == (draft, True)
    assert publish(world, accepted) == draft  # publish keeps the tag
    assert snapshot(world, draft)['inventory'] is True
    assert server_documents(world)['inventory_document']['id'] == draft


def test_a_proposal_never_changes_a_live_inventory_document(run):
    world, sid, config = run
    inventory = give_inventory_document(world)
    before = snapshot(world, inventory)
    propose(config, 'propose_document', title='web-1 inventory', markdown='# rewritten', description='')
    [proposal] = listed(world, sid)
    assert proposal['document_id'] is None  # a new document beside it, never a new version of it
    accepted = decide(world, proposal, 'accept')
    assert accepted.status_code == 200, accepted.text
    draft = accepted.json()['result_id']
    assert draft != inventory
    made = snapshot(world, draft)
    assert (made['is_draft'], made['inventory'], made['markdown']) == (True, False, '# rewritten')
    assert snapshot(world, inventory) == before  # content, revision, tag and drafts (none) unchanged
    publish(world, accepted)
    assert snapshot(world, draft)['inventory'] is False
    found = server_documents(world)
    assert found['inventory_document']['id'] == inventory
    own = [
        item['title']
        for item in found['documents']
        if item['resource']['id'] == world.server['id'] and item['id'] != inventory
    ]
    assert sorted(own) == ['web-1 inventory', 'web-1 overview']


def test_a_proposal_never_changes_a_brand_new_inventory_draft(run):
    world, sid, config = run
    propose(config, 'propose_document', title='Disk layout', markdown='# first', description='')
    first = decide(world, listed(world, sid)[0], 'accept').json()['result_id']
    before = snapshot(world, first)
    assert before['inventory'] is True
    other = start(world).json()['id']
    propose(config_of(world, other), 'propose_document', title='Disk layout', markdown='# second', description='')
    [proposal] = listed(world, other)
    accepted = decide(world, proposal, 'accept')
    assert accepted.status_code == 200, accepted.text
    second = accepted.json()['result_id']
    assert second != first and snapshot(world, second)['inventory'] is False
    assert snapshot(world, first) == before
    assert server_documents(world)['inventory_document']['id'] == first


def test_a_matched_document_that_became_the_inventory_document_gets_a_new_document_not_a_draft_of_it(run):
    world, sid, config = run
    with world.store.connect() as conn:
        saved = live_document(conn, world.project, 'disks', 'Disk layout', '# Disks', server_ids=[world.server['id']])
    document = str(saved['id'])
    propose(config, 'propose_document', title='Disk layout', markdown='# new disks', description='')
    [proposal] = listed(world, sid)
    assert proposal['document_id'] == document  # a new version of an ordinary document
    with world.store.connect() as conn:
        conn.execute("UPDATE ka.documents SET tags='{inventory}' WHERE id=%s", (document,))
    before = snapshot(world, document)
    accepted = decide(world, proposal, 'accept')
    assert accepted.status_code == 200, accepted.text
    draft = accepted.json()['result_id']
    made = snapshot(world, draft)
    assert (made['is_draft'], made['inventory'], made['markdown']) == (True, False, '# new disks')
    with world.store.connect() as conn:
        assert conn.execute('SELECT draft_of FROM ka.documents WHERE id=%s', (draft,)).fetchone()['draft_of'] is None
    assert snapshot(world, document) == before
