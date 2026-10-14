"""Document drafts: create, save, discard and promote — fresh, stale (confirmed), a new document, and a new document
whose name was taken meanwhile."""
from uuid import UUID, uuid4

from drafts_world import API, audit, docs, make_document, new_draft, promote, save_live, version_of  # noqa: F401
from knowledge_admin.documents.drafts import (DRAFT_CHANGED, DRAFT_DECIDED, NAME_TAKEN, NEW_DEFAULTS, STALE_BASE,
                                             STALE_MESSAGE, create_draft)
from session_rows import a_model, add_ad_hoc_session
from test_repositories import HEADERS


def test_a_draft_of_a_document_starts_from_its_live_version(docs):
    draft = new_draft(docs, document_id=docs.runbook)
    assert (draft['document_id'], draft['title'], draft['description'], draft['markdown'], draft['origin']) == (
        docs.runbook, 'Runbook', 'How to run it', '# Runbook v1', 'manual')
    assert (draft['base_version'], draft['live_version'], draft['stale'], draft['author'], draft['status']) == (
        1, 1, False, 'admin@example.test', 'open')
    assert [(s['kind'], s['source_document_id'], s['source_title'], s['source_version'], s['deleted'])
            for s in draft['sources']] == [('parent', docs.runbook, 'Runbook', 1, False)]
    given = new_draft(docs, document_id=docs.runbook, title='Runbook (draft)', markdown='# Runbook v2')
    assert (given['title'], given['description'], given['markdown']) == ('Runbook (draft)', 'How to run it', '# Runbook v2')
    assert audit(docs, draft['id']) == ['document.draft.created']
    assert docs.client.post(f'{API}/drafts', headers=HEADERS, json={'document_id': str(uuid4())}).status_code == 404


def test_a_new_document_draft_needs_its_name_and_text(docs):
    for body in ({}, {'slug': 'disk-layout'}, {'slug': 'disk-layout', 'title': 'Disk layout'},
                 {'document_id': docs.runbook, 'slug': 'x'}, {'slug': 'Disk Layout', 'title': 'D', 'markdown': '# D'},
                 {'slug': 'd', 'title': 'D', 'markdown': '# D', 'folder': 'Servers'},
                 {'document_id': docs.runbook, 'folder': 'servers'}, {'slug': 'd', 'title': ' ', 'markdown': '# D'}):
        assert docs.client.post(f'{API}/drafts', headers=HEADERS, json=body).status_code == 400, body
    draft = new_draft(docs, slug='disk-layout', folder='servers/web-1', title='Disk layout', markdown='# Disks')
    assert (draft['document_id'], draft['slug'], draft['folder'], draft['base_version'], draft['sources']) == (
        None, 'disk-layout', 'servers/web-1', None, [])
    # A name that is already a document's: the draft revises that document.
    named = new_draft(docs, slug='runbook', title='Runbook again', markdown='# Again')
    assert (named['document_id'], named['slug'], named['base_version'], named['title']) == (docs.runbook, None, 1, 'Runbook again')


def test_saving_a_draft_needs_its_revision_and_marks_it_edited(docs):
    draft = new_draft(docs, document_id=docs.runbook)
    url = f"{API}/drafts/{draft['id']}"
    body = {'title': 'Runbook', 'markdown': '# Runbook, edited'}
    saved = docs.client.put(url, headers=HEADERS, json={**body, 'revision': 1})
    assert saved.status_code == 200, saved.text
    assert (saved.json()['revision'], saved.json()['edited'], saved.json()['markdown']) == (2, True, '# Runbook, edited')
    stale = docs.client.put(url, headers=HEADERS, json={**body, 'markdown': '# Lost?', 'revision': 1})
    assert stale.status_code == 409 and stale.json()['error']['message'] == DRAFT_CHANGED
    assert docs.client.get(url, headers=HEADERS).json()['markdown'] == '# Runbook, edited'
    assert audit(docs, draft['id']) == ['document.draft.created', 'document.draft.updated']


