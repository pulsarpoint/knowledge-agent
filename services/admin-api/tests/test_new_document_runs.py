"""Writing a new document with an agent: a new document is a resource the run may edit; the agent writes it with
save_document; the first save creates the new-document draft."""

import asyncio, json
from uuid import UUID, uuid4

import pytest

from knowledge_admin.capabilities import inventory
from knowledge_admin.capabilities.context import NOT_AVAILABLE
from knowledge_admin.capabilities.document_tools import _run_links
from knowledge_admin.data_root import FOLDER_RULE
from live_documents import live_document, replace_live_document
from knowledge_admin.sessions.start import ONE_START
from knowledge_admin.tools.catalog import CATALOG
from test_generations import sessions  # noqa: F401  (fixture)
from test_repositories import HEADERS

NEW = {'folder': 'payments-api-docs', 'name': 'Payments – API reference'}
COLUMNS = (
    'draft_of AS document_id,edited,origin,markdown,title,description,revision,base_version,folder,slug,session_id'
)


def body(s, **change):
    return {
        'started_from': {'new_document': NEW},
        'message': 'Write the API reference.',
        'model_id': s.gpt,
        'system_prompt': 'You document systems.',
        'resources': {'code_project_ids': [s.repo], 'documents': [{'new': NEW, 'editable': True}]},
        'tools': ['read_file', 'get_document', 'save_document', 'edit_document'],
        **change,
    }


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
        return conn.execute(
            f'SELECT {COLUMNS} FROM ka.documents WHERE is_draft AND session_id=%s ORDER BY created_at,id',
            (UUID(str(session_id)),),
        ).fetchall()


def all_drafts(s) -> int:
    with s.store.connect() as conn:
        return conn.execute('SELECT count(*) AS n FROM ka.documents WHERE is_draft').fetchone()['n']


def test_a_new_document_start_pins_a_new_entry_and_records_purpose_documents(sessions):
    started = sessions.client.post('/api/sessions', headers=HEADERS, json=body(sessions))
    assert started.status_code == 201, started.text
    session = started.json()
    assert (session['purpose'], session['folder'], session['document_name'], session['target_document_id']) == (
        'documents',
        'payments-api-docs',
        'Payments – API reference',
        None,
    )
    [entry] = session['config']['document_access']
    assert (
        entry['id'].startswith('new:')
        and entry['editable']
        and entry['new']
        and entry['slug'] == 'payments-api-reference'
    )
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
    first = {
        'document_id': None,
        'edited': False,
        'origin': 'generated',
        'markdown': '# API\n\nFirst.',
        'title': 'Payments – API reference',
        'description': '',
        'revision': 1,
        'base_version': None,
        'folder': 'payments-api-docs',
        'slug': 'payments-api-reference',
        'session_id': run,
    }
    assert drafts(s, run) == [first]
    assert (
        call(
            config,
            'save_document',
            document_id=new_id,
            markdown='# API\n\nSecond.',
            title='API reference',
            description='Endpoints',
        )
        == 'Saved draft (3 lines).'
    )
    assert drafts(s, run) == [
        {**first, 'markdown': '# API\n\nSecond.', 'title': 'API reference', 'description': 'Endpoints', 'revision': 2}
    ]
    assert (
        call(config, 'edit_document', document_id=new_id, old_text='Second.', new_text='Third.')
        == 'Saved draft (3 lines).'
    )
    assert drafts(s, run) == [
        {**first, 'markdown': '# API\n\nThird.', 'title': 'API reference', 'description': 'Endpoints', 'revision': 3}
    ]
    read = json.loads(call(config, 'get_document', document_id=new_id))
    assert (read['id'], read['title'], read['reads'], read['markdown']) == (
        new_id,
        'API reference',
        'your draft',
        '# API\n\nThird.',
    )


