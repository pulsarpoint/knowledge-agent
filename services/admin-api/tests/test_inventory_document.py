"""0107: one inventory document per server, service or environment (documents.inventory), the documents of a resource
read from its links, and ka.inventory_documents removed (disposable DB)."""

import threading
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from live_documents import write_live_document
from test_api import _client
from test_repositories import HEADERS

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0107_inventory_document.up.sql', '0107_inventory_document.down.sql'
DOCUMENTS = '/api/documentation/documents'
DRAFTS = '/api/documentation/drafts'
NOT_BUILT_FOR = 'An inventory document must be built for one server, service or environment.'
NO_LINKS = {'code_project_ids': [], 'service_ids': [], 'server_ids': [], 'deployment_ids': [], 'environment_ids': []}


@pytest.fixture
def client(store, tmp_path, monkeypatch):
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(tmp_path / 'missing'))  # exports fail quietly
    return _client(tmp_path, repository_store=store)


def message(response) -> tuple:
    return response.status_code, response.json()['error']['message']


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def resource(store, table: str, name: str) -> str:
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        if table == 'repositories':
            return str(
                conn.execute(
                    'INSERT INTO ka.repositories(project_id,slug,name,remote_url) VALUES (%s,%s,%s,%s) RETURNING id',
                    (project, name.lower(), name, f'https://example.test/{name}.git'),
                ).fetchone()['id']
            )
        if table == 'servers':
            return str(
                conn.execute(
                    'INSERT INTO ka.servers(project_id,name,hostname,enabled) VALUES (%s,%s,%s,false) RETURNING id',
                    (project, name, f'{name}.internal'),
                ).fetchone()['id']
            )
        return str(
            conn.execute(
                f'INSERT INTO ka.{table}(project_id,name) VALUES (%s,%s) RETURNING id', (project, name)
            ).fetchone()['id']
        )


def live(store, slug, title, **links) -> str:
    return write_live_document(store, slug, title, f'# {title}\n', **links)['id']


def new_draft(client, slug, title, *, inventory=False, **ids):
    return client.post(
        DRAFTS,
        headers=HEADERS,
        json={'slug': slug, 'title': title, 'markdown': f'# {title}\n', 'resource_ids': ids, 'inventory': inventory},
    )


def revision(client, document_id) -> int:
    return client.get(f'{DOCUMENTS}/{document_id}', headers=HEADERS).json()['revision']


def mark(client, document_id, inventory=True, at=None):
    return client.put(
        f'{DOCUMENTS}/{document_id}/inventory',
        headers=HEADERS,
        json={'inventory': inventory, 'revision': at if at is not None else revision(client, document_id)},
    )


def flag(conn, document_id) -> None:
    conn.execute('UPDATE ka.documents SET inventory=true WHERE id=%s', (document_id,))


# Migration


def test_0107_adds_the_flag_drops_attachments_and_round_trips(store):
    server = resource(store, 'servers', 'prod-1')
    runbook = live(store, 'runbook', 'Runbook', server_ids=[server])
    with store.connect() as conn:
        assert conn.execute("SELECT to_regclass('ka.inventory_documents') AS t").fetchone()['t'] is None
        flag(conn, runbook)
        conn.commit()
        assert conn.execute('SELECT inventory FROM ka.document_details WHERE id=%s', (runbook,)).fetchone()['inventory']
        run(conn, DOWN)
        assert conn.execute("SELECT to_regclass('ka.inventory_documents') AS t").fetchone()['t'] is not None
        assert conn.execute('SELECT count(*) AS n FROM ka.inventory_documents').fetchone()['n'] == 0
        columns = {
            row['column_name']
            for row in conn.execute(
                """SELECT column_name FROM information_schema.columns WHERE table_schema='ka'
                AND table_name IN ('documents','document_details','document_drafts_view','live_documents')"""
            )
        }
        assert 'inventory' not in columns
        # 0104's live-document trigger is back on the table
        draft = conn.execute(
            """INSERT INTO ka.documents(project_id,slug,revision,is_draft,title,description,purpose,audience,tags,
            markdown,content_hash,origin) SELECT project_id,'x',1,true,'X','','explanation','both','{}','# X',
            encode(sha256(convert_to('# X','UTF8')),'hex'),'manual' FROM ka.documents WHERE id=%s RETURNING id,project_id""",
            (runbook,),
        ).fetchone()
        with pytest.raises(psycopg.errors.RaiseException, match='A draft is not a document yet'):
            with conn.transaction():
                conn.execute(
                    'INSERT INTO ka.inventory_documents(project_id,server_id,document_id) VALUES (%s,%s,%s)',
                    (draft['project_id'], server, draft['id']),
                )
        conn.execute('DELETE FROM ka.documents WHERE id=%s', (draft['id'],))
        conn.commit()
        run(conn, UP)
        assert conn.execute("SELECT to_regclass('ka.inventory_documents') AS t").fetchone()['t'] is None
        assert not conn.execute('SELECT inventory FROM ka.documents WHERE id=%s', (runbook,)).fetchone()['inventory']


