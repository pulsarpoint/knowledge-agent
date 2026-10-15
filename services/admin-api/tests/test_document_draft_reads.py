"""Reading drafts: the documents list's counts and filter, new-document drafts, a document's drafts, connections and
links — and that drafts are never documents."""

import asyncio
import json
from uuid import uuid4

from drafts_world import API, audit, docs, make_document, new_draft, promote, save_live, version_of  # noqa: F401
from knowledge_admin.capabilities import inventory
from knowledge_admin.documents.drafts import LINK_NOT_FOUND, LINK_SELF, LINK_TWICE
from knowledge_admin.tools.catalog import CATALOG
from live_documents import link_document, write_live_document
from test_repositories import HEADERS


def test_the_list_counts_open_drafts_and_filters_documents_that_have_them(docs):
    overview = make_document(docs, 'overview', 'Overview')
    new_draft(docs, document_id=docs.runbook)
    new_draft(docs, document_id=docs.runbook, markdown='# Second')
    discarded = new_draft(docs, document_id=overview)
    docs.client.post(f'{API}/drafts/{discarded["id"]}/discard', headers=HEADERS, json={'revision': 1})
    new_draft(docs, slug='disk-layout', title='Disk layout', markdown='# Disks')
    rows = {
        row['slug']: row['draft_count']
        for row in docs.client.get(f'{API}/documents', headers=HEADERS).json()['documents']
    }
    assert rows == {'runbook': 2, 'overview': 0}
    filtered = docs.client.get(f'{API}/documents', params={'has_drafts': 'true'}, headers=HEADERS).json()
    assert (filtered['total'], [row['slug'] for row in filtered['documents']]) == (1, ['runbook'])


def test_new_document_drafts_are_listed_apart_and_a_documents_drafts_without_their_text(docs):
    of_runbook = new_draft(docs, document_id=docs.runbook, markdown='# Long text')
    fresh = new_draft(docs, slug='disk-layout', folder='servers', title='Disk layout', markdown='# Disks')
    listed = docs.client.get(f'{API}/drafts', params={'new': 'true'}, headers=HEADERS).json()['drafts']
    assert [(d['id'], d['title'], d['folder'], d['slug'], d['origin'], d['author']) for d in listed] == [
        (fresh['id'], 'Disk layout', 'servers', 'disk-layout', 'manual', 'admin@example.test')
    ]
    assert 'markdown' not in listed[0]
    mine = docs.client.get(f'{API}/documents/{docs.runbook}/drafts', headers=HEADERS).json()['drafts']
    assert [d['id'] for d in mine] == [of_runbook['id']] and 'markdown' not in mine[0]
    assert (mine[0]['base_version'], mine[0]['live_version'], mine[0]['stale']) == (1, 1, False)
    save_live(docs, docs.runbook, '# Runbook v2')
    detail = docs.client.get(f'{API}/drafts/{of_runbook["id"]}', headers=HEADERS).json()
    assert (detail['stale'], detail['base_version'], detail['live_version'], detail['live_markdown']) == (
        True,
        1,
        2,
        '# Runbook v2',
    )
    assert docs.client.get(f'{API}/documents/{uuid4()}/drafts', headers=HEADERS).status_code == 404