def test_an_existing_documents_title_and_folder_make_the_saves_a_draft_of_it(sessions, monkeypatch):
    s = sessions
    monkeypatch.setattr(inventory, '_store', s.store)
    with s.store.connect() as conn:
        project = s.store.project(conn, 'shop')
        saved = live_document(
            conn,
            project,
            'payments-api-reference',
            'Payments API',
            '# Old',
            folder='payments-api-docs',
            description='The public API',
            purpose='reference',
            audience='people',
            tags=['payments'],
        )
        document, version = saved['id'], saved['version']
    session, config, new_id = started(s)
    run = UUID(session['id'])
    assert call(config, 'get_document', document_id=new_id) == 'Not written yet.'
    with pytest.raises(ValueError, match='Write the document with save_document first.'):
        call(config, 'edit_document', document_id=new_id, old_text='Old', new_text='New')
    assert call(config, 'save_document', document_id=new_id, markdown='# New') == 'Saved draft (1 lines).'
    first = {
        'document_id': UUID(str(document)),
        'edited': False,
        'origin': 'generated',
        'markdown': '# New',
        'title': 'Payments API',
        'description': 'The public API',
        'revision': 1,
        'base_version': version,
        'folder': None,
        'slug': None,
        'session_id': run,
    }
    assert drafts(s, run) == [first]
    assert metadata(s, run) == [('reference', 'people', ['payments'])]
    assert call(config, 'save_document', document_id=new_id, markdown='# Newer') == 'Saved draft (1 lines).'
    assert drafts(s, run) == [{**first, 'markdown': '# Newer', 'revision': 2}]
    assert json.loads(call(config, 'get_document', document_id=new_id))['reads'] == 'your draft'


def metadata(s, session_id) -> list[tuple]:
    with s.store.connect() as conn:
        return [
            (row['purpose'], row['audience'], row['tags'])
            for row in conn.execute(
                'SELECT purpose,audience,tags FROM ka.documents WHERE is_draft AND session_id=%s ORDER BY created_at,id',
                (UUID(str(session_id)),),
            ).fetchall()
        ]


def promote(s, draft_id) -> dict:
    draft = s.client.get(f'/api/documentation/drafts/{draft_id}', headers=HEADERS).json()
    promoted = s.client.post(
        f'/api/documentation/drafts/{draft_id}/promote', headers=HEADERS, json={'revision': draft['revision']}
    )
    assert promoted.status_code == 200, promoted.text
    return promoted.json()


def draft_ids(s, session_id) -> list[UUID]:
    with s.store.connect() as conn:
        return [
            row['id']
            for row in conn.execute(
                'SELECT id FROM ka.documents WHERE is_draft AND session_id=%s ORDER BY created_at,id',
                (UUID(str(session_id)),),
            )
        ]


def test_a_save_after_a_person_promoted_the_new_document_drafts_the_promoted_document(sessions, monkeypatch):
    s = sessions
    monkeypatch.setattr(inventory, '_store', s.store)
    session, config, new_id = started(s)
    run = UUID(session['id'])
    assert (
        call(
            config,
            'save_document',
            document_id=new_id,
            markdown='# API',
            title='API reference',
            description='Endpoints',
        )
        == 'Saved draft (1 lines).'
    )
    [draft] = draft_ids(s, run)
    document = UUID(str(promote(s, draft)['document_id']))
    assert document == draft  # a brand-new draft becomes the document itself
    with s.store.connect() as conn:  # a person sets its metadata after the promote
        replace_live_document(
            conn,
            document,
            '# API',
            links={'code_project_ids': [s.repo]},
            title='API reference',
            description='Endpoints',
            purpose='reference',
            audience='people',
            tags=['payments'],
        )
        version = conn.execute('SELECT version FROM ka.documents WHERE id=%s', (document,)).fetchone()
    assert call(config, 'save_document', document_id=new_id, markdown='# API v2') == (
        f'Saved draft (1 lines). Your earlier draft was promoted; this is a new draft based on version {version["version"]}.'
    )
    [row] = drafts(s, run)  # the promoted draft is the live document now
    assert {
        key: row[key] for key in ('document_id', 'base_version', 'title', 'description', 'markdown', 'folder', 'slug')
    } == {
        'document_id': document,
        'base_version': version['version'],
        'title': 'API reference',
        'description': 'Endpoints',
        'markdown': '# API v2',
        'folder': None,
        'slug': None,
    }
    assert metadata(s, run) == [('reference', 'people', ['payments'])]
    assert json.loads(call(config, 'get_document', document_id=new_id))['markdown'] == '# API v2'


def test_a_promoted_new_document_links_the_resources_chosen_for_the_run(sessions, monkeypatch):
    s = sessions
    monkeypatch.setattr(inventory, '_store', s.store)
    response = s.client.post(
        '/api/sessions',
        headers=HEADERS,
        json=body(
            s,
            resources={
                'code_project_ids': [s.repo],
                'service_ids': [s.service],
                'documents': [{'new': NEW, 'editable': True}],
            },
        ),
    )
    assert response.status_code == 201, response.text
    session = response.json()
    config = {**session['config'], 'session_id': session['id']}
    new_id = session['config']['document_access'][0]['id']
    assert call(config, 'save_document', document_id=new_id, markdown='# API') == 'Saved draft (1 lines).'
    [draft] = draft_ids(s, session['id'])
    created = s.client.get(f'/api/documentation/documents/{promote(s, draft)["document_id"]}', headers=HEADERS).json()
    assert created['code_project_ids'] == [s.repo] and created['service_ids'] == [s.service]
    assert created['server_ids'] == [] and created['environment_ids'] == []  # the worker Payments brought: not linked