# Constraint (database)


def test_the_flag_needs_exactly_one_built_for_server_service_or_environment(store):
    server, other = resource(store, 'servers', 'prod-1'), resource(store, 'servers', 'prod-2')
    code = resource(store, 'repositories', 'Shop')
    unlinked = live(store, 'unlinked', 'Unlinked')
    of_code = live(store, 'code', 'Code', code_project_ids=[code])
    two = live(store, 'two', 'Two', server_ids=[server, other])
    for document_id in (unlinked, of_code, two):
        with store.connect() as conn:
            flag(conn, document_id)
            with pytest.raises(psycopg.errors.RaiseException, match=NOT_BUILT_FOR):
                conn.commit()
    linked_only = live(store, 'linked', 'Linked')
    with store.connect() as conn:
        conn.execute(
            """INSERT INTO ka.document_resources(project_id,document_id,server_id,connection,added_how)
            SELECT project_id,id,%s,'linked','person' FROM ka.documents WHERE id=%s""",
            (server, linked_only),
        )
        flag(conn, linked_only)
        with pytest.raises(psycopg.errors.RaiseException, match=NOT_BUILT_FOR):
            conn.commit()
    one = live(store, 'one', 'One', server_ids=[server])
    with store.connect() as conn:
        flag(conn, one)
    with store.connect() as conn:  # a second built-for link later is refused too
        conn.execute(
            """INSERT INTO ka.document_resources(project_id,document_id,server_id,connection,added_how)
            SELECT project_id,id,%s,'built_for','person' FROM ka.documents WHERE id=%s""",
            (other, one),
        )
        with pytest.raises(psycopg.errors.RaiseException, match=NOT_BUILT_FOR):
            conn.commit()


@pytest.mark.parametrize(
    'table,kind,label',
    [
        ('servers', 'server_ids', 'Server'),
        ('services', 'service_ids', 'Service'),
        ('environments', 'environment_ids', 'Environment'),
    ],
)
def test_a_second_inventory_document_for_a_resource_is_refused(store, client, table, kind, label):
    entry = resource(store, table, 'main')
    first = live(store, 'first', 'First', **{kind: [entry]})
    second = live(store, 'second', 'Second', **{kind: [entry]})
    with store.connect() as conn:
        flag(conn, first)
    with store.connect() as conn:
        flag(conn, second)
        with pytest.raises(psycopg.errors.RaiseException, match=f'{label} main already has an inventory document.'):
            conn.commit()
    brand_new = new_draft(client, 'third', 'Third', **{kind: [entry]})
    assert brand_new.status_code == 201, brand_new.text
    with store.connect() as conn:
        flag(conn, brand_new.json()['id'])
        with pytest.raises(psycopg.errors.RaiseException, match=f'{label} main already has an inventory document.'):
            conn.commit()