def test_promote_saves_a_live_version_with_the_drafts_sources(docs):
    overview = make_document(docs, 'overview', 'Overview')
    draft = new_draft(docs, document_id=docs.runbook, markdown='# Runbook v2')
    with docs.store.connect() as conn:  # the author read the overview while writing
        conn.execute('''INSERT INTO ka.document_sources(project_id,draft_id,kind,source_document_id,source_version_id,
            source_title,source_version) VALUES (%s,%s,'read',%s,%s,'Overview',1)''',
            (docs.project, draft['id'], overview, version_of(docs, overview)))
    promoted = promote(docs, draft)
    assert promoted.status_code == 200, promoted.text
    result = promoted.json()
    assert (result['document_id'], result['revision'], result['export_error']) == (docs.runbook, 2, None)
    assert (result['draft']['status'], result['draft']['promoted_version_id']) == ('promoted', result['version_id'])
    document = docs.client.get(f'{API}/documents/{docs.runbook}', headers=HEADERS).json()
    assert (document['version'], document['slug'], document['description'], document['current_version']['markdown'],
            document['current_version']['origin']) == (2, 'runbook', 'How to run it', '# Runbook v2', 'manual')
    assert (docs.out / 'runbook' / 'README.md').read_text() == '# Runbook v2'
    with docs.store.connect() as conn:
        copied = conn.execute('''SELECT kind,source_title,source_version FROM ka.document_sources WHERE version_id=%s
            ORDER BY kind''', (UUID(result['version_id']),)).fetchall()
    assert copied == [{'kind': 'parent', 'source_title': 'Runbook', 'source_version': 1},
                      {'kind': 'read', 'source_title': 'Overview', 'source_version': 1}]
    assert audit(docs, draft['id']) == ['document.draft.created', 'document.draft.promoted']
    # Review focus 1: Promote clicked twice — one version, the second answers "already promoted".
    again = promote(docs, draft)
    assert again.status_code == 409 and again.json()['error']['message'] == DRAFT_DECIDED
    assert len(docs.client.get(f'{API}/documents/{docs.runbook}', headers=HEADERS).json()['versions']) == 2


def test_a_stale_draft_is_promoted_only_when_confirmed(docs):
    draft = new_draft(docs, document_id=docs.runbook, markdown='# Draft text')
    save_live(docs, docs.runbook, '# Runbook v2 by a person')
    assert docs.client.get(f"{API}/drafts/{draft['id']}", headers=HEADERS).json()['stale'] is True
    refused = promote(docs, draft)
    message = STALE_MESSAGE.format(base=1, live=2)
    assert message == "This draft was based on version 1; the live version is now 2. Promoting replaces version 2's text."
    assert refused.status_code == 409 and refused.json()['error'] == {'message': message, 'code': STALE_BASE}
    confirmed = promote(docs, draft, confirm_stale=True)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()['revision'] == 3
    assert docs.client.get(f'{API}/documents/{docs.runbook}', headers=HEADERS).json()['current_version']['markdown'] == '# Draft text'


def test_a_new_document_draft_creates_the_document_with_the_resources_that_still_exist(docs):
    server = docs.client.post('/api/servers', headers=HEADERS, json={'name': 'web-1', 'hostname': '10.0.0.7'}).json()
    gone = uuid4()
    with docs.store.connect() as conn:
        conn.execute("INSERT INTO ka.services(id,project_id,name) VALUES (%s,%s,'Billing')", (gone, docs.project))
    draft = new_draft(docs, slug='disk-layout', folder='servers/web-1', title='Disk layout', markdown='# Disks',
                      resource_ids={'server_ids': [server['id']], 'service_ids': [str(gone)]})
    with docs.store.connect() as conn:  # Review focus 2: a chosen resource is deleted before the promote
        conn.execute('DELETE FROM ka.services WHERE id=%s', (gone,))
    promoted = promote(docs, draft)
    assert promoted.status_code == 200, promoted.text
    created = docs.client.get(f"{API}/documents/{promoted.json()['document_id']}", headers=HEADERS).json()
    assert (created['slug'], created['folder'], created['title'], created['server_ids'], created['service_ids']) == (
        'disk-layout', 'servers/web-1', 'Disk layout', [server['id']], [])
    assert (docs.out / 'servers/web-1/disk-layout/README.md').read_text() == '# Disks'
    assert (promoted.json()['draft']['document_id'], promoted.json()['draft']['slug']) == (created['id'], None)


