"""Every save is a draft: no route writes live content directly; links are saved on their own (a live document or a
brand-new draft); a brand-new draft opens at /documents/{id} and may be renamed until it is published. Accepting a
discovery document proposal creates a draft; publishing a brand-new one attaches it to its inventory entry."""

from uuid import UUID, uuid4

from discovery_world import world  # noqa: F401  (fixture)
from drafts_world import API, docs, new_draft, promote  # noqa: F401
from knowledge_admin.documents.drafts import NAME_IN_USE
from knowledge_admin.documents.store import DOCUMENT_CHANGED, DOCUMENT_NOT_FOUND
from test_discovery_review import decide, listed, propose, run  # noqa: F401  (run: fixture)
from test_discovery_runs import config_of, start
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


def test_draft_rows_say_whether_their_document_is_published_and_its_title(docs):
    of_live = new_draft(docs, document_id=docs.runbook)
    brand_new = new_draft(docs, slug='disk-layout', title='Disk layout', markdown='# Disks')
    listed = docs.client.get(f'{API}/drafts', headers=HEADERS).json()['drafts']
    rows = {row['id']: (row['published'], row['document_title']) for row in listed}
    assert rows == {of_live['id']: (True, 'Runbook'), brand_new['id']: (False, None)}
    of_document = docs.client.get(f'{API}/documents/{docs.runbook}/drafts', headers=HEADERS).json()['drafts']
    assert [(row['id'], row['published']) for row in of_document] == [(of_live['id'], True)]
    assert docs.client.get(f'{API}/drafts/{of_live["id"]}', headers=HEADERS).json()['published'] is True
    assert docs.client.get(f'{API}/drafts/{brand_new["id"]}', headers=HEADERS).json()['published'] is False


# Discovery: accepting a document proposal creates a draft; publishing a brand-new one attaches it to its entry.


def draft_row(store, draft_id) -> dict | None:
    with store.connect() as conn:
        return conn.execute('SELECT * FROM ka.documents WHERE id=%s', (UUID(str(draft_id)),)).fetchone()


def attachments(store, document_id) -> list[tuple]:
    with store.connect() as conn:
        return [
            (row['server_id'], row['service_id'])
            for row in conn.execute(
                'SELECT server_id,service_id FROM ka.inventory_documents WHERE document_id=%s',
                (UUID(str(document_id)),),
            )
        ]


def publish(world, draft_id, path='promote'):
    revision = world.client.get(f'{API}/drafts/{draft_id}', headers=HEADERS).json()['revision']
    return world.client.post(f'{API}/drafts/{draft_id}/{path}', headers=HEADERS, json={'revision': revision})


def test_accepting_a_new_document_proposal_creates_a_brand_new_draft(run):
    world, sid, config = run
    propose(config, 'propose_document', title='Disk layout', markdown='# Disks', description='Mounts')
    [proposal] = listed(world, sid)
    accepted = decide(world, proposal, 'accept')
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    draft_id = body['proposal']['draft_id']
    assert draft_id is not None and body['result_id'] == draft_id
    assert (body['proposal']['status'], body['proposal']['draft_published']) == ('accepted', False)
    row = draft_row(world.store, draft_id)
    assert (row['is_draft'], row['draft_of'], row['origin'], str(row['session_id'])) == (True, None, 'generated', sid)
    assert (row['folder'], row['slug'], row['title'], row['markdown'], row['description']) == (
        'servers/web-1',
        'web-1-disk-layout',
        'Disk layout',
        '# Disks',
        'Mounts',
    )
    with world.store.connect() as conn:
        links = conn.execute(
            'SELECT server_id,service_id,connection,added_how,session_id FROM ka.document_resources WHERE document_id=%s',
            (UUID(draft_id),),
        ).fetchall()
        live = conn.execute("SELECT count(*) AS n FROM ka.live_documents WHERE title='Disk layout'").fetchone()['n']
    assert [
        (str(r['server_id']), r['service_id'], r['connection'], r['added_how'], str(r['session_id'])) for r in links
    ] == [(world.server['id'], None, 'built_for', 'discovery', sid)]
    assert live == 0 and attachments(world.store, draft_id) == []  # nothing live, nothing attached yet
    [listed_proposal] = listed(world, sid)
    assert (listed_proposal['draft_id'], listed_proposal['draft_published']) == (draft_id, False)
    # The draft opens at /documents/{id} as a brand-new draft.
    opened = world.client.get(f'{API}/documents/{draft_id}', headers=HEADERS).json()
    assert (opened['is_draft'], opened['published']) == (True, False)