def test_a_brand_new_inventory_draft_blocks_a_live_one_and_a_draft_of_the_live_one_is_fine(store, client):
    server = resource(store, 'servers', 'prod-1')
    draft = new_draft(client, 'overview', 'Overview', inventory=True, server_ids=[server])
    assert draft.status_code == 201, draft.text
    assert draft.json()['inventory'] is True
    runbook = live(store, 'runbook', 'Runbook', server_ids=[server])
    assert message(mark(client, runbook)) == (409, 'Server prod-1 already has an inventory document.')
    discarded = client.post(f'{DRAFTS}/{draft.json()["id"]}/discard', headers=HEADERS, json={'revision': 1})
    assert discarded.status_code == 200, discarded.text
    assert mark(client, runbook).status_code == 200
    of_live = client.post(DRAFTS, headers=HEADERS, json={'document_id': runbook})
    assert of_live.status_code == 201, of_live.text
    assert of_live.json()['inventory'] is True  # reports its document's flag
    with store.connect() as conn:
        assert not conn.execute('SELECT inventory FROM ka.documents WHERE id=%s', (of_live.json()['id'],)).fetchone()[
            'inventory'
        ]
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            flag(conn, of_live.json()['id'])
    promoted = client.post(f'{DRAFTS}/{of_live.json()["id"]}/promote', headers=HEADERS, json={'revision': 1})
    assert promoted.status_code == 200, promoted.text
    assert client.get(f'{DOCUMENTS}/{runbook}', headers=HEADERS).json()['inventory'] is True


def test_two_concurrent_transactions_add_only_one_inventory_document(store):
    """Review Focus 1: the second waits for the first and is refused, never two flagged documents."""
    server = resource(store, 'servers', 'prod-1')
    first = live(store, 'first', 'First', server_ids=[server])
    second = live(store, 'second', 'Second', server_ids=[server])
    outcome = []
    with store.connect() as one:
        flag(one, first)
        one.execute('SET CONSTRAINTS ka.documents_inventory IMMEDIATE')  # checked now: holds the resource's lock

        def other():
            try:
                with store.connect() as two:
                    flag(two, second)
            except psycopg.errors.RaiseException as error:
                outcome.append(str(error))
            else:
                outcome.append('committed')

        worker = threading.Thread(target=other)
        worker.start()
        worker.join(1)
        assert worker.is_alive()  # waiting on the first transaction
    worker.join(10)
    assert outcome and outcome[0].startswith('Server prod-1 already has an inventory document.')
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.documents WHERE inventory').fetchone()['n'] == 1


def test_a_concurrent_mark_waits_and_gets_the_message(store, client):
    server = resource(store, 'servers', 'prod-1')
    first = live(store, 'first', 'First', server_ids=[server])
    second = live(store, 'second', 'Second', server_ids=[server])
    responses = []
    with store.connect() as one:
        flag(one, first)
        one.execute('SET CONSTRAINTS ka.documents_inventory IMMEDIATE')
        worker = threading.Thread(target=lambda: responses.append(mark(client, second, at=1)))
        worker.start()
        worker.join(1)
        assert worker.is_alive()
    worker.join(10)
    assert message(responses[0]) == (409, 'Server prod-1 already has an inventory document.')


# Mark / unmark


def test_mark_and_unmark_keep_the_built_for_link(store, client):
    """Review Focus 4: unmarking keeps the built-for link; removing it stays refused."""
    service = resource(store, 'services', 'payments')
    runbook = live(store, 'runbook', 'Runbook', service_ids=[service])
    marked = mark(client, runbook)
    assert marked.status_code == 200, marked.text
    assert marked.json() == {'id': runbook, 'revision': 2, 'inventory': True}
    unmarked = mark(client, runbook, False)
    assert unmarked.json() == {'id': runbook, 'revision': 3, 'inventory': False}
    document = client.get(f'{DOCUMENTS}/{runbook}', headers=HEADERS).json()
    assert document['inventory'] is False
    assert [(link['type'], link['connection']) for link in document['resource_links']] == [('service', 'built_for')]
    refused = client.put(f'{DOCUMENTS}/{runbook}/links', headers=HEADERS, json={**NO_LINKS, 'revision': 3})
    assert message(refused) == (409, "This document was built for service payments; that link can't be removed.")
    with store.connect() as conn:
        actions = [
            row['action']
            for row in conn.execute(
                "SELECT action FROM ka.audit_events WHERE action LIKE 'document.inventory.%' ORDER BY created_at,id"
            )
        ]
    assert actions == ['document.inventory.marked', 'document.inventory.unmarked']