def test_drafts_are_never_documents(docs, monkeypatch):
    monkeypatch.setattr(inventory, '_store', docs.store)
    new_draft(docs, document_id=docs.runbook, markdown='# DRAFT ONLY TEXT')
    new_draft(docs, slug='secret-plan', folder='plans', title='Secret plan', markdown='# DRAFT ONLY TEXT')
    document = docs.client.get(f'{API}/documents/{docs.runbook}', headers=HEADERS).json()
    assert (document['version'], document['markdown'], len(document['versions'])) == (
        1,
        '# Runbook v1',
        1,
    )
    assert [row['slug'] for row in docs.client.get(f'{API}/documents', headers=HEADERS).json()['documents']] == [
        'runbook'
    ]
    assert not any('DRAFT ONLY TEXT' in path.read_text() for path in docs.out.rglob('*') if path.is_file())
    assert all(row['folder'] != 'plans' for row in docs.client.get(f'{API}/folders', headers=HEADERS).json()['folders'])
    with docs.store.connect() as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM ka.document_details WHERE slug='secret-plan'").fetchone()['n'] == 0
        )
        server = conn.execute(
            """INSERT INTO ka.servers(project_id,name,hostname,username,enabled)
            VALUES (%s,'web-1','10.0.0.7','ops',false) RETURNING id""",
            (docs.project,),
        ).fetchone()['id']
        link_document(conn, docs.project, docs.runbook, 'server_id', server)
    spec = CATALOG['get_document']
    config = {'project_id': str(docs.project), 'ssh': {'enabled': True, 'servers': [{'id': str(server)}]}}
    text = asyncio.run(spec.run({'config': config}, spec.arguments.model_validate({'document_id': docs.runbook})))
    assert json.loads(text)['markdown'] == '# Runbook v1'


def test_links_add_and_remove_dependencies(docs):
    overview = make_document(docs, 'overview', 'Overview')
    links = f'{API}/documents/{docs.runbook}/links'
    added = docs.client.post(links, headers=HEADERS, json={'to_document_id': overview})
    assert added.status_code == 201, added.text
    assert [d['title'] for d in added.json()['depends_on']] == ['Overview']
    twice = docs.client.post(links, headers=HEADERS, json={'to_document_id': overview})
    assert twice.status_code == 409 and twice.json()['error']['message'] == LINK_TWICE
    itself = docs.client.post(links, headers=HEADERS, json={'to_document_id': docs.runbook})
    assert itself.status_code == 400 and itself.json()['error']['message'] == LINK_SELF
    assert docs.client.post(links, headers=HEADERS, json={'to_document_id': str(uuid4())}).status_code == 404
    other = docs.client.get(f'{API}/documents/{overview}/connections', headers=HEADERS).json()
    assert ([d['title'] for d in other['used_by']], other['depends_on']) == (['Runbook'], [])
    removed = docs.client.delete(f'{links}/{overview}', headers=HEADERS)
    assert removed.status_code == 200 and removed.json()['depends_on'] == []
    again = docs.client.delete(f'{links}/{overview}', headers=HEADERS)
    assert again.status_code == 404 and again.json()['error']['message'] == LINK_NOT_FOUND
    assert audit(docs, docs.runbook)[-2:] == ['document.link.added', 'document.link.removed']


def test_a_document_deleted_while_a_link_is_added_answers_not_found(docs, monkeypatch):
    overview = make_document(docs, 'overview', 'Overview')
    connect = docs.store.connect

    class Racing:
        """A connection on which the linked document is deleted (and committed) just before the link is inserted."""

        def __init__(self):
            self.conn = connect()

        def __enter__(self):
            self.conn.__enter__()
            return self

        def __exit__(self, *exc):
            return self.conn.__exit__(*exc)

        def execute(self, query, params=None):
            if query.lstrip().startswith('INSERT INTO ka.document_links'):
                with connect() as other:
                    other.execute('DELETE FROM ka.documents WHERE id=%s', (overview,))
            return self.conn.execute(query, params)

        def __getattr__(self, name):
            return getattr(self.conn, name)

    monkeypatch.setattr(docs.store, 'connect', Racing)
    response = docs.client.post(
        f'{API}/documents/{docs.runbook}/links', headers=HEADERS, json={'to_document_id': overview}
    )
    assert response.status_code == 404 and response.json()['error']['message'] == 'Document not found'