def resource_ids(s, draft_id) -> dict:
    """The brand-new draft's built-for links as resource_ids."""
    with s.store.connect() as conn:
        row = conn.execute(
            """SELECT code_project_ids,environment_ids,service_ids,server_ids,deployment_ids
            FROM ka.document_drafts_view WHERE id=%s""",
            (draft_id,),
        ).fetchone()
        assert {
            r['connection']
            for r in conn.execute('SELECT connection FROM ka.document_resources WHERE document_id=%s', (draft_id,))
        } == {'built_for'}
    return {key: [str(item) for item in value] for key, value in row.items()}


def test_a_new_document_links_the_chosen_environment_and_not_what_it_brings(sessions, monkeypatch):
    s = sessions  # Production brings Payments, its code projects (Payments API, Payments worker) and prod-1
    monkeypatch.setattr(inventory, '_store', s.store)
    resources = {
        'environment_ids': [s.environment],
        'code_project_ids': [s.repo],
        'documents': [{'new': NEW, 'editable': True}],
    }
    response = s.client.post('/api/sessions', headers=HEADERS, json=body(s, resources=resources))
    assert response.status_code == 201, response.text
    session = response.json()
    assert session['run_resources']['service_ids'] == [s.service]  # the tools still reach what it brought
    assert sorted(session['run_resources']['code_project_ids']) == sorted([s.repo, s.worker])
    config = {**session['config'], 'session_id': session['id']}
    new_id = session['config']['document_access'][0]['id']
    assert call(config, 'save_document', document_id=new_id, markdown='# API') == 'Saved draft (1 lines).'
    [draft] = draft_ids(s, session['id'])
    assert resource_ids(s, draft) == {
        'code_project_ids': [s.repo],
        'environment_ids': [s.environment],
        'service_ids': [],
        'server_ids': [],
        'deployment_ids': [],
    }
    created = s.client.get(f'/api/documentation/documents/{promote(s, draft)["document_id"]}', headers=HEADERS).json()
    assert {key: created[key] for key in ('environment_ids', 'code_project_ids', 'service_ids', 'server_ids')} == {
        'environment_ids': [s.environment],
        'code_project_ids': [s.repo],
        'service_ids': [],
        'server_ids': [],
    }


def test_a_run_without_its_chosen_resources_links_what_it_was_given():
    # a session started before chosen_resources was recorded links its run_resources, as it did
    given = {'code_project_ids': ['p'], 'collection_ids': ['c'], 'server_ids': ['h'], 'service_ids': ['s']}
    assert _run_links({'run_resources': given}) == {
        'code_project_ids': ['p'],
        'environment_ids': [],
        'service_ids': ['s'],
        'server_ids': ['h'],
        'deployment_ids': [],
    }
    chosen = {**given, 'server_ids': [], 'service_ids': [], 'environment_ids': ['e']}
    assert _run_links({'run_resources': given, 'chosen_resources': chosen})['environment_ids'] == ['e']
    assert _run_links({}) == {
        key: [] for key in ('code_project_ids', 'environment_ids', 'service_ids', 'server_ids', 'deployment_ids')
    }


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


@pytest.mark.parametrize(
    'change,message',
    [
        (
            {'started_from': {'new_document': NEW, 'document_id': str(uuid4())}},
            'Choose one starting point: a document, a new document, a server, a service or an environment.',
        ),
        ({'started_from': {'new_document': {**NEW, 'name': '!!!'}}}, 'Use letters or digits in the document name'),
        ({'started_from': {'new_document': {**NEW, 'folder': '../docs'}}}, FOLDER_RULE),
        ({'started_from': {'new_document': {**NEW, 'folder': '/docs'}}}, FOLDER_RULE),
        ({'resources': {'documents': [{'new': NEW}, {'new': {**NEW, 'name': 'Other'}}]}}, 'One new document per run.'),
        (
            {'resources': {'documents': [{'new': {**NEW, 'name': 'Other'}}]}},
            'The new document must be the one the run starts from.',
        ),
    ],
)
def test_bad_new_document_starts_are_refused(sessions, change, message):
    response = sessions.client.post('/api/sessions', headers=HEADERS, json=body(sessions, **change))
    assert response.status_code == 400 and message in response.json()['error']['message']