def test_publishing_a_discovery_draft_attaches_it_once(run):
    world, sid, config = run
    propose(config, 'propose_document', title='Disk layout', markdown='# Disks')
    propose(config, 'propose_document', title='nginx ports', markdown='# Ports', about_service='nginx')
    first, about = listed(world, sid)
    assert decide(world, first, 'accept').status_code == 200
    assert decide(world, about, 'accept').status_code == 200
    first, about = listed(world, sid)
    # A second accepted proposal pointing at the same draft must not attach it twice.
    other = start(world).json()['id']
    propose(config_of(world, other), 'propose_document', title='Disk layout', markdown='# Disks, again')
    [second] = listed(world, other)
    assert decide(world, second, 'accept').status_code == 200
    with world.store.connect() as conn:
        conn.execute(
            'UPDATE ka.discovery_proposals SET draft_id=%s WHERE session_id=%s', (UUID(first['draft_id']), UUID(other))
        )
    published = publish(world, first['draft_id'])
    assert published.status_code == 200, published.text
    assert attachments(world.store, first['draft_id']) == [(UUID(world.server['id']), None)]
    attached = world.client.get(
        '/api/inventory/documents', headers=HEADERS, params={'entry_kind': 'server', 'entry_id': world.server['id']}
    ).json()['documents']
    assert sorted(item['title'] for item in attached) == ['Disk layout', 'web-1 overview']
    first, about = listed(world, sid)
    assert (first['draft_id'], first['draft_published'], about['draft_published']) == (
        first['result_id'],
        True,
        False,
    )
    # A document about a service attaches to the service.
    assert publish(world, about['draft_id']).status_code == 200
    assert attachments(world.store, about['draft_id']) == [(None, world.nginx)]


def test_discarding_a_discovery_draft_attaches_nothing(run):
    world, sid, config = run
    propose(config, 'propose_document', title='Disk layout', markdown='# Disks')
    [proposal] = listed(world, sid)
    draft_id = decide(world, proposal, 'accept').json()['proposal']['draft_id']
    discarded = publish(world, draft_id, 'discard')
    assert discarded.status_code == 200, discarded.text
    assert draft_row(world.store, draft_id) is None and attachments(world.store, draft_id) == []
    [proposal] = listed(world, sid)
    assert (proposal['status'], proposal['draft_id'], proposal['draft_published']) == ('accepted', None, False)


def test_accepting_an_update_proposal_creates_a_draft_of_the_document(run):
    world, sid, config = run
    propose(config, 'propose_document', title='web-1 overview', markdown='# web-1, updated')
    [proposal] = listed(world, sid)
    document_id = proposal['document_id']
    accepted = decide(world, proposal, 'accept')
    assert accepted.status_code == 200, accepted.text
    draft_id = accepted.json()['proposal']['draft_id']
    row = draft_row(world.store, draft_id)
    assert (row['is_draft'], str(row['draft_of']), row['base_version'], row['markdown'], row['origin']) == (
        True,
        document_id,
        1,
        '# web-1, updated',
        'generated',
    )
    live = world.client.get(f'{API}/documents/{document_id}', headers=HEADERS).json()
    assert (live['version'], live['revision'], live['markdown']) == (1, 1, '# web-1 overview\n\nBody.')
    [proposal] = listed(world, sid)
    assert (proposal['document_id'], proposal['draft_id'], proposal['draft_published']) == (
        document_id,
        draft_id,
        False,
    )
    assert publish(world, draft_id).status_code == 200
    [proposal] = listed(world, sid)
    assert (proposal['draft_id'], proposal['draft_published']) == (None, True)  # promoted: the draft row is gone
    live = world.client.get(f'{API}/documents/{document_id}', headers=HEADERS).json()
    assert (live['version'], live['markdown']) == (2, '# web-1, updated')
    assert attachments(world.store, document_id) == [(UUID(world.server['id']), None)]  # attached once, as before
