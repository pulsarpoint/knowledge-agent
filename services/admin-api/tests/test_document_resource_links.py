"""A document's resource links from the document side: the Connections answer lists its active links (names per type,
removed ones left out), and one link is added (POST /documents/{id}/resources) or removed (DELETE
/documents/{id}/resources/{type}/{resource_id}) with a revision bump and an audit entry (disposable DB)."""

from uuid import uuid4

import pytest
from knowledge_admin.documents.store import Provenance, add_link
from live_documents import write_live_document
from test_api import _client
from test_inventory_document import message, resource
from test_repositories import HEADERS

DOCUMENTS = '/api/documentation/documents'


@pytest.fixture
def client(store, tmp_path):
    return _client(tmp_path, repository_store=store)


def live(store, slug='runbook', title='Runbook', **links) -> str:
    return write_live_document(store, slug, title, f'# {title}\n', **links)['id']


def deployment(store, service: str, server: str, environment: str) -> str:
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        return str(
            conn.execute(
                """INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment_id)
                VALUES (%s,%s,%s,%s) RETURNING id""",
                (project, service, server, environment),
            ).fetchone()['id']
        )


def resources(client, document: str) -> list[tuple]:
    answer = client.get(f'{DOCUMENTS}/{document}/connections', headers=HEADERS)
    assert answer.status_code == 200, answer.text
    return [(item['type'], item['id'], item['name'], item['connection']) for item in answer.json()['resources']]


def add(client, document: str, type: str, id: str):
    return client.post(f'{DOCUMENTS}/{document}/resources', headers=HEADERS, json={'type': type, 'id': id})


def remove(client, document: str, type: str, id: str):
    return client.delete(f'{DOCUMENTS}/{document}/resources/{type}/{id}', headers=HEADERS)


def revision(store, document: str) -> int:
    with store.connect() as conn:
        return conn.execute('SELECT revision FROM ka.documents WHERE id=%s', (document,)).fetchone()['revision']


def audits(store, document: str) -> list[dict]:
    with store.connect() as conn:
        return conn.execute(
            """SELECT action,details FROM ka.audit_events WHERE resource_type='document' AND resource_id=%s
            AND action='document.links.updated' ORDER BY created_at,id""",
            (document,),
        ).fetchall()


def test_connections_list_the_active_resource_links_named_per_type_by_type_then_name(store, client):
    env = resource(store, 'environments', 'dev')
    server = resource(store, 'servers', 'web-1')
    other_server = resource(store, 'servers', 'app-1')
    service = resource(store, 'services', 'Payments')
    code = resource(store, 'repositories', 'Shop')
    run = deployment(store, service, server, env)
    document = live(
        store,
        environment_ids=[env],
        server_ids=[server, other_server],
        service_ids=[service],
        code_project_ids=[code],
        deployment_ids=[run],
    )
    answer = client.get(f'{DOCUMENTS}/{document}/connections', headers=HEADERS).json()
    assert [(item['type'], item['name'], item['connection']) for item in answer['resources']] == [
        ('environment', 'dev', 'built_for'),
        ('server', 'app-1', 'built_for'),
        ('server', 'web-1', 'built_for'),
        ('service', 'Payments', 'built_for'),
        ('code_project', 'Shop', 'built_for'),
        ('deployment', 'Payments on web-1 (dev)', 'built_for'),
    ]
    first = answer['resources'][0]
    assert (
        first['id'] == env
        and first['added_by'] == 'admin@example.test'
        and first['added_how'] == 'person'
        and first['created_at']
    )
    assert answer['resources'][-1]['service_id'] == service
    assert answer['revision'] == 1
    # the document's own resource_links (the header's badges) name a deployment alike and carry its service
    links = client.get(f'{DOCUMENTS}/{document}', headers=HEADERS).json()['resource_links']
    assert [(item['name'], item['service_id']) for item in links if item['type'] == 'deployment'] == [
        ('Payments on web-1 (dev)', service)
    ]


def test_a_removed_link_is_not_listed(store, client):
    service = resource(store, 'services', 'Payments')
    document = live(store)
    assert add(client, document, 'service', service).status_code == 201
    assert remove(client, document, 'service', service).status_code == 200
    assert resources(client, document) == []


def test_add_a_resource_links_it_with_a_revision_bump_and_an_audit_entry(store, client):
    server = resource(store, 'servers', 'web-1')
    document = live(store)
    added = add(client, document, 'server', server)
    assert added.status_code == 201, added.text
    assert [(item['type'], item['id'], item['name'], item['connection']) for item in added.json()['resources']] == [
        ('server', server, 'web-1', 'linked')
    ]
    assert added.json()['resources'][0]['added_by'] == 'admin@example.test'
    assert added.json()['revision'] == 2 == revision(store, document)
    assert [(row['action'], row['details']) for row in audits(store, document)] == [
        ('document.links.updated', {'links_added': [server], 'links_removed': []})
    ]