def test_options_for_a_new_document(sessions):
    options = sessions.client.get('/api/sessions/options?new_document=1', headers=HEADERS).json()
    assert options['resources']['documents'] == [] and all(
        not item['linked'] for item in options['resources']['services']
    )
    assert {'save_document', 'edit_document', 'get_document'} <= {tool['name'] for tool in options['tools']}


def test_options_for_a_new_document_and_a_document_are_refused(sessions):
    response = sessions.client.get(f'/api/sessions/options?new_document=1&document_id={uuid4()}', headers=HEADERS)
    assert response.status_code == 400 and response.json()['error']['message'] == ONE_START


def test_two_runs_cannot_draft_the_same_new_document(sessions, monkeypatch):
    s = sessions
    monkeypatch.setattr(inventory, '_store', s.store)
    _, config, new_id = started(s)
    second, other, other_id = started(s)
    assert call(config, 'save_document', document_id=new_id, markdown='# Mine') == 'Saved draft (1 lines).'
    with pytest.raises(ValueError) as refused:
        call(other, 'save_document', document_id=other_id, markdown='# Theirs')
    assert str(refused.value) == (
        'A document or draft named payments-api-reference already exists in this folder. Choose another name.'
    )
    assert drafts(s, second['id']) == [] and all_drafts(s) == 1


def test_a_new_document_run_is_refused_when_a_brand_new_draft_holds_its_name(sessions, monkeypatch):
    s = sessions
    monkeypatch.setattr(inventory, '_store', s.store)
    session, config, new_id = started(s)
    call(config, 'save_document', document_id=new_id, markdown='# Mine')
    [draft] = draft_ids(s, session['id'])
    with s.store.connect() as conn:
        before = conn.execute('SELECT count(*) AS n FROM ka.agent_sessions').fetchone()['n']
    response = s.client.post('/api/sessions', headers=HEADERS, json=body(s))
    assert response.status_code == 409
    assert response.json()['error']['message'] == (
        'A document or draft named payments-api-reference already exists in this folder. Choose another name.'
    )
    with s.store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions').fetchone()['n'] == before
    # once promoted, the name is a live document's: a run starts and drafts that document
    promote(s, draft)
    again, other, other_id = started(s)
    assert call(other, 'save_document', document_id=other_id, markdown='# Next') == 'Saved draft (1 lines).'
    [row] = drafts(s, again['id'])
    assert (row['document_id'], row['base_version'], row['slug']) == (draft, 1, None)


def edit_by_person(s, draft_id) -> dict:
    draft = s.client.get(f'/api/documentation/drafts/{draft_id}', headers=HEADERS).json()
    edited = s.client.put(
        f'/api/documentation/drafts/{draft_id}',
        headers=HEADERS,
        json={'revision': draft['revision'], 'title': draft['title'], 'markdown': '# Edited by a person'},
    )
    assert edited.status_code == 200, edited.text
    return edited.json()


@pytest.mark.parametrize('decision', ['promote', 'discard'])
def test_a_brand_new_draft_a_person_edited_is_theirs_until_they_decide(sessions, monkeypatch, decision):
    s = sessions
    monkeypatch.setattr(inventory, '_store', s.store)
    session, config, new_id = started(s)
    call(config, 'save_document', document_id=new_id, markdown='# API', title='API reference')
    [draft] = draft_ids(s, session['id'])
    edited = edit_by_person(s, draft)
    for tool, arguments in (
        ('save_document', {'markdown': '# Mine'}),
        ('edit_document', {'old_text': 'A', 'new_text': 'B'}),
    ):
        with pytest.raises(ValueError) as refused:
            call(config, tool, document_id=new_id, **arguments)
        assert str(refused.value) == (
            'A person edited your draft of API reference; it is theirs now. '
            'Save again after they promote or discard it.'
        )
    assert draft_ids(s, session['id']) == [draft]
    if decision == 'promote':
        promote(s, draft)
        assert call(config, 'save_document', document_id=new_id, markdown='# API v2') == (
            'Saved draft (1 lines). Your earlier draft was promoted; this is a new draft based on version 1.'
        )
        [row] = drafts(s, session['id'])
        assert (row['document_id'], row['base_version'], row['slug'], row['markdown']) == (draft, 1, None, '# API v2')
    else:
        url = f'/api/documentation/drafts/{draft}/discard'
        assert s.client.post(url, headers=HEADERS, json={'revision': edited['revision']}).status_code == 200
        assert call(config, 'save_document', document_id=new_id, markdown='# Fresh') == 'Saved draft (1 lines).'
        [row] = drafts(s, session['id'])
        assert (row['document_id'], row['slug'], row['markdown']) == (None, 'payments-api-reference', '# Fresh')
