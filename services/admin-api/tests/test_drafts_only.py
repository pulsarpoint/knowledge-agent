"""Every save is a draft: no route writes live content directly; links are saved on their own (a live document or a
brand-new draft); a brand-new draft opens at /documents/{id} and may be renamed until it is published."""

from uuid import UUID, uuid4

from drafts_world import API, docs, new_draft, promote  # noqa: F401
from knowledge_admin.documents.drafts import NAME_IN_USE
from knowledge_admin.documents.store import DOCUMENT_CHANGED, DOCUMENT_NOT_FOUND
from test_document_resources_api import resource
from test_repositories import HEADERS

KEEPS_NAME = "A draft of a published document keeps its document's name."
NO_LINKS = {'code_project_ids': [], 'service_ids': [], 'server_ids': [], 'deployment_ids': [], 'environment_ids': []}


def links(docs, document_id, revision, **ids):
    return docs.client.put(
        f'{API}/documents/{document_id}/links', headers=HEADERS, json={**NO_LINKS, **ids, 'revision': revision}
    )


def link_actions(docs, document_id) -> list[tuple[str, dict]]:
    with docs.store.connect() as conn:
        return [
            (row['action'], row['details'])
            for row in conn.execute(
                "SELECT action,details FROM ka.audit_events WHERE resource_id=%s AND action LIKE 'document.links.%%' ORDER BY id",
                (UUID(str(document_id)),),
            )
        ]


def test_no_route_writes_live_content(docs):
    body = {'slug': 'direct', 'title': 'Direct', 'markdown': '# Direct'}
    assert docs.client.post(f'{API}/documents', headers=HEADERS, json=body).status_code == 405
    put = docs.client.put(f'{API}/documents/{docs.runbook}', headers=HEADERS, json={**body, 'revision': 1})
    assert put.status_code == 405
    document = docs.client.get(f'{API}/documents/{docs.runbook}', headers=HEADERS).json()
    assert (document['markdown'], document['revision'], document['is_draft'], document['published']) == (
        '# Runbook v1',
        1,
        False,
        True,
    )


def test_links_are_saved_on_their_own_with_history(docs):
    service = resource(docs.store, 'services', 'Payments')
    added = links(docs, docs.runbook, 1, service_ids=[service])
    assert added.status_code == 200, added.text
    body = added.json()
    assert (body['id'], body['revision']) == (docs.runbook, 2)
    assert [
        (link['name'], link['connection'], link['added_how'], link['added_by']) for link in body['resource_links']
    ] == [('Payments', 'linked', 'person', 'admin@example.test')]
    document = docs.client.get(f'{API}/documents/{docs.runbook}', headers=HEADERS).json()
    assert (document['version'], document['revision'], document['service_ids']) == (1, 2, [service])
    removed = links(docs, docs.runbook, 2, service_ids=[])
    assert removed.status_code == 200 and removed.json()['resource_links'] == []
    history = docs.client.get(f'{API}/documents/{docs.runbook}/resource-history', headers=HEADERS).json()['history']
    assert [(row['name'], row['removed_by']) for row in history] == [('Payments', 'admin@example.test')]
    assert link_actions(docs, docs.runbook) == [
        ('document.links.updated', {'links_added': [service], 'links_removed': []}),
        ('document.links.updated', {'links_added': [], 'links_removed': [service]}),
    ]
    # A page loaded before that change sends the old revision: nothing is written.
    stale = links(docs, docs.runbook, 2, service_ids=[service])
    assert stale.status_code == 409 and stale.json()['error']['message'] == DOCUMENT_CHANGED
    assert docs.client.get(f'{API}/documents/{docs.runbook}', headers=HEADERS).json()['service_ids'] == []
    assert links(docs, uuid4(), 1).status_code == 404
    assert links(docs, docs.runbook, 3, service_ids=[str(uuid4())]).status_code == 400


def test_a_built_for_link_cannot_be_unticked(docs):
    env = resource(docs.store, 'environments', 'Production')
    draft = new_draft(docs, slug='prod', title='Prod', markdown='# Prod', resource_ids={'environment_ids': [env]})
    published = promote(docs, draft)
    assert published.status_code == 200, published.text
    refused = links(docs, draft['id'], published.json()['revision'], environment_ids=[])
    assert refused.status_code == 409
    assert (
        refused.json()['error']['message']
        == "This document was built for environment Production; that link can't be removed."
    )
    document = docs.client.get(f'{API}/documents/{draft["id"]}', headers=HEADERS).json()
    assert (document['revision'], document['environment_ids']) == (published.json()['revision'], [env])
    assert link_actions(docs, draft['id']) == []


def test_links_of_a_brand_new_draft_but_not_of_a_draft_of_a_document(docs):
    env, service = resource(docs.store, 'environments', 'Production'), resource(docs.store, 'services', 'Payments')
    draft = new_draft(docs, slug='prod', title='Prod', markdown='# Prod', resource_ids={'environment_ids': [env]})
    saved = links(docs, draft['id'], draft['revision'], environment_ids=[env], service_ids=[service])
    assert saved.status_code == 200, saved.text
    assert saved.json()['revision'] == draft['revision'] + 1
    assert sorted((link['name'], link['connection']) for link in saved.json()['resource_links']) == [
        ('Payments', 'linked'),
        ('Production', 'built_for'),
    ]
    assert links(docs, draft['id'], saved.json()['revision'], service_ids=[service]).status_code == 409  # built-for
    of_live = new_draft(docs, document_id=docs.runbook)
    refused = links(docs, of_live['id'], of_live['revision'], service_ids=[service])
    assert refused.status_code == 404 and refused.json()['error']['message'] == DOCUMENT_NOT_FOUND


