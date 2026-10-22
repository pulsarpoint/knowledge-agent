"""0107: one inventory document per server, service or environment (documents.inventory, replaced by the tag
`inventory` in 0109: its database checks are tested at 0108, after 0109's down), the documents of a resource read from
their links, and ka.inventory_documents removed (disposable DB)."""

import threading
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi import HTTPException
from knowledge_admin.documents.store import Provenance, create_draft

from live_documents import write_live_document
from test_api import _client
from test_repositories import HEADERS

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0107_inventory_document.up.sql', '0107_inventory_document.down.sql'
TAGS_UP, TAGS_DOWN = '0109_tags_not_flags.up.sql', '0109_tags_not_flags.down.sql'
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


def new_draft(client, slug, title, *, tags=(), **ids):
    return client.post(
        DRAFTS,
        headers=HEADERS,
        json={'slug': slug, 'title': title, 'markdown': f'# {title}\n', 'resource_ids': ids, 'tags': list(tags)},
    )


def brief(answer: dict) -> dict:
    """A resource documents answer with each listed document as {id, title, connection} (and tags when it has some)."""
    return {
        'inventory_document': answer['inventory_document'],
        'documents': [
            {'id': item['id'], 'title': item['title'], 'connection': item['connection']}
            | ({'tags': item['tags']} if item['tags'] else {})
            for item in answer['documents']
        ],
    }


def tag(store, document_id) -> None:
    with store.connect() as conn:
        conn.execute("UPDATE ka.documents SET tags='{inventory}' WHERE id=%s", (document_id,))


def flag(conn, document_id) -> None:
    conn.execute('UPDATE ka.documents SET inventory=true WHERE id=%s', (document_id,))


@pytest.fixture
def at_0108(store):
    """The schema before 0109: documents.inventory and its checks."""
    with store.connect() as conn:
        run(conn, '0126_inventory_drop_old.down.sql')  # 0126 drops service_assets, which 0109 changes
        run(conn, TAGS_DOWN)
    return store


# Migration


def test_0107_adds_the_flag_drops_attachments_and_round_trips(at_0108):
    store = at_0108
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
        conn.commit()
        run(conn, TAGS_UP)


# Constraint (database, at 0108)


def test_the_flag_needs_exactly_one_built_for_server_service_or_environment(at_0108):
    store = at_0108
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
def test_a_second_inventory_document_for_a_resource_is_refused(at_0108, client, table, kind, label):
    store = at_0108
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


def test_two_concurrent_transactions_add_only_one_inventory_document(at_0108):
    store = at_0108
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


# Drafts


def test_a_new_inventory_draft_never_becomes_a_draft_of_the_live_document_its_name_names(store):
    server = resource(store, 'servers', 'prod-1')
    named = live(store, 'runbook', 'Runbook')
    content = {
        'title': 'Runbook',
        'description': '',
        'purpose': 'explanation',
        'audience': 'both',
        'tags': ['inventory'],
        'markdown': '# Runbook\n',
    }
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        with pytest.raises(HTTPException) as refused:
            create_draft(
                conn,
                project,
                Provenance('person', None),
                content,
                origin='manual',
                slug='runbook',
                links={'server_ids': [server]},
            )
        assert (refused.value.status_code, refused.value.detail) == (
            409,
            'A document or draft named runbook already exists in this folder. Choose another name.',
        )
        conn.rollback()
        assert conn.execute('SELECT count(*) AS n FROM ka.documents WHERE draft_of=%s', (named,)).fetchone()['n'] == 0


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
    links = client.put(f'{DOCUMENTS}/{linked}/links', headers=HEADERS, json={**NO_LINKS, kind: [target], 'revision': 1})
    assert links.status_code == 200, links.text
    url = f'/api/inventory/{entry}/{target}/documents'
    assert brief(client.get(url, headers=HEADERS).json()) == {
        'inventory_document': None,
        'documents': [
            {'id': built, 'title': 'Built', 'connection': 'built_for'},
            {'id': linked, 'title': 'Linked', 'connection': 'linked'},
            {'id': overview, 'title': 'Overview', 'connection': 'built_for'},
        ],
    }
    tag(store, overview)
    assert brief(client.get(url, headers=HEADERS).json()) == {
        'inventory_document': {
            'id': overview,
            'title': 'Overview',
            'markdown': '# Overview\n',
            'version': 1,
            'is_draft': False,
            'published': True,
        },
        'documents': [
            {'id': overview, 'title': 'Overview', 'connection': 'built_for', 'tags': ['inventory']},
            {'id': built, 'title': 'Built', 'connection': 'built_for'},
            {'id': linked, 'title': 'Linked', 'connection': 'linked'},
        ],
    }
    assert client.get(f'/api/inventory/{entry}/{uuid4()}/documents', headers=HEADERS).status_code == 404


