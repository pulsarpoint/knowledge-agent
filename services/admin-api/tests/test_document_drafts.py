"""Document drafts: create, save, discard and promote — fresh, stale (confirmed), a new document, a name a brand-new
draft holds, and promote deleting the document's other drafts."""

from uuid import UUID, uuid4

from drafts_world import API, audit, docs, make_document, new_draft, promote, save_live, version_of  # noqa: F401
from knowledge_admin.documents.drafts import NAME_IN_USE, NEW_DEFAULTS
from live_documents import write_live_document
from knowledge_admin.documents.store import (
    DRAFT_CHANGED,
    DRAFT_NOT_FOUND,
    STALE_BASE,
    STALE_MESSAGE,
    Provenance,
    create_draft,
    draft_outcome,
)
from session_rows import a_model, add_ad_hoc_session
from test_repositories import HEADERS


def outcome(docs, draft_id) -> str | None:
    with docs.store.connect() as conn:
        found = draft_outcome(conn, UUID(str(draft_id)))
    return found and found['status']


def test_a_draft_of_a_document_starts_from_its_live_version(docs):
    draft = new_draft(docs, document_id=docs.runbook)
    assert (draft['document_id'], draft['title'], draft['description'], draft['markdown'], draft['origin']) == (
        docs.runbook,
        'Runbook',
        'How to run it',
        '# Runbook v1',
        'manual',
    )
    assert (draft['base_version'], draft['live_version'], draft['stale'], draft['author']) == (
        1,
        1,
        False,
        'admin@example.test',
    )
    assert 'status' not in draft and 'base_version_id' not in draft
    assert [
        (s['kind'], s['source_document_id'], s['source_title'], s['source_version'], s['deleted'])
        for s in draft['sources']
    ] == [('parent', docs.runbook, 'Runbook', 1, False)]
    given = new_draft(docs, document_id=docs.runbook, title='Runbook (draft)', markdown='# Runbook v2')
    assert (given['title'], given['description'], given['markdown']) == (
        'Runbook (draft)',
        'How to run it',
        '# Runbook v2',
    )
    assert audit(docs, draft['id']) == ['document.draft.created']
    assert docs.client.post(f'{API}/drafts', headers=HEADERS, json={'document_id': str(uuid4())}).status_code == 404


def test_a_new_document_draft_needs_its_name_and_text(docs):
    for body in (
        {},
        {'slug': 'disk-layout'},
        {'slug': 'disk-layout', 'title': 'Disk layout'},
        {'document_id': docs.runbook, 'slug': 'x'},
        {'slug': 'Disk Layout', 'title': 'D', 'markdown': '# D'},
        {'slug': 'd', 'title': 'D', 'markdown': '# D', 'folder': 'servers'},  # documents have no folders
        {'slug': 'd', 'title': ' ', 'markdown': '# D'},
    ):
        assert docs.client.post(f'{API}/drafts', headers=HEADERS, json=body).status_code == 400, body
    draft = new_draft(docs, slug='disk-layout', title='Disk layout', markdown='# Disks')
    assert (draft['document_id'], draft['slug'], draft['base_version'], draft['sources']) == (
        None,
        'disk-layout',
        None,
        [],
    )
    # A name that is already a document's: a person's new document is refused (a run's save drafts that document,
    # store.create_draft's rule; see test_new_document_runs).
    named = docs.client.post(
        f'{API}/drafts', headers=HEADERS, json={'slug': 'runbook', 'title': 'Runbook again', 'markdown': '# Again'}
    )
    assert named.status_code == 409 and named.json()['error']['message'] == NAME_IN_USE.format(slug='runbook')
    # A brand-new draft holds its slug: a second brand-new draft of that name is refused.
    taken = docs.client.post(
        f'{API}/drafts', headers=HEADERS, json={'slug': 'disk-layout', 'title': 'D', 'markdown': '# D'}
    )
    assert taken.status_code == 409 and taken.json()['error']['message'] == NAME_IN_USE.format(slug='disk-layout')
    assert NAME_IN_USE.format(slug='x') == 'A document or draft named x already exists. Choose another name.'