def test_add_a_deployment_and_a_code_project(store, client):
    env = resource(store, 'environments', 'dev')
    server = resource(store, 'servers', 'web-1')
    service = resource(store, 'services', 'Payments')
    code = resource(store, 'repositories', 'Shop')
    run = deployment(store, service, server, env)
    document = live(store)
    assert add(client, document, 'deployment', run).status_code == 201
    assert add(client, document, 'code_project', code).status_code == 201
    # a deployment brings its server and service along (as the picker ticks them)
    assert resources(client, document) == [
        ('server', server, 'web-1', 'linked'),
        ('service', service, 'Payments', 'linked'),
        ('code_project', code, 'Shop', 'linked'),
        ('deployment', run, 'Payments on web-1 (dev)', 'linked'),
    ]


def test_adding_a_linked_resource_again_is_a_409(store, client):
    env = resource(store, 'environments', 'dev')
    document = live(store, environment_ids=[env])
    assert message(add(client, document, 'environment', env)) == (
        409,
        'This document is already linked to that environment.',
    )
    assert revision(store, document) == 1 and audits(store, document) == []


def test_an_unknown_or_other_projects_resource_is_a_404(store, client):
    document = live(store)
    assert message(add(client, document, 'service', str(uuid4()))) == (404, 'That service was not found.')
    with store.connect() as conn:
        other = store.project(conn, 'other')
        foreign = conn.execute(
            "INSERT INTO ka.services(project_id,name) VALUES (%s,'Secret') RETURNING id", (other,)
        ).fetchone()['id']
    assert message(add(client, document, 'service', str(foreign))) == (404, 'That service was not found.')
    assert add(client, str(uuid4()), 'service', str(foreign)).status_code == 404
    assert add(client, document, 'agent', str(foreign)).status_code == 400
    assert resources(client, document) == [] and revision(store, document) == 1


def test_remove_a_linked_resource_bumps_the_revision_and_audits(store, client):
    service = resource(store, 'services', 'Payments')
    document = live(store)
    add(client, document, 'service', service)
    removed = remove(client, document, 'service', service)
    assert removed.status_code == 200, removed.text
    assert removed.json()['resources'] == [] and removed.json()['revision'] == 3
    assert [row['details'] for row in audits(store, document)][-1] == {'links_added': [], 'links_removed': [service]}
    history = client.get(f'{DOCUMENTS}/{document}/resource-history', headers=HEADERS).json()['history']
    assert [row['removed_by'] for row in history] == ['admin@example.test']
    assert message(remove(client, document, 'service', service)) == (
        404,
        'This document is not linked to that service.',
    )


def test_removing_a_built_for_link_is_refused(store, client):
    server = resource(store, 'servers', 'web-1')
    document = live(store, server_ids=[server])
    assert message(remove(client, document, 'server', server)) == (
        409,
        "This document was built for server web-1; that link can't be removed.",
    )
    assert resources(client, document) == [('server', server, 'web-1', 'built_for')]
    assert revision(store, document) == 1 and audits(store, document) == []


def test_the_same_service_and_server_in_two_environments_read_differently(store, client):
    dev, prod = resource(store, 'environments', 'dev'), resource(store, 'environments', 'prod')
    server = resource(store, 'servers', 'web-1')
    service = resource(store, 'services', 'Payments')
    on_dev, on_prod = deployment(store, service, server, dev), deployment(store, service, server, prod)
    document = live(store)
    assert add(client, document, 'deployment', on_dev).status_code == 201
    assert add(client, document, 'deployment', on_prod).status_code == 201
    assert resources(client, document) == [
        ('server', server, 'web-1', 'linked'),
        ('service', service, 'Payments', 'linked'),
        ('deployment', on_dev, 'Payments on web-1 (dev)', 'linked'),
        ('deployment', on_prod, 'Payments on web-1 (prod)', 'linked'),
    ]
    links = client.get(f'{DOCUMENTS}/{document}', headers=HEADERS).json()['resource_links']
    assert sorted(item['name'] for item in links if item['type'] == 'deployment') == [
        'Payments on web-1 (dev)',
        'Payments on web-1 (prod)',
    ]
    picker = client.get('/api/documentation/resources', headers=HEADERS).json()['deployments']
    assert [item['name'] for item in picker] == ['Payments on web-1 (dev)', 'Payments on web-1 (prod)']
    built = live(store, 'built', 'Built', service_ids=[service], server_ids=[server], deployment_ids=[on_prod])
    assert message(remove(client, built, 'deployment', on_prod)) == (
        409,
        "This document was built for deployment Payments on web-1 (prod); that link can't be removed.",
    )


def deployed(store):
    """dev, web-1, Payments and Payments on web-1 (dev)."""
    env = resource(store, 'environments', 'dev')
    server = resource(store, 'servers', 'web-1')
    service = resource(store, 'services', 'Payments')
    return env, server, service, deployment(store, service, server, env)