def test_a_brand_new_draft_opens_as_a_document_and_a_draft_of_a_document_does_not(docs):
    env = resource(docs.store, 'environments', 'Production')
    draft = new_draft(
        docs, slug='prod', folder='ops', title='Prod', markdown='# Prod', resource_ids={'environment_ids': [env]}
    )
    opened = docs.client.get(f'{API}/documents/{draft["id"]}', headers=HEADERS)
    assert opened.status_code == 200, opened.text
    document = opened.json()
    assert (
        document['id'],
        document['slug'],
        document['folder'],
        document['title'],
        document['markdown'],
        document['revision'],
        document['is_draft'],
        document['published'],
        document['origin'],
        document['session_id'],
        document['versions'],
        document['drafts'],
    ) == (draft['id'], 'prod', 'ops', 'Prod', '# Prod', 1, True, False, 'manual', None, [], [])
    assert [(link['name'], link['connection']) for link in document['resource_links']] == [('Production', 'built_for')]
    of_live = new_draft(docs, document_id=docs.runbook)
    missing = docs.client.get(f'{API}/documents/{of_live["id"]}', headers=HEADERS)
    assert missing.status_code == 404 and missing.json()['error']['message'] == DOCUMENT_NOT_FOUND
    # Published: the same id is the live document.
    assert promote(docs, draft).status_code == 200
    live = docs.client.get(f'{API}/documents/{draft["id"]}', headers=HEADERS).json()
    assert (live['is_draft'], live['published'], live['version']) == (False, True, 1)


def test_a_brand_new_draft_can_be_renamed_until_published(docs):
    draft = new_draft(docs, slug='disk-layout', title='Disk layout', markdown='# Disks')
    url = f'{API}/drafts/{draft["id"]}'
    text = {'title': 'Disk layout', 'markdown': '# Disks'}
    renamed = docs.client.put(url, headers=HEADERS, json={**text, 'revision': 1, 'slug': 'disks', 'folder': 'servers'})
    assert renamed.status_code == 200, renamed.text
    assert (renamed.json()['slug'], renamed.json()['folder'], renamed.json()['revision']) == ('disks', 'servers', 2)
    kept = docs.client.put(url, headers=HEADERS, json={**text, 'revision': 2})  # no name: it stays
    assert (kept.json()['slug'], kept.json()['folder']) == ('disks', 'servers')
    to_root = docs.client.put(url, headers=HEADERS, json={**text, 'revision': 3, 'folder': None})
    assert (to_root.json()['slug'], to_root.json()['folder']) == ('disks', None)
    taken = docs.client.put(url, headers=HEADERS, json={**text, 'revision': 4, 'slug': 'runbook'})
    assert taken.status_code == 409 and taken.json()['error']['message'] == NAME_IN_USE.format(slug='runbook')
    other = new_draft(docs, slug='other', title='Other', markdown='# Other')
    clash = docs.client.put(url, headers=HEADERS, json={**text, 'revision': 4, 'slug': 'other'})
    assert clash.status_code == 409 and clash.json()['error']['message'] == NAME_IN_USE.format(slug='other')
    nested = docs.client.put(url, headers=HEADERS, json={**text, 'revision': 4, 'slug': 'x', 'folder': 'runbook'})
    assert nested.status_code == 409 and 'holds the export of the document runbook' in nested.text
    assert docs.client.put(url, headers=HEADERS, json={**text, 'revision': 4, 'slug': 'Bad Name'}).status_code == 400
    assert docs.client.put(url, headers=HEADERS, json={**text, 'revision': 4, 'folder': 'Servers'}).status_code == 400
    current = docs.client.get(url, headers=HEADERS).json()
    assert (current['slug'], current['folder'], current['revision']) == ('disks', None, 4)
    assert docs.client.get(f'{API}/drafts/{other["id"]}', headers=HEADERS).json()['slug'] == 'other'


def test_a_draft_of_a_document_keeps_its_name(docs):
    draft = new_draft(docs, document_id=docs.runbook)
    url = f'{API}/drafts/{draft["id"]}'
    text = {'title': 'Runbook', 'markdown': '# Runbook v2'}
    for name in ({'slug': 'renamed'}, {'folder': 'ops'}, {'folder': None}):
        refused = docs.client.put(url, headers=HEADERS, json={**text, 'revision': 1, **name})
        assert refused.status_code == 400 and refused.json()['error']['message'] == KEEPS_NAME, name
    saved = docs.client.put(url, headers=HEADERS, json={**text, 'revision': 1})
    assert saved.status_code == 200 and (saved.json()['slug'], saved.json()['revision']) == ('runbook', 2)


def test_draft_rows_say_whether_their_document_is_published(docs):
    of_live = new_draft(docs, document_id=docs.runbook)
    brand_new = new_draft(docs, slug='disk-layout', title='Disk layout', markdown='# Disks')
    rows = {row['id']: row['published'] for row in docs.client.get(f'{API}/drafts', headers=HEADERS).json()['drafts']}
    assert rows == {of_live['id']: True, brand_new['id']: False}
    of_document = docs.client.get(f'{API}/documents/{docs.runbook}/drafts', headers=HEADERS).json()['drafts']
    assert [(row['id'], row['published']) for row in of_document] == [(of_live['id'], True)]
    assert docs.client.get(f'{API}/drafts/{of_live["id"]}', headers=HEADERS).json()['published'] is True
    assert docs.client.get(f'{API}/drafts/{brand_new["id"]}', headers=HEADERS).json()['published'] is False