def test_saving_a_draft_needs_its_revision_and_marks_it_edited(docs):
    draft = new_draft(docs, document_id=docs.runbook)
    url = f'{API}/drafts/{draft["id"]}'
    body = {'title': 'Runbook', 'markdown': '# Runbook, edited'}
    saved = docs.client.put(url, headers=HEADERS, json={**body, 'revision': 1})
    assert saved.status_code == 200, saved.text
    assert (saved.json()['revision'], saved.json()['edited'], saved.json()['markdown']) == (
        2,
        True,
        '# Runbook, edited',
    )
    stale = docs.client.put(url, headers=HEADERS, json={**body, 'markdown': '# Lost?', 'revision': 1})
    assert stale.status_code == 409 and stale.json()['error']['message'] == DRAFT_CHANGED
    assert docs.client.get(url, headers=HEADERS).json()['markdown'] == '# Runbook, edited'
    assert audit(docs, draft['id']) == ['document.draft.created', 'document.draft.updated']


def test_promote_saves_a_live_version_with_the_drafts_sources(docs):
    overview = make_document(docs, 'overview', 'Overview')
    draft = new_draft(docs, document_id=docs.runbook, markdown='# Runbook v2')
    with docs.store.connect() as conn:  # the author read the overview while writing
        conn.execute(
            """INSERT INTO ka.document_sources(project_id,document_id,kind,source_document_id,source_title,
            source_version) VALUES (%s,%s,'read',%s,'Overview',%s)""",
            (docs.project, draft['id'], overview, version_of(docs, overview)),
        )
    promoted = promote(docs, draft)
    assert promoted.status_code == 200, promoted.text
    assert promoted.json() == {
        'document_id': docs.runbook,
        'version': 2,
        'revision': 2,
        'deleted_drafts': 1,
    }
    assert docs.client.get(f'{API}/drafts/{draft["id"]}', headers=HEADERS).status_code == 404  # the draft is gone
    assert outcome(docs, draft['id']) == 'promoted'
    document = docs.client.get(f'{API}/documents/{docs.runbook}', headers=HEADERS).json()
    assert (
        document['version'],
        document['slug'],
        document['description'],
        document['markdown'],
        document['origin'],
    ) == (2, 'runbook', 'How to run it', '# Runbook v2', 'manual')
    with docs.store.connect() as conn:
        copied = conn.execute(
            """SELECT kind,source_title,source_version FROM ka.document_sources WHERE document_id=%s
            ORDER BY kind""",
            (UUID(docs.runbook),),
        ).fetchall()
    assert copied == [
        {'kind': 'parent', 'source_title': 'Runbook', 'source_version': 1},
        {'kind': 'read', 'source_title': 'Overview', 'source_version': 1},
    ]
    assert audit(docs, draft['id']) == ['document.draft.created', 'document.draft.promoted']
    # Review focus 1: Promote clicked twice — one version, the second finds the draft gone.
    again = promote(docs, draft)
    assert again.status_code == 404 and again.json()['error']['message'] == DRAFT_NOT_FOUND
    assert len(docs.client.get(f'{API}/documents/{docs.runbook}', headers=HEADERS).json()['versions']) == 2


def test_a_stale_draft_is_promoted_only_when_confirmed(docs):
    draft = new_draft(docs, document_id=docs.runbook, markdown='# Draft text')
    save_live(docs, docs.runbook, '# Runbook v2 by a person')
    assert docs.client.get(f'{API}/drafts/{draft["id"]}', headers=HEADERS).json()['stale'] is True
    refused = promote(docs, draft)
    message = STALE_MESSAGE.format(base=1, live=2)
    assert (
        message == "This draft was based on version 1; the live version is now 2. Promoting replaces version 2's text."
    )
    assert refused.status_code == 409 and refused.json()['error'] == {'message': message, 'code': STALE_BASE}
    confirmed = promote(docs, draft, confirm_stale=True)
    assert confirmed.status_code == 200, confirmed.text
    assert (confirmed.json()['revision'], confirmed.json()['version']) == (3, 3)
    assert docs.client.get(f'{API}/documents/{docs.runbook}', headers=HEADERS).json()['markdown'] == '# Draft text'


