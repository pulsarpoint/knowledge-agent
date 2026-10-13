"""Writing a new document with an agent: a new document is a resource the run may edit; the agent writes it with
save_document; the first save creates the new-document draft."""
import asyncio, json
from uuid import UUID, uuid4

import pytest

from knowledge_admin.capabilities import inventory
from knowledge_admin.capabilities.context import NOT_AVAILABLE
from knowledge_admin.data_root import FOLDER_RULE
from knowledge_admin.documents import DocumentInput, save_document
from knowledge_admin.sessions.start import ONE_START
from knowledge_admin.tools.catalog import CATALOG
from test_generations import sessions  # noqa: F401  (fixture)
from test_repositories import HEADERS

NEW = {'folder': 'payments-api-docs', 'name': 'Payments – API reference'}
COLUMNS = 'document_id,status,edited,origin,markdown,title,description,revision,base_version_id,folder,slug,session_id'


def body(s, **change):
    return {'started_from': {'new_document': NEW}, 'message': 'Write the API reference.', 'model_id': s.gpt,
            'system_prompt': 'You document systems.', 'resources': {'code_project_ids': [s.repo], 'documents': [{'new': NEW, 'editable': True}]},
            'tools': ['read_file', 'get_document', 'save_document', 'edit_document'], **change}


def call(config, tool, **arguments):
    spec = CATALOG[tool]
    return asyncio.run(spec.run({'config': config}, spec.arguments.model_validate(arguments)))


def started(s) -> tuple[dict, dict, str]:
    """A new-document run: (session, its tool config, the pinned new document's id)."""
    response = s.client.post('/api/sessions', headers=HEADERS, json=body(s))
    assert response.status_code == 201, response.text
    session = response.json()
    config = {**session['config'], 'session_id': session['id']}
    return session, config, session['config']['document_access'][0]['id']


def drafts(s, session_id) -> list[dict]:
    with s.store.connect() as conn:
        return conn.execute(f'SELECT {COLUMNS} FROM ka.document_drafts WHERE session_id=%s ORDER BY created_at,id',
                            (UUID(str(session_id)),)).fetchall()


def all_drafts(s) -> int:
    with s.store.connect() as conn:
        return conn.execute('SELECT count(*) AS n FROM ka.document_drafts').fetchone()['n']


def test_a_new_document_start_pins_a_new_entry_and_records_purpose_documents(sessions):
    started = sessions.client.post('/api/sessions', headers=HEADERS, json=body(sessions))
    assert started.status_code == 201, started.text
    session = started.json()
    assert (session['purpose'], session['folder'], session['document_name'], session['target_document_id']) == (
        'documents', 'payments-api-docs', 'Payments – API reference', None)
    [entry] = session['config']['document_access']
    assert entry['id'].startswith('new:') and entry['editable'] and entry['new'] and entry['slug'] == 'payments-api-reference'
    assert session['context_snapshot'] == {'new_document': NEW}


def test_the_first_save_creates_the_new_document_draft_and_later_saves_update_it(sessions, monkeypatch):
    s = sessions
    monkeypatch.setattr(inventory, '_store', s.store)
    session, config, new_id = started(s)
    assert call(config, 'get_document', document_id=new_id) == 'Not written yet.'
    with pytest.raises(ValueError, match='Write the document with save_document first.'):
        call(config, 'edit_document', document_id=new_id, old_text='a', new_text='b')
    assert drafts(s, session['id']) == []
    assert call(config, 'save_document', document_id=new_id, markdown='# API\n\nFirst.') == 'Saved draft (3 lines).'
    run = UUID(session['id'])
    first = {'document_id': None, 'status': 'open', 'edited': False, 'origin': 'agent', 'markdown': '# API\n\nFirst.',
             'title': 'Payments – API reference', 'description': '', 'revision': 1, 'base_version_id': None,
             'folder': 'payments-api-docs', 'slug': 'payments-api-reference', 'session_id': run}
    assert drafts(s, run) == [first]
    assert call(config, 'save_document', document_id=new_id, markdown='# API\n\nSecond.', title='API reference',
                description='Endpoints') == 'Saved draft (3 lines).'
    assert drafts(s, run) == [{**first, 'markdown': '# API\n\nSecond.', 'title': 'API reference', 'description': 'Endpoints',
                               'revision': 2}]
    assert call(config, 'edit_document', document_id=new_id, old_text='Second.', new_text='Third.') == 'Saved draft (3 lines).'
    assert drafts(s, run) == [{**first, 'markdown': '# API\n\nThird.', 'title': 'API reference', 'description': 'Endpoints',
                               'revision': 3}]
    read = json.loads(call(config, 'get_document', document_id=new_id))
    assert (read['id'], read['title'], read['reads'], read['markdown']) == (new_id, 'API reference', 'your draft', '# API\n\nThird.')