def test_a_new_document_whose_name_was_taken_meanwhile_becomes_a_draft_of_it(docs):
    draft = new_draft(docs, slug='overview', title='Overview draft', markdown='# Draft overview')
    created = docs.client.post(f'{API}/documents', headers=HEADERS, json={
        'slug': 'overview', 'title': 'Overview', 'markdown': '# Overview by a person'}).json()
    refused = promote(docs, draft)
    assert NAME_TAKEN.format(slug='overview') == ('A document named overview was created in this folder meanwhile. '
                                                 'The draft is now a draft of it. Review it again.')
    assert refused.status_code == 409 and refused.json()['error']['message'] == NAME_TAKEN.format(slug='overview')
    converted = docs.client.get(f"{API}/drafts/{draft['id']}", headers=HEADERS).json()
    assert (converted['document_id'], converted['slug'], converted['folder'], converted['revision'],
            converted['base_version'], converted['stale']) == (created['id'], None, None, 2, 1, False)
    assert converted['live_markdown'] == '# Overview by a person'
    assert [s['kind'] for s in converted['sources']] == ['parent']
    assert promote(docs, draft).status_code == 409  # its old revision
    promoted = promote(docs, converted)
    assert promoted.status_code == 200 and promoted.json()['revision'] == 2


def test_a_discarded_draft_is_kept_but_never_changes_again(docs):
    draft = new_draft(docs, document_id=docs.runbook)
    url = f"{API}/drafts/{draft['id']}"
    stale = docs.client.post(url + '/discard', headers=HEADERS, json={'revision': 2})
    assert stale.status_code == 409 and stale.json()['error']['message'] == DRAFT_CHANGED
    discarded = docs.client.post(url + '/discard', headers=HEADERS, json={'revision': 1})
    assert discarded.status_code == 200 and discarded.json()['status'] == 'discarded'
    for response in (docs.client.post(url + '/discard', headers=HEADERS, json={'revision': 2}),
                     docs.client.put(url, headers=HEADERS, json={'revision': 2, 'title': 'T', 'markdown': '# T'}),
                     promote(docs, discarded.json())):
        assert response.status_code == 409 and response.json()['error']['message'] == DRAFT_DECIDED
    assert docs.client.get(url, headers=HEADERS).json()['status'] == 'discarded'
    assert audit(docs, draft['id']) == ['document.draft.created', 'document.draft.discarded']
    assert docs.client.get(f'{API}/drafts/{uuid4()}', headers=HEADERS).status_code == 404


def test_promoting_a_draft_drops_the_documents_archived_code_project(docs):
    kept, archived = uuid4(), uuid4()
    with docs.store.connect() as conn:
        conn.execute('''INSERT INTO ka.repositories(id,project_id,slug,name) VALUES (%s,%s,'payments','Payments'),
            (%s,%s,'legacy','Legacy')''', (kept, docs.project, archived, docs.project))
    document_id = docs.client.post(f'{API}/documents', headers=HEADERS, json={
        'slug': 'payments', 'title': 'Payments', 'markdown': '# Payments',
        'code_project_ids': [str(kept), str(archived)]}).json()['id']
    draft = new_draft(docs, document_id=document_id, markdown='# Payments v2')
    with docs.store.connect() as conn:
        conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id=%s', (archived,))
    promoted = promote(docs, draft)
    assert promoted.status_code == 200, promoted.text
    document = docs.client.get(f'{API}/documents/{document_id}', headers=HEADERS).json()
    assert (document['version'], document['code_project_ids']) == (2, [str(kept)])


def test_an_agent_draft_is_promoted_as_generated_text_of_its_run(docs):
    with docs.store.connect() as conn:
        actor = conn.execute('SELECT id FROM ka.principals ORDER BY created_at LIMIT 1').fetchone()['id']
        session = add_ad_hoc_session(conn, docs.project, actor, a_model(conn, actor))
        draft_id = create_draft(conn, docs.project, None, {**NEW_DEFAULTS, 'title': 'Payments', 'markdown': '# Payments'},
                                origin='agent', slug='payments', folder='payments', session_id=session)
    draft = docs.client.get(f'{API}/drafts/{draft_id}', headers=HEADERS).json()
    assert (draft['origin'], draft['session_id'], draft['session_title'], draft['author']) == (
        'agent', str(session), 'Payments', None)
    promoted = promote(docs, draft)
    assert promoted.status_code == 200, promoted.text
    document = docs.client.get(f"{API}/documents/{promoted.json()['document_id']}", headers=HEADERS).json()
    assert (document['folder'], document['current_version']['origin']) == ('payments', 'generated')
    with docs.store.connect() as conn:
        assert conn.execute('SELECT document_id FROM ka.agent_sessions WHERE id=%s', (session,)).fetchone()['document_id'] == UUID(
            document['id'])