def test_a_new_document_draft_creates_the_document_with_the_resources_that_still_exist(docs):
    server = docs.client.post('/api/servers', headers=HEADERS, json={'name': 'web-1', 'hostname': '10.0.0.7'}).json()
    gone = uuid4()
    with docs.store.connect() as conn:
        conn.execute("INSERT INTO ka.services(id,project_id,name) VALUES (%s,%s,'Billing')", (gone, docs.project))
    draft = new_draft(
        docs,
        slug='disk-layout',
        title='Disk layout',
        markdown='# Disks',
        resource_ids={'server_ids': [server['id']], 'service_ids': [str(gone)]},
    )
    with docs.store.connect() as conn:  # Review focus 2: a chosen resource is deleted before the promote
        conn.execute('DELETE FROM ka.services WHERE id=%s', (gone,))
    promoted = promote(docs, draft)
    assert promoted.status_code == 200, promoted.text
    created = docs.client.get(f'{API}/documents/{promoted.json()["document_id"]}', headers=HEADERS).json()
    assert (created['slug'], created['title'], created['server_ids'], created['service_ids']) == (
        'disk-layout',
        'Disk layout',
        [server['id']],
        [],
    )
    assert (created['id'], created['version'], promoted.json()['deleted_drafts']) == (draft['id'], 1, 0)  # same id
    assert outcome(docs, draft['id']) == 'promoted'


def test_a_brand_new_drafts_name_cannot_be_taken_by_a_live_document(docs):
    draft = new_draft(docs, slug='overview', title='Overview draft', markdown='# Draft overview')
    taken = docs.client.post(
        f'{API}/drafts',
        headers=HEADERS,
        json={'slug': 'overview', 'title': 'Overview', 'markdown': '# Overview by a person'},
    )
    assert taken.status_code == 409, taken.text
    assert taken.json()['error']['message'] == NAME_IN_USE.format(slug='overview')
    promoted = promote(docs, draft)
    assert promoted.status_code == 200 and promoted.json()['document_id'] == draft['id']


def test_promote_deletes_the_documents_other_drafts(docs):
    chosen = new_draft(docs, document_id=docs.runbook, markdown='# Chosen')
    others = [new_draft(docs, document_id=docs.runbook, markdown=f'# Other {n}') for n in (1, 2)]
    elsewhere = new_draft(docs, document_id=make_document(docs, 'overview', 'Overview'))
    assert docs.client.get(f'{API}/documents/{docs.runbook}', headers=HEADERS).json()['draft_count'] == 3
    promoted = promote(docs, chosen)
    assert promoted.status_code == 200 and promoted.json()['deleted_drafts'] == 3  # the promoted draft included
    for other in others:
        assert docs.client.get(f'{API}/drafts/{other["id"]}', headers=HEADERS).status_code == 404
    assert docs.client.get(f'{API}/drafts/{elsewhere["id"]}', headers=HEADERS).status_code == 200
    assert docs.client.get(f'{API}/documents/{docs.runbook}/drafts', headers=HEADERS).json()['drafts'] == []
    assert [outcome(docs, other['id']) for other in others] == ['replaced', 'replaced']  # deleted by the promote


def test_the_drafts_list_is_newest_created_first(docs):
    older = new_draft(docs, document_id=docs.runbook, markdown='# Older')
    newer = new_draft(docs, slug='disk-layout', title='Disk layout', markdown='# Disk')
    edited = docs.client.put(
        f'{API}/drafts/{older["id"]}',
        headers=HEADERS,
        json={'revision': 1, 'title': 'Runbook', 'markdown': '# Older, edited later'},
    )
    assert edited.status_code == 200, edited.text
    listed = docs.client.get(f'{API}/drafts', headers=HEADERS).json()['drafts']
    assert [draft['id'] for draft in listed] == [newer['id'], older['id']]