def test_mark_errors(store, client):
    server = resource(store, 'servers', 'prod-1')
    code = resource(store, 'repositories', 'Shop')
    unlinked = live(store, 'unlinked', 'Unlinked')
    of_code = live(store, 'code', 'Code', code_project_ids=[code])
    for document_id in (unlinked, of_code):
        assert message(mark(client, document_id)) == (409, NOT_BUILT_FOR)
    runbook = live(store, 'runbook', 'Runbook', server_ids=[server])
    assert message(mark(client, runbook, at=7)) == (
        409,
        'This document changed. Reload it before saving; your text has not been overwritten.',
    )
    assert mark(client, str(uuid4()), at=1).status_code == 404
    of_live = client.post(DRAFTS, headers=HEADERS, json={'document_id': runbook}).json()['id']
    assert mark(client, of_live, at=1).status_code == 404  # a draft of a document uses its document's flag
    brand_new = new_draft(client, 'notes', 'Notes', server_ids=[server]).json()['id']
    assert mark(client, brand_new, at=1).json() == {'id': brand_new, 'revision': 2, 'inventory': True}
    assert message(mark(client, runbook)) == (409, 'Server prod-1 already has an inventory document.')


def test_a_person_creates_an_inventory_draft_and_publishing_keeps_the_flag(store, client):
    environment = resource(store, 'environments', 'production')
    draft = new_draft(client, 'production', 'Production', inventory=True, environment_ids=[environment])
    assert draft.status_code == 201, draft.text
    again = new_draft(client, 'production-2', 'Production 2', inventory=True, environment_ids=[environment])
    assert message(again) == (409, 'Environment production already has an inventory document.')
    assert message(new_draft(client, 'loose', 'Loose', inventory=True)) == (409, NOT_BUILT_FOR)
    refused = client.post(DRAFTS, headers=HEADERS, json={'document_id': draft.json()['id'], 'inventory': True})
    assert refused.status_code == 400
    published = client.post(f'{DRAFTS}/{draft.json()["id"]}/promote', headers=HEADERS, json={'revision': 1})
    assert published.status_code == 200, published.text
    assert client.get(f'{DOCUMENTS}/{draft.json()["id"]}', headers=HEADERS).json()['inventory'] is True


# Readers


@pytest.mark.parametrize(
    'table,kind,entry',
    [
        ('servers', 'server_ids', 'server'),
        ('services', 'service_ids', 'service'),
        ('environments', 'environment_ids', 'environment'),
    ],
)
def test_resource_documents_are_the_inventory_document_and_the_other_linked_ones(store, client, table, kind, entry):
    target, elsewhere = resource(store, table, 'main'), resource(store, table, 'other')
    overview = live(store, 'overview', 'Overview', **{kind: [target]})
    built = live(store, 'built', 'Built', **{kind: [target]})
    linked = live(store, 'linked', 'Linked')
    live(store, 'unrelated', 'Unrelated', **{kind: [elsewhere]})
    new_draft(client, 'draft', 'Draft', **{kind: [target]})  # drafts are not shown
    assert client.put(f'{DOCUMENTS}/{linked}/links', headers=HEADERS, json={**NO_LINKS, kind: [target], 'revision': 1})
    url = f'/api/inventory/{entry}/{target}/documents'
    assert client.get(url, headers=HEADERS).json() == {
        'inventory_document': None,
        'documents': [
            {'id': built, 'title': 'Built', 'connection': 'built_for'},
            {'id': linked, 'title': 'Linked', 'connection': 'linked'},
            {'id': overview, 'title': 'Overview', 'connection': 'built_for'},
        ],
    }
    assert mark(client, overview).status_code == 200
    assert client.get(url, headers=HEADERS).json() == {
        'inventory_document': {'id': overview, 'title': 'Overview', 'markdown': '# Overview\n', 'version': 1},
        'documents': [
            {'id': built, 'title': 'Built', 'connection': 'built_for'},
            {'id': linked, 'title': 'Linked', 'connection': 'linked'},
        ],
    }
    assert client.get(f'/api/inventory/{entry}/{uuid4()}/documents', headers=HEADERS).status_code == 404


def test_code_project_documents_and_the_old_attachments_route_is_gone(store, client):
    code = resource(store, 'repositories', 'Shop')
    readme = live(store, 'readme', 'Readme', code_project_ids=[code])
    assert client.get(f'/api/inventory/code_project/{code}/documents', headers=HEADERS).json() == {
        'inventory_document': None,
        'documents': [{'id': readme, 'title': 'Readme', 'connection': 'built_for'}],
    }
    params = {'entry_kind': 'code_project', 'entry_id': code}
    assert client.get('/api/inventory/documents', headers=HEADERS, params=params).status_code in (404, 405)