def test_an_existing_documents_title_and_folder_make_the_saves_a_draft_of_it(sessions, monkeypatch):
    s = sessions
    monkeypatch.setattr(inventory, '_store', s.store)
    with s.store.connect() as conn:
        project = s.store.project(conn, 'shop')
        saved = save_document(conn, project, None, DocumentInput(
            slug='payments-api-reference', title='Payments API', description='The public API', purpose='reference',
            audience='people', tags=['payments'], markdown='# Old'), folder='payments-api-docs')
        document, version = saved['id'], conn.execute('SELECT current_version_id FROM ka.documents WHERE id=%s',
                                                       (saved['id'],)).fetchone()['current_version_id']
    session, config, new_id = started(s)
    run = UUID(session['id'])
    assert call(config, 'get_document', document_id=new_id) == 'Not written yet.'
    with pytest.raises(ValueError, match='Write the document with save_document first.'):
        call(config, 'edit_document', document_id=new_id, old_text='Old', new_text='New')
    assert call(config, 'save_document', document_id=new_id, markdown='# New') == 'Saved draft (1 lines).'
    first = {'document_id': UUID(str(document)), 'status': 'open', 'edited': False, 'origin': 'agent', 'markdown': '# New',
             'title': 'Payments API', 'description': 'The public API', 'revision': 1, 'base_version_id': version,
             'folder': None, 'slug': None, 'session_id': run}
    assert drafts(s, run) == [first]
    assert metadata(s, run) == [('reference', 'people', ['payments'])]
    assert call(config, 'save_document', document_id=new_id, markdown='# Newer') == 'Saved draft (1 lines).'
    assert drafts(s, run) == [{**first, 'markdown': '# Newer', 'revision': 2}]
    assert json.loads(call(config, 'get_document', document_id=new_id))['reads'] == 'your draft'


def metadata(s, session_id) -> list[tuple]:
    with s.store.connect() as conn:
        return [(row['purpose'], row['audience'], row['tags']) for row in conn.execute(
            'SELECT purpose,audience,tags FROM ka.document_drafts WHERE session_id=%s ORDER BY created_at,id',
            (UUID(str(session_id)),)).fetchall()]


def promote(s, draft_id) -> dict:
    draft = s.client.get(f'/api/documentation/drafts/{draft_id}', headers=HEADERS).json()
    promoted = s.client.post(f'/api/documentation/drafts/{draft_id}/promote', headers=HEADERS,
                             json={'revision': draft['revision']})
    assert promoted.status_code == 200, promoted.text
    return promoted.json()


def draft_ids(s, session_id) -> list[UUID]:
    with s.store.connect() as conn:
        return [row['id'] for row in conn.execute(
            'SELECT id FROM ka.document_drafts WHERE session_id=%s ORDER BY created_at,id', (UUID(str(session_id)),))]


