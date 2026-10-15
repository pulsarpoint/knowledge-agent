"""0109: tags instead of special columns. The inventory document of a server, service or environment is the document
tagged `inventory` built for it (the most recently updated live one, else a brand-new draft); services.operations_notes
and documents.inventory are gone; documents and drafts filter by one tag (disposable DB)."""

import asyncio
import re
from pathlib import Path

import psycopg
import pytest
from discovery_world import world  # noqa: F401  (fixture)
from knowledge_admin.documents.store import Provenance, create_draft
from knowledge_admin.tools.catalog import CATALOG
from live_documents import live_document, write_live_document
from pydantic import ValidationError
from test_api import _client
from test_discovery_runs import config_of, ssh_ready, start
from test_get_resource import get, given
from test_repositories import HEADERS

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / 'db/migrations'
UP, DOWN = '0109_tags_not_flags.up.sql', '0109_tags_not_flags.down.sql'
DOCUMENTS = '/api/documentation/documents'
DRAFTS = '/api/documentation/drafts'
INVENTORY_OBJECTS = (
    'inventory_document_check',
    'documents_inventory_check',
    'document_resources_inventory_check',
    'document_resources_inventory_gone',
)


@pytest.fixture
def client(store, tmp_path, monkeypatch):
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(tmp_path / 'missing'))  # exports fail quietly
    return _client(tmp_path, repository_store=store)


def resource(store, table: str, name: str) -> str:
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        if table == 'repositories':
            row = conn.execute(
                'INSERT INTO ka.repositories(project_id,slug,name,remote_url) VALUES (%s,%s,%s,%s) RETURNING id',
                (project, name.lower(), name, f'https://example.test/{name}.git'),
            ).fetchone()
        elif table == 'servers':
            row = conn.execute(
                'INSERT INTO ka.servers(project_id,name,hostname,enabled) VALUES (%s,%s,%s,false) RETURNING id',
                (project, name, f'{name}.internal'),
            ).fetchone()
        else:
            row = conn.execute(
                f'INSERT INTO ka.{table}(project_id,name) VALUES (%s,%s) RETURNING id', (project, name)
            ).fetchone()
        return str(row['id'])


def live(store, slug, title, **fields) -> str:
    return write_live_document(store, slug, title, f'# {title}\n', **fields)['id']


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def columns(conn, *tables) -> set[str]:
    return {
        row['column_name']
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='ka' AND table_name=ANY(%s)",
            (list(tables),),
        )
    }


def inventory_objects(conn) -> list[str]:
    triggers = conn.execute(
        "SELECT tgname AS name FROM pg_trigger WHERE NOT tgisinternal AND tgname LIKE '%%inventory%%'"
    ).fetchall()
    functions = conn.execute(
        'SELECT proname AS name FROM pg_proc WHERE proname=ANY(%s)', (list(INVENTORY_OBJECTS),)
    ).fetchall()
    return sorted(row['name'] for row in [*triggers, *functions])


def tags_of(conn, document_id) -> list[str]:
    return conn.execute('SELECT tags FROM ka.documents WHERE id=%s', (document_id,)).fetchone()['tags']