def test_link_and_unlink_a_document_from_an_entry(store, client):
    server = resource(store, 'servers', 'prod-1')
    notes = live(store, 'notes', 'Notes')
    built = live(store, 'built', 'Built', server_ids=[server])
    url = f'/api/inventory/server/{server}/documents'
    added = client.post(url, headers=HEADERS, json={'document_id': notes})
    assert added.status_code == 201, added.text
    assert {'id': notes, 'title': 'Notes', 'connection': 'linked'} in added.json()['documents']
    assert message(client.post(url, headers=HEADERS, json={'document_id': notes})) == (
        409,
        'This document is already linked.',
    )
    document = client.get(f'{DOCUMENTS}/{notes}', headers=HEADERS).json()
    assert document['revision'] == 2
    assert [(link['id'], link['connection'], link['added_how']) for link in document['resource_links']] == [
        (server, 'linked', 'person')
    ]
    removed = client.delete(f'{url}/{notes}', headers=HEADERS)
    assert removed.status_code == 200, removed.text
    assert removed.json()['documents'] == [{'id': built, 'title': 'Built', 'connection': 'built_for'}]
    history = client.get(f'{DOCUMENTS}/{notes}/resource-history', headers=HEADERS).json()['history']
    assert [row['removed_at'] is not None for row in history] == [True]
    assert message(client.delete(f'{url}/{notes}', headers=HEADERS)) == (404, 'This document is not linked.')
    assert message(client.delete(f'{url}/{built}', headers=HEADERS)) == (
        409,
        "This document was built for server prod-1; that link can't be removed.",
    )
    assert message(client.post(url, headers=HEADERS, json={'document_id': str(uuid4())})) == (404, 'Document not found')
    brand_new = new_draft(client, 'draft', 'Draft').json()['id']
    assert client.post(url, headers=HEADERS, json={'document_id': brand_new}).status_code == 404
    assert (
        client.post(
            f'/api/inventory/server/{uuid4()}/documents', headers=HEADERS, json={'document_id': notes}
        ).status_code
        == 404
    )


def test_the_system_map_shows_the_documents_linked_to_each_entry(store, client):
    server = client.post('/api/servers', headers=HEADERS, json={'name': 'prod-1', 'hostname': 'one.internal'}).json()
    runbook = live(store, 'runbook', 'Runbook', server_ids=[server['id']])
    notes = live(store, 'notes', 'Notes')
    client.post(f'/api/inventory/server/{server["id"]}/documents', headers=HEADERS, json={'document_id': notes})
    new_draft(client, 'draft', 'Draft', server_ids=[server['id']])
    view = client.get('/api/map/view/infrastructure', headers=HEADERS, params={'depth': 3}).json()
    [node] = [n for n in view['nodes'] if n['entry_id'] == server['id']]
    assert node['documents'] == [
        {'id': notes, 'slug': 'notes', 'title': 'Notes'},
        {'id': runbook, 'slug': 'runbook', 'title': 'Runbook'},
    ]


def test_documents_list_has_the_flag_and_filters_inventory_documents(store, client):
    server = resource(store, 'servers', 'prod-1')
    overview = live(store, 'overview', 'Overview', server_ids=[server])
    live(store, 'other', 'Other', server_ids=[server])
    mark(client, overview)
    listed = client.get(DOCUMENTS, headers=HEADERS).json()
    assert {row['title']: row['inventory'] for row in listed['documents']} == {'Overview': True, 'Other': False}
    only = client.get(DOCUMENTS, headers=HEADERS, params={'inventory': 'true'}).json()
    assert [row['id'] for row in only['documents']] == [overview] and only['total'] == 1
    brand_new = new_draft(client, 'draft', 'Draft').json()['id']
    assert client.get(f'{DOCUMENTS}/{brand_new}', headers=HEADERS).json()['inventory'] is False
    drafts = client.get(DRAFTS, headers=HEADERS).json()['drafts']
    assert [row['inventory'] for row in drafts] == [False]
