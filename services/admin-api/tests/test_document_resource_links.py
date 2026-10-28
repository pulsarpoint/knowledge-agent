"""A document's resource links from the document side: the Connections answer lists its active links (names per type,
removed ones left out), and one link is added (POST /documents/{id}/resources) or removed (DELETE
/documents/{id}/resources/{type}/{resource_id}) with a revision bump and an audit entry (disposable DB)."""

from uuid import uuid4

import pytest
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
        ('deployment', 'Payments on web-1', 'built_for'),
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
    assert resources(client, document) == [
        ('code_project', code, 'Shop', 'linked'),
        ('deployment', run, 'Payments on web-1', 'linked'),
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