def documents_of(client, kind, resource_id) -> dict:
    response = client.get(f'/api/inventory/{kind}/{resource_id}/documents', headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def promote_with(client, document_id, **content) -> None:
    """Edit a live document as a person does: a draft of it with `content`, then Promote."""
    draft = client.post(DRAFTS, headers=HEADERS, json={'document_id': document_id})
    assert draft.status_code == 201, draft.text
    current = draft.json()
    body = {key: current[key] for key in ('title', 'description', 'purpose', 'audience', 'tags', 'markdown')}
    saved = client.put(
        f'{DRAFTS}/{current["id"]}', headers=HEADERS, json={**body, **content, 'revision': current['revision']}
    )
    assert saved.status_code == 200, saved.text
    promoted = client.post(
        f'{DRAFTS}/{current["id"]}/promote', headers=HEADERS, json={'revision': saved.json()['revision']}
    )
    assert promoted.status_code == 200, promoted.text


# Migration


def test_0109_turns_the_flag_into_the_tag_and_drops_both_columns(store):
    server, service = resource(store, 'servers', 'prod-1'), resource(store, 'services', 'payments')
    overview = live(store, 'overview', 'Overview', server_ids=[server])
    other = live(store, 'other', 'Other', server_ids=[server])
    payments = live(store, 'payments', 'Payments', service_ids=[service])
    with store.connect() as conn:
        assert 'inventory' not in columns(conn, 'documents', 'document_details', 'document_drafts_view')
        assert 'operations_notes' not in columns(conn, 'services', 'service_assets')
        assert inventory_objects(conn) == []
        conn.execute("UPDATE ka.documents SET tags='{inventory}' WHERE id=%s", (payments,))
        conn.commit()
        run(conn, DOWN)
        assert 'inventory' in columns(conn, 'documents', 'document_details', 'document_drafts_view')
        assert 'operations_notes' in columns(conn, 'services', 'service_assets')
        assert len(inventory_objects(conn)) == 7  # 3 triggers, 4 functions
        flags = {
            row['id']: row['inventory']
            for row in conn.execute('SELECT id,inventory FROM ka.documents WHERE NOT is_draft')
        }
        assert {str(key): value for key, value in flags.items()} == {overview: False, other: False, payments: True}
        conn.commit()
        conn.execute("UPDATE ka.documents SET inventory=true,tags='{ops}' WHERE id=%s", (overview,))
        conn.execute("UPDATE ka.services SET operations_notes='Restart nightly.' WHERE id=%s", (service,))
        conn.commit()
        project = store.project(conn, 'shop')
        content = {'title': 'Overview', 'description': '', 'purpose': 'explanation', 'audience': 'both'}
        draft = create_draft(
            conn,
            project,
            Provenance('person', None),
            {**content, 'tags': ['ops'], 'markdown': '# Overview\n\nMore.'},
            origin='manual',
            document_id=overview,
        )
        conn.commit()
        before = conn.execute('SELECT updated_at FROM ka.documents WHERE id=%s', (overview,)).fetchone()
        run(conn, UP)
        conn.commit()
        assert tags_of(conn, overview) == ['ops', 'inventory']
        assert tags_of(conn, draft) == ['ops', 'inventory']  # publishing the draft keeps the tag
        assert tags_of(conn, payments) == ['inventory']  # never twice
        assert tags_of(conn, other) == []
        assert conn.execute('SELECT updated_at FROM ka.documents WHERE id=%s', (overview,)).fetchone() == before
        assert 'inventory' not in columns(conn, 'documents', 'document_details', 'document_drafts_view')
        assert 'operations_notes' not in columns(conn, 'services', 'service_assets')
        assert inventory_objects(conn) == []
        assert conn.execute('SELECT name FROM ka.service_assets WHERE id=%s', (service,)).fetchone()['name'] == (
            'payments'
        )


def test_0109_down_refuses_when_two_documents_tagged_inventory_are_built_for_one_resource(store):
    server = resource(store, 'servers', 'prod-1')
    live(store, 'first', 'First', server_ids=[server], tags=['inventory'])
    live(store, 'second', 'Second', server_ids=[server], tags=['inventory'])
    with store.connect() as conn:
        with pytest.raises(psycopg.errors.RaiseException, match='would be lost'):
            run(conn, DOWN)
        conn.rollback()
        conn.execute("UPDATE ka.documents SET tags='{}' WHERE slug='second'")
        conn.commit()
        run(conn, DOWN)
        conn.commit()
        flagged = conn.execute('SELECT slug FROM ka.documents WHERE inventory').fetchall()
        assert [row['slug'] for row in flagged] == ['first']
        run(conn, UP)


def test_the_mark_route_is_gone_and_lists_have_no_inventory_field(store, client):
    server = resource(store, 'servers', 'prod-1')
    overview = live(store, 'overview', 'Overview', server_ids=[server], tags=['inventory'])
    marked = client.put(f'{DOCUMENTS}/{overview}/inventory', headers=HEADERS, json={'inventory': True, 'revision': 1})
    assert marked.status_code in (404, 405)
    assert 'inventory' not in client.get(f'{DOCUMENTS}/{overview}', headers=HEADERS).json()
    [row] = client.get(DOCUMENTS, headers=HEADERS).json()['documents']
    assert 'inventory' not in row and row['tags'] == ['inventory']
    [item] = documents_of(client, 'server', server)['documents']
    assert 'inventory' not in item and item['tags'] == ['inventory']
    new = client.post(
        DRAFTS,
        headers=HEADERS,
        json={'slug': 'notes', 'title': 'Notes', 'markdown': '# Notes\n', 'inventory': True},
    )
    assert new.status_code == 400  # a new inventory document is a draft tagged inventory
    draft = client.post(DRAFTS, headers=HEADERS, json={'slug': 'notes', 'title': 'Notes', 'markdown': '# Notes\n'})
    assert 'inventory' not in draft.json()
    assert 'inventory' not in client.get(DRAFTS, headers=HEADERS).json()['drafts'][0]


# The inventory document: the document tagged inventory built for the resource


def test_the_most_recently_updated_live_document_tagged_inventory_is_the_inventory_document(store, client):
    """Review Focus 2: with two, the newest is the page's inventory document and the other is listed."""
    server = resource(store, 'servers', 'prod-1')
    first = live(store, 'first', 'First', server_ids=[server], tags=['inventory'])
    second = live(store, 'second', 'Second', server_ids=[server], tags=['inventory'])
    plain = live(store, 'plain', 'Plain', server_ids=[server])
    found = documents_of(client, 'server', server)
    assert (found['inventory_document']['id'], found['inventory_document']['published']) == (second, True)
    assert [item['id'] for item in found['documents']] == [second, first, plain]
    promote_with(client, first, markdown='# First\n\nEdited.')
    found = documents_of(client, 'server', server)
    assert found['inventory_document']['id'] == first
    assert [item['id'] for item in found['documents']] == [first, plain, second]
    promote_with(client, first, tags=[])  # the tag removed: the other one is the inventory document again
    found = documents_of(client, 'server', server)
    assert found['inventory_document']['id'] == second
    assert [item['id'] for item in found['documents']] == [second, first, plain]


def test_get_resource_picks_the_same_inventory_document_as_the_page(world):
    server = world.server['id']
    config = given(world)
    with world.store.connect() as conn:
        older = live_document(conn, world.project, 'older', 'Older', '# Older', server_ids=[server], tags=['inventory'])
    with world.store.connect() as conn:
        newer = live_document(conn, world.project, 'newer', 'Newer', '# Newer', server_ids=[server], tags=['inventory'])
    found = get(config, 'server', server)
    assert found['inventory_document'] == {'id': str(newer['id']), 'title': 'Newer'}
    own = [item for item in found['documents'] if item['resource']['id'] == server]
    assert [item['title'] for item in own] == ['Newer', 'Older', 'web-1 overview']
    assert all('inventory' not in item for item in found['documents'])
    assert own[1]['tags'] == ['inventory']
    page = documents_of(world.client, 'server', server)
    assert page['inventory_document']['id'] == str(newer['id'])
    assert [item['id'] for item in page['documents'] if item['resource']['id'] == server] == [
        item['id'] for item in own
    ]
    assert older['id'] != newer['id']


def test_a_brand_new_draft_tagged_inventory_is_shown_when_there_is_no_live_one(store, client):
    service = resource(store, 'services', 'payments')
    draft = client.post(
        DRAFTS,
        headers=HEADERS,
        json={
            'slug': 'payments',
            'title': 'Payments',
            'markdown': '# Payments\n',
            'tags': ['inventory'],
            'resource_ids': {'service_ids': [service]},
        },
    )
    assert draft.status_code == 201, draft.text
    assert draft.json()['tags'] == ['inventory']
    found = documents_of(client, 'service', service)
    assert found['inventory_document'] == {
        'id': draft.json()['id'],
        'title': 'Payments',
        'markdown': '# Payments\n',
        'version': None,
        'is_draft': True,
        'published': False,
    }
    assert found['documents'] == []
    runbook = live(store, 'runbook', 'Runbook', service_ids=[service], tags=['inventory'])
    found = documents_of(client, 'service', service)
    assert found['inventory_document']['id'] == runbook  # a live one wins over a brand-new draft
    assert [item['id'] for item in found['documents']] == [runbook]
    assert documents_of(client, 'code_project', resource(store, 'repositories', 'Shop'))['inventory_document'] is None


def test_tags_are_content_edited_through_a_draft_and_promote(store, client):
    environment = resource(store, 'environments', 'production')
    runbook = live(store, 'runbook', 'Runbook', environment_ids=[environment])
    assert documents_of(client, 'environment', environment)['inventory_document'] is None
    draft = client.post(DRAFTS, headers=HEADERS, json={'document_id': runbook}).json()
    body = {key: draft[key] for key in ('title', 'description', 'purpose', 'audience', 'markdown')}
    saved = client.put(f'{DRAFTS}/{draft["id"]}', headers=HEADERS, json={**body, 'tags': ['inventory'], 'revision': 1})
    assert saved.status_code == 200, saved.text
    # a draft of a live document is not the inventory document: its document becomes it when the draft is promoted
    assert documents_of(client, 'environment', environment)['inventory_document'] is None
    promoted = client.post(f'{DRAFTS}/{draft["id"]}/promote', headers=HEADERS, json={'revision': 2})
    assert promoted.status_code == 200, promoted.text
    assert client.get(f'{DOCUMENTS}/{runbook}', headers=HEADERS).json()['tags'] == ['inventory']
    assert documents_of(client, 'environment', environment)['inventory_document']['id'] == runbook


# Filtering by tag


def test_documents_and_drafts_filter_by_one_tag_exactly_and_case_insensitively(store, client):
    ops = live(store, 'ops', 'Ops', tags=['Ops', 'inventory'])
    near = live(store, 'near', 'Near', tags=['ops-x'])
    live(store, 'none', 'None')

    def listed(tag):
        response = client.get(DOCUMENTS, headers=HEADERS, params={'tag': tag})
        assert response.status_code == 200, response.text
        return [row['id'] for row in response.json()['documents']], response.json()['total']

    assert listed('OPS') == ([ops], 1)
    assert listed('inventory') == ([ops], 1)
    assert listed('ops-x') == ([near], 1)
    assert listed('op') == ([], 0)
    assert len(client.get(DOCUMENTS, headers=HEADERS).json()['documents']) == 3
    brand_new = client.post(
        DRAFTS,
        headers=HEADERS,
        json={'slug': 'notes', 'title': 'Notes', 'markdown': '# Notes\n', 'tags': ['Runbook']},
    ).json()['id']
    of_near = client.post(DRAFTS, headers=HEADERS, json={'document_id': near}).json()['id']

    def drafts(tag):
        response = client.get(DRAFTS, headers=HEADERS, params={'tag': tag})
        assert response.status_code == 200, response.text
        return [row['id'] for row in response.json()['drafts']]

    assert drafts('runbook') == [brand_new]
    assert drafts('OPS-X') == [of_near]  # a draft of a document carries its tags
    assert drafts('ops') == []
    assert len(client.get(DRAFTS, headers=HEADERS).json()['drafts']) == 2


# Discovery


@pytest.fixture
def discovery(world):
    ssh_ready(world)
    sid = start(world).json()['id']
    return world, sid, config_of(world, sid)


def propose(config, tool, **arguments):
    spec = CATALOG[tool]
    return asyncio.run(spec.run({'config': config}, spec.arguments.model_validate(arguments)))


def accept_only(world, sid) -> str:
    [proposal] = world.client.get(f'/api/discovery/{sid}/proposals', headers=HEADERS).json()['proposals']
    accepted = world.client.post(
        f'/api/discovery/proposals/{proposal["id"]}/accept', headers=HEADERS, json={'revision': proposal['revision']}
    )
    assert accepted.status_code == 200, accepted.text
    return accepted.json()['result_id']


def test_discovery_tags_a_new_document_only_when_the_resource_has_none_and_never_tags_an_existing_one(discovery):
    """Review Focus 3."""
    world, sid, config = discovery
    with world.store.connect() as conn:
        existing = live_document(
            conn, world.project, 'disks', 'Disk layout', '# Disks', server_ids=[world.server['id']], tags=['ops']
        )
    propose(config, 'propose_document', title='Disk layout', markdown='# new disks', description='')
    update = accept_only(world, sid)
    with world.store.connect() as conn:
        row = conn.execute('SELECT draft_of,tags FROM ka.documents WHERE id=%s', (update,)).fetchone()
        assert (str(row['draft_of']), row['tags']) == (str(existing['id']), ['ops'])  # no tag added
        assert tags_of(conn, existing['id']) == ['ops']
    other = start(world).json()['id']
    propose(config_of(world, other), 'propose_document', title='Network', markdown='# Net', description='')
    first = accept_only(world, other)
    with world.store.connect() as conn:
        assert tags_of(conn, first) == ['inventory']  # web-1 had no document tagged inventory
    third = start(world).json()['id']
    propose(config_of(world, third), 'propose_document', title='Users', markdown='# Users', description='')
    second = accept_only(world, third)
    with world.store.connect() as conn:
        assert tags_of(conn, second) == []  # the brand-new draft tagged inventory counts
        assert tags_of(conn, first) == ['inventory']


def test_propose_service_has_no_operations_notes():
    spec = CATALOG['propose_service']
    assert 'operations_notes' not in spec.arguments.model_json_schema()['properties']
    with pytest.raises(ValidationError):
        spec.arguments.model_validate(
            {'name': 'redis', 'deployment': {'runtime': 'systemd'}, 'operations_notes': 'Restart nightly.'}
        )


# Nothing reads the removed columns


def test_nothing_reads_operations_notes_or_the_inventory_flag():
    """Review Focus 1 (backend): only migrations and the tests of the migrations that had them name them."""
    patterns = (
        r'operations_notes',
        r'\b[dlx]\.inventory\b',
        r'documents\.inventory\b',
        r'claim_inventory',
        r'INVENTORY_TAKEN',
        r'SET inventory',
        r'/documents/\{document_id\}/inventory',
        r'document\.inventory\.(un)?marked',
    )
    found = []
    folders = ['services/admin-api/src', 'docs/agents.md', 'docs/documentation.md']
    for folder in folders:
        base = ROOT / folder
        for path in [base] if base.is_file() else base.rglob('*'):
            if not path.is_file() or path.suffix not in {'.py', '.md', '.json', '.txt', '.sql'}:
                continue
            text = path.read_text(errors='ignore')
            found += [f'{path.relative_to(ROOT)}: {pattern}' for pattern in patterns if re.search(pattern, text)]
    assert found == []