def test_a_discarded_draft_is_deleted(docs):
    draft = new_draft(docs, document_id=docs.runbook)
    url = f'{API}/drafts/{draft["id"]}'
    stale = docs.client.post(url + '/discard', headers=HEADERS, json={'revision': 2})
    assert stale.status_code == 409 and stale.json()['error']['message'] == DRAFT_CHANGED
    discarded = docs.client.post(url + '/discard', headers=HEADERS, json={'revision': 1})
    assert discarded.status_code == 200 and discarded.json() == {}
    assert outcome(docs, draft['id']) == 'discarded'
    for response in (
        docs.client.post(url + '/discard', headers=HEADERS, json={'revision': 1}),
        docs.client.put(url, headers=HEADERS, json={'revision': 1, 'title': 'T', 'markdown': '# T'}),
        promote(docs, draft),
        docs.client.get(url, headers=HEADERS),
    ):
        assert response.status_code == 404 and response.json()['error']['message'] == DRAFT_NOT_FOUND
    assert audit(docs, draft['id']) == ['document.draft.created', 'document.draft.discarded']
    assert docs.client.get(f'{API}/drafts/{uuid4()}', headers=HEADERS).status_code == 404
    fresh = new_draft(docs, slug='disk-layout', title='Disk layout', markdown='# Disks')
    gone = docs.client.post(f'{API}/drafts/{fresh["id"]}/discard', headers=HEADERS, json={'revision': 1})
    assert gone.status_code == 200
    assert new_draft(docs, slug='disk-layout', title='Disk layout', markdown='# Again')['slug'] == 'disk-layout'


def test_promoting_a_draft_keeps_the_links_and_drops_an_archived_code_project_from_the_snapshot(docs):
    kept, archived = uuid4(), uuid4()
    with docs.store.connect() as conn:
        conn.execute(
            """INSERT INTO ka.repositories(id,project_id,slug,name) VALUES (%s,%s,'payments','Payments'),
            (%s,%s,'legacy','Legacy')""",
            (kept, docs.project, archived, docs.project),
        )
    document_id = write_live_document(
        docs.store, 'payments', 'Payments', '# Payments', code_project_ids=[kept, archived]
    )['id']
    draft = new_draft(docs, document_id=document_id, markdown='# Payments v2')
    with docs.store.connect() as conn:
        conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id=%s', (archived,))
    promoted = promote(docs, draft)
    assert promoted.status_code == 200, promoted.text
    document = docs.client.get(f'{API}/documents/{document_id}', headers=HEADERS).json()
    assert (document['version'], sorted(document['code_project_ids'])) == (2, sorted([str(kept), str(archived)]))
    assert [row['id'] for row in document['source_snapshot']['code_projects']] == [str(kept)]


def test_an_agent_draft_is_promoted_as_generated_text_of_its_run(docs):
    with docs.store.connect() as conn:
        actor = conn.execute('SELECT id FROM ka.principals ORDER BY created_at LIMIT 1').fetchone()['id']
        session = add_ad_hoc_session(conn, docs.project, actor, a_model(conn, actor))
        draft_id = create_draft(
            conn,
            docs.project,
            Provenance('agent_run', None, session),
            {**NEW_DEFAULTS, 'title': 'Payments', 'markdown': '# Payments'},
            origin='generated',
            slug='payments',
        )
    draft = docs.client.get(f'{API}/drafts/{draft_id}', headers=HEADERS).json()
    assert (draft['origin'], draft['session_id'], draft['session_title'], draft['author']) == (
        'agent',
        str(session),
        'Payments',
        None,
    )
    promoted = promote(docs, draft)
    assert promoted.status_code == 200, promoted.text
    document = docs.client.get(f'{API}/documents/{promoted.json()["document_id"]}', headers=HEADERS).json()
    assert document['origin'] == 'generated'
    with docs.store.connect() as conn:
        assert conn.execute('SELECT document_id FROM ka.agent_sessions WHERE id=%s', (session,)).fetchone()[
            'document_id'
        ] == UUID(document['id'])