def test_connections_show_what_the_live_version_was_built_from_even_when_a_source_is_gone(docs):
    overview = make_document(docs, 'overview', 'Overview')
    draft = new_draft(docs, document_id=docs.runbook, markdown='# Runbook v2')
    with docs.store.connect() as conn:
        conn.execute(
            """INSERT INTO ka.document_sources(project_id,document_id,kind,source_document_id,source_title,
            source_version) VALUES (%s,%s,'read',%s,'Overview',%s)""",
            (docs.project, draft['id'], overview, version_of(docs, overview)),
        )
    assert promote(docs, draft).status_code == 200
    # Review focus 3: the source is deleted afterwards.
    assert (
        docs.client.delete(
            f'{API}/documents/{overview}', headers=HEADERS, params={'revision': 1, 'confirmed': True}
        ).status_code
        == 200
    )
    built = docs.client.get(f'{API}/documents/{docs.runbook}/connections', headers=HEADERS).json()['built_from']
    assert [(s['kind'], s['source_title'], s['source_version'], s['deleted']) for s in built] == [
        ('parent', 'Runbook', 1, False),
        ('read', 'Overview', 1, True),
    ]
    versions = docs.client.get(f'{API}/documents/{docs.runbook}', headers=HEADERS).json()['versions']
    assert [(v['version'], v['from_draft']) for v in versions] == [(2, True), (1, False)]


def test_deleting_a_document_deletes_its_drafts_and_links(docs):
    overview = make_document(docs, 'overview', 'Overview')
    draft = new_draft(docs, document_id=overview)
    docs.client.post(f'{API}/documents/{docs.runbook}/links', headers=HEADERS, json={'to_document_id': overview})
    assert (
        docs.client.delete(
            f'{API}/documents/{overview}', headers=HEADERS, params={'revision': 1, 'confirmed': True}
        ).status_code
        == 200
    )
    assert docs.client.get(f'{API}/drafts/{draft["id"]}', headers=HEADERS).status_code == 404
    assert docs.client.get(f'{API}/documents/{docs.runbook}/connections', headers=HEADERS).json()['depends_on'] == []


def test_drafts_are_found_by_the_resources_they_are_for(docs):
    with docs.store.connect() as conn:
        production, staging = (
            conn.execute(
                'INSERT INTO ka.environments(project_id,name) VALUES (%s,%s) RETURNING id', (docs.project, name)
            ).fetchone()['id']
            for name in ('Production', 'Staging')
        )
    built = new_draft(
        docs, slug='prod-runbook', title='Prod', markdown='# Prod', resource_ids={'environment_ids': [str(production)]}
    )
    linked = write_live_document(docs.store, 'prod-notes', 'Notes', '# Notes', environment_ids=[production])['id']
    of_linked = new_draft(docs, document_id=linked, markdown='# Notes v2')
    new_draft(docs, document_id=docs.runbook)  # not linked to anything
    new_draft(docs, slug='staging', title='Staging', markdown='# S', resource_ids={'environment_ids': [str(staging)]})
    found = docs.client.get(f'{API}/drafts', params={'environment_id': str(production)}, headers=HEADERS).json()
    assert sorted(d['id'] for d in found['drafts']) == sorted([built['id'], of_linked['id']])
    only_new = docs.client.get(
        f'{API}/drafts', params={'environment_id': str(production), 'new': 'true'}, headers=HEADERS
    ).json()
    assert [d['id'] for d in only_new['drafts']] == [built['id']]
    assert docs.client.get(f'{API}/drafts', params={'service_id': str(uuid4())}, headers=HEADERS).json() == {
        'drafts': []
    }
    assert len(docs.client.get(f'{API}/drafts', headers=HEADERS).json()['drafts']) == 4
    detail = docs.client.get(f'{API}/drafts/{built["id"]}', headers=HEADERS).json()
    assert detail['resource_ids'] == {
        'code_project_ids': [],
        'service_ids': [],
        'server_ids': [],
        'deployment_ids': [],
        'environment_ids': [str(production)],
    }
    assert docs.client.get(f'{API}/drafts/{of_linked["id"]}', headers=HEADERS).json()['resource_ids'][
        'environment_ids'
    ] == [str(production)]
    gone = docs.client.post(
        f'{API}/drafts',
        headers=HEADERS,
        json={'slug': 'x', 'title': 'X', 'markdown': '# X', 'resource_ids': {'service_ids': [str(uuid4())]}},
    )
    assert gone.status_code == 400, gone.text