def test_a_save_after_a_person_promoted_the_new_document_drafts_the_promoted_document(sessions, monkeypatch):
    s = sessions
    monkeypatch.setattr(inventory, '_store', s.store)
    session, config, new_id = started(s)
    run = UUID(session['id'])
    assert call(config, 'save_document', document_id=new_id, markdown='# API', title='API reference',
                description='Endpoints') == 'Saved draft (1 lines).'
    [draft] = draft_ids(s, run)
    document = UUID(str(promote(s, draft)['document_id']))
    with s.store.connect() as conn:  # a person sets its metadata after the promote
        project = s.store.project(conn, 'shop')
        live = conn.execute('SELECT revision FROM ka.document_details WHERE id=%s', (document,)).fetchone()
        save_document(conn, project, None, DocumentInput(
            slug='payments-api-reference', title='API reference', description='Endpoints', purpose='reference',
            audience='people', tags=['payments'], markdown='# API', revision=live['revision'],
            code_project_ids=[s.repo]), document)
        version = conn.execute('SELECT v.id,v.version FROM ka.documents d JOIN ka.document_versions v '
                               'ON v.id=d.current_version_id WHERE d.id=%s', (document,)).fetchone()
    assert call(config, 'save_document', document_id=new_id, markdown='# API v2') == (
        f"Saved draft (1 lines). Your earlier draft was promoted; this is a new draft based on version {version['version']}.")
    rows = drafts(s, run)
    assert [row['status'] for row in rows] == ['promoted', 'open']
    assert {key: rows[1][key] for key in ('document_id', 'base_version_id', 'title', 'description', 'markdown', 'folder', 'slug')} == {
        'document_id': document, 'base_version_id': version['id'], 'title': 'API reference', 'description': 'Endpoints',
        'markdown': '# API v2', 'folder': None, 'slug': None}
    assert metadata(s, run)[1] == ('reference', 'people', ['payments'])
    assert json.loads(call(config, 'get_document', document_id=new_id))['markdown'] == '# API v2'


def test_a_promoted_new_document_links_the_resources_chosen_for_the_run(sessions, monkeypatch):
    s = sessions
    monkeypatch.setattr(inventory, '_store', s.store)
    response = s.client.post('/api/sessions', headers=HEADERS, json=body(s, resources={
        'code_project_ids': [s.repo], 'service_ids': [s.service], 'documents': [{'new': NEW, 'editable': True}]}))
    assert response.status_code == 201, response.text
    session = response.json()
    config = {**session['config'], 'session_id': session['id']}
    new_id = session['config']['document_access'][0]['id']
    assert call(config, 'save_document', document_id=new_id, markdown='# API') == 'Saved draft (1 lines).'
    [draft] = draft_ids(s, session['id'])
    created = s.client.get(f"/api/documentation/documents/{promote(s, draft)['document_id']}", headers=HEADERS).json()
    assert s.repo in created['code_project_ids'] and created['service_ids'] == [s.service]


def test_no_other_new_id_can_be_written(sessions, monkeypatch):
    s = sessions
    monkeypatch.setattr(inventory, '_store', s.store)
    session, config, _ = started(s)
    _, _, other = started(s)
    for document_id in ('new:' + str(uuid4()), other):
        with pytest.raises(ValueError) as refused:
            call(config, 'save_document', document_id=document_id, markdown='# Mine')
        assert str(refused.value) == NOT_AVAILABLE.format(kind='document')
        assert call(config, 'get_document', document_id=document_id) == NOT_AVAILABLE.format(kind='document')
    assert drafts(s, session['id']) == [] and all_drafts(s) == 0


@pytest.mark.parametrize('change,message', [
    ({'started_from': {'new_document': NEW, 'document_id': str(uuid4())}}, 'Choose one starting point: a document, a new document or a server.'),
    ({'started_from': {'new_document': {**NEW, 'name': '!!!'}}}, 'Use letters or digits in the document name'),
    ({'started_from': {'new_document': {**NEW, 'folder': '../docs'}}}, FOLDER_RULE),
    ({'started_from': {'new_document': {**NEW, 'folder': '/docs'}}}, FOLDER_RULE),
    ({'resources': {'documents': [{'new': NEW}, {'new': {**NEW, 'name': 'Other'}}]}}, 'One new document per run.'),
    ({'resources': {'documents': [{'new': {**NEW, 'name': 'Other'}}]}}, 'The new document must be the one the run starts from.'),
])
def test_bad_new_document_starts_are_refused(sessions, change, message):
    response = sessions.client.post('/api/sessions', headers=HEADERS, json=body(sessions, **change))
    assert response.status_code == 400 and message in response.json()['error']['message']


def test_options_for_a_new_document(sessions):
    options = sessions.client.get('/api/sessions/options?new_document=1', headers=HEADERS).json()
    assert options['resources']['documents'] == [] and all(not item['linked'] for item in options['resources']['services'])
    assert {'save_document', 'edit_document', 'get_document'} <= {tool['name'] for tool in options['tools']}


def test_options_for_a_new_document_and_a_document_are_refused(sessions):
    response = sessions.client.get(f'/api/sessions/options?new_document=1&document_id={uuid4()}', headers=HEADERS)
    assert response.status_code == 400 and response.json()['error']['message'] == ONE_START