def test_code_project_documents_and_the_old_attachments_route_is_gone(store, client):
    code = resource(store, 'repositories', 'Shop')
    readme = live(store, 'readme', 'Readme', code_project_ids=[code])
    assert brief(client.get(f'/api/inventory/code_project/{code}/documents', headers=HEADERS).json()) == {
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
    assert {'id': notes, 'title': 'Notes', 'connection': 'linked'} in brief(added.json())['documents']
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
    assert brief(removed.json())['documents'] == [{'id': built, 'title': 'Built', 'connection': 'built_for'}]
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


def test_a_brand_new_inventory_draft_is_the_resources_inventory_document_unpublished(store, client):
    service = resource(store, 'services', 'payments')
    draft = new_draft(client, 'payments', 'Payments', tags=['inventory'], service_ids=[service]).json()['id']
    url = f'/api/inventory/service/{service}/documents'
    assert client.get(url, headers=HEADERS).json() == {
        'inventory_document': {
            'id': draft,
            'title': 'Payments',
            'markdown': '# Payments\n',
            'version': None,
            'is_draft': True,
            'published': False,
        },
        'documents': [],  # a draft is not among the other documents
    }
    assert client.post(f'{DRAFTS}/{draft}/promote', headers=HEADERS, json={'revision': 1}).status_code == 200
    found = client.get(url, headers=HEADERS).json()['inventory_document']
    assert (found['id'], found['version'], found['is_draft'], found['published']) == (draft, 1, False, True)


def test_deleting_the_resource_leaves_its_inventory_document_with_its_tag_and_no_links(store, client):
    created = client.post('/api/services', headers=HEADERS, json={'name': 'payments'}).json()
    runbook = live(store, 'runbook', 'Runbook', service_ids=[created['id']], tags=['inventory'])
    deleted = client.delete(f'/api/services/{created["id"]}', headers=HEADERS, params={'revision': created['revision']})
    assert deleted.status_code == 200, deleted.text
    document = client.get(f'{DOCUMENTS}/{runbook}', headers=HEADERS).json()
    assert (document['tags'], document['resource_links']) == (['inventory'], [])
    again = resource(store, 'services', 'payments')
    overview = live(store, 'overview', 'Overview', service_ids=[again], tags=['inventory'])
    found = client.get(f'/api/inventory/service/{again}/documents', headers=HEADERS).json()
    assert found['inventory_document']['id'] == overview


def test_nothing_reads_or_writes_the_old_attachments_table():
    """Only the migrations and the tests of the migrations that had the table name ka.inventory_documents."""
    root = Path(__file__).resolve().parents[3]
    allowed = {
        'services/admin-api/tests/test_inventory_document.py',
        'services/admin-api/tests/test_document_model_migration.py',
        'services/admin-api/tests/test_inventory_links_migration.py',
    }
    found = []
    for folder in ('services', 'packages', 'docs/agents.md', 'docs/documentation.md'):
        base = root / folder
        files = [base] if base.is_file() else base.rglob('*') if base.is_dir() else []
        for path in files:
            if (
                not path.is_file()
                or 'node_modules' in path.parts
                or path.suffix not in {'.py', '.ts', '.tsx', '.md', '.sql', '.txt', '.json', '.yaml', '.yml'}
            ):
                continue
            name = str(path.relative_to(root))
            if name in allowed:
                continue
            text = path.read_text(errors='ignore')
            if (
                'ka.inventory_documents' in text
                or 'FROM inventory_documents' in text
                or ' inventory_documents ' in text
            ):
                found.append(name)
    assert found == []