def test_adding_a_deployment_adds_its_missing_service_and_server_in_one_change(store, client):
    _, server, service, run = deployed(store)
    document = live(store)
    assert add(client, document, 'service', service).status_code == 201
    added = add(client, document, 'deployment', run)
    assert added.status_code == 201, added.text
    assert added.json()['revision'] == 3
    assert [row['details'] for row in audits(store, document)][-1] == {
        'links_added': [server, run],
        'links_removed': [],
    }
    assert [(item['type'], item['connection']) for item in added.json()['resources']] == [
        ('server', 'linked'),
        ('service', 'linked'),
        ('deployment', 'linked'),
    ]


def test_a_deployment_name_carries_its_runtime_everywhere(store, client):
    env, server, service, _ = deployed(store)
    with store.connect() as conn:
        worker = conn.execute(
            """INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment_id,runtime_name)
            VALUES (%s,%s,%s,%s,'worker') RETURNING id""",
            (store.project(conn, 'shop'), service, server, env),
        ).fetchone()['id']
    document = live(store, service_ids=[service], server_ids=[server], deployment_ids=[str(worker)])
    expected = 'Payments on web-1 (dev) · worker'
    assert [item[2] for item in resources(client, document) if item[0] == 'deployment'] == [expected]
    picker = client.get('/api/documentation/resources', headers=HEADERS).json()['deployments']
    assert expected in [item['name'] for item in picker]
    snapshot = client.get(f'{DOCUMENTS}/{document}', headers=HEADERS).json()['source_snapshot']
    assert [item['name'] for item in snapshot['deployments']] == [expected]


def test_removing_a_service_or_server_removes_its_linked_deployments(store, client):
    _, server, service, run = deployed(store)
    document = live(store)
    add(client, document, 'deployment', run)
    removed = remove(client, document, 'service', service)
    assert removed.status_code == 200, removed.text
    assert [item['type'] for item in removed.json()['resources']] == ['server']
    assert [row['details'] for row in audits(store, document)][-1] == {
        'links_added': [],
        'links_removed': [service, run],
    }
    add(client, document, 'deployment', run)  # brings the service back
    assert [item['type'] for item in remove(client, document, 'server', server).json()['resources']] == ['service']


def test_removing_a_service_refuses_when_one_of_its_deployments_is_built_for(store, client):
    _, server, service, run = deployed(store)
    document = live(store, service_ids=[service], server_ids=[server], deployment_ids=[run])
    with store.connect() as conn:  # the service and server linked later, the deployment built for
        conn.execute(
            "UPDATE ka.document_resources SET connection='linked' WHERE document_id=%s AND deployment_id IS NULL",
            (document,),
        )
    assert message(remove(client, document, 'service', service)) == (
        409,
        "This document was built for deployment Payments on web-1 (dev); that link can't be removed.",
    )
    assert len(resources(client, document)) == 3 and revision(store, document) == 1


def test_the_snapshot_keeps_an_added_deployment_at_the_next_publish(store, client):
    _, _, _, run = deployed(store)
    document = live(store)
    add(client, document, 'deployment', run)
    draft = client.post('/api/documentation/drafts', headers=HEADERS, json={'document_id': document})
    assert draft.status_code == 201, draft.text
    published = client.post(
        f'/api/documentation/drafts/{draft.json()["id"]}/promote', headers=HEADERS, json={'revision': 1}
    )
    assert published.status_code == 200, published.text
    snapshot = client.get(f'{DOCUMENTS}/{document}', headers=HEADERS).json()['source_snapshot']
    assert [item['id'] for item in snapshot['deployments']] == [run]


def test_a_link_a_draft_holds_is_carried_once_at_publish(store, client):
    server = resource(store, 'servers', 'web-1')
    document = live(store)
    draft = client.post('/api/documentation/drafts', headers=HEADERS, json={'document_id': document}).json()
    with store.connect() as conn:
        add_link(conn, store.project(conn, 'shop'), draft['id'], 'server', server, Provenance('person', None))
    assert add(client, document, 'server', server).status_code == 201
    published = client.post(
        f'/api/documentation/drafts/{draft["id"]}/promote', headers=HEADERS, json={'revision': draft['revision']}
    )
    assert published.status_code == 200, published.text
    assert resources(client, document) == [('server', server, 'web-1', 'linked')]


def test_a_brand_new_draft_and_an_archived_code_project_are_404s(store, client):
    server = resource(store, 'servers', 'web-1')
    code = resource(store, 'repositories', 'Shop')
    with store.connect() as conn:
        conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id=%s', (code,))
    draft = client.post(
        '/api/documentation/drafts', headers=HEADERS, json={'slug': 'new', 'title': 'New', 'markdown': '# New\n'}
    ).json()['id']
    assert message(add(client, draft, 'server', server)) == (404, 'Document not found')
    assert message(remove(client, draft, 'server', server)) == (404, 'Document not found')
    document = live(store)
    assert message(add(client, document, 'code_project', code)) == (404, 'That code project was not found.')
    assert message(remove(client, document, 'server', server)) == (404, 'This document is not linked to that server.')
