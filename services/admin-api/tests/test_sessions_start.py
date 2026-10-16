"""Starting a run from a document through /api/sessions: the panel's options, Preview equal to what Start pins,
refusals, the audit, and the runs going with their document."""

from uuid import uuid4

from test_sessions_world import ask, keyless, post  # noqa: F401  (fixtures)
from test_generations import sessions  # noqa: F401  (fixture)
from test_repositories import HEADERS


def test_options_mark_the_documents_links_and_unusable_servers(ask):
    options = ask.s.client.get(f'/api/sessions/options?document_id={ask.document["id"]}', headers=HEADERS).json()
    servers = {item['name']: item for item in options['resources']['servers']}
    assert servers['web-1']['linked'] and servers['web-1']['usable']
    assert not servers['prod-1']['linked'] and servers['prod-1']['reason'] == 'SSH is not set up for this server.'
    assert [item['name'] for item in options['resources']['services'] if item['linked']] == ['Payments']
    assert options['default_prompt_id'] == next(p['id'] for p in options['prompts'] if p['name'] == 'Revise a document')
    assert options['resources']['documents'] == [
        {'id': ask.document['id'], 'name': 'Payments runbook', 'linked': True, 'editable': True}
    ]
    assert 'tools' not in options  # every run gets every tool
    assert ask.s.gpt in {item['id'] for item in options['models']}


def test_preview_is_exactly_what_start_pins(ask):
    preview = post(ask, '/preview')
    assert preview.status_code == 200, preview.text
    started = post(ask)
    assert started.status_code == 201, started.text
    session = started.json()
    assert (session['purpose'], session['target_document_id'], session['title']) == (
        'revision',
        ask.document['id'],
        'Add a section on log rotation.',
    )
    pinned = session['config']
    assert pinned['document_access'] == [{'id': ask.document['id'], 'title': 'Payments runbook', 'editable': True}]
    assert preview.json()['config'] == pinned
    assert preview.json()['first_message'] == ask.body['message']
    assert 'tools' not in pinned and pinned['experts'] == []  # every run gets every tool
    assert pinned['access']['roles'] == {'content_editor': True}  # a revision is a writing run
    assert 'You revise documents.' in pinned['system_prompt']['content']
    assert '# Context\n\nDocument: Payments runbook' in pinned['system_prompt']['content']
    assert '## Backup' not in pinned['system_prompt']['content']  # no document text or outline is appended
    assert 'bedrock-secret-token' not in started.text and 'PRIVATE KEY' not in started.text
    assert session['inputs'][0]['text'] == ask.body['message']


def test_start_refuses_what_cannot_run(ask):
    web = [ask.web['id']]
    for change, message in (
        ({'tools': ['read_file'], 'resources': {'server_ids': web}}, 'Extra inputs are not permitted'),
        ({'subagents': True}, 'Extra inputs are not permitted'),
        (
            {'expert_ids': ['00000000-0000-0000-0000-000000000001']},
            'This expert is not available: 00000000-0000-0000-0000-000000000001.',
        ),
    ):
        for path in ('/preview', ''):
            response = post(ask, path, **change)
            assert response.status_code == 400 and response.json()['error']['message'] == message, response.text


def test_start_refuses_an_unusable_server_a_disabled_model_and_a_missing_document(ask):
    prod = ask.s.server['id']
    assert post(ask, resources={'server_ids': [prod]}).json()['error']['message'] in (
        'Server prod-1: SSH is not set up for this server.',
        'Server prod-1: SSH is not allowed for this server.',
    )
    assert post(ask, model_id=str(uuid4())).json()['error']['message'] == 'Choose an enabled model.'
    gone = post(ask, started_from={'document_id': str(uuid4())})
    assert gone.status_code == 404 and gone.json()['error']['message'] == 'Document not found'


def test_start_is_audited(ask):
    assert post(ask).status_code == 201
    with ask.s.store.connect() as conn:
        details = conn.execute("SELECT details FROM ka.audit_events WHERE action='agent_run.created'").fetchone()[
            'details'
        ]
    assert details['document_id'] == ask.document['id'] and details['content_editor'] is True
    assert 'tools_off' not in details and 'subagents' not in details
    assert 'kind' not in details


def test_deleting_the_document_deletes_its_runs(ask):
    session = post(ask).json()
    current = ask.s.client.get(f'/api/documentation/documents/{ask.document["id"]}', headers=HEADERS).json()
    deleted = ask.s.client.delete(
        f'/api/documentation/documents/{ask.document["id"]}?revision={current["revision"]}&confirmed=true',
        headers=HEADERS,
    )
    assert deleted.status_code in (200, 204), deleted.text
    assert ask.s.client.get(f'/api/sessions/{session["id"]}', headers=HEADERS).status_code == 404


def test_a_server_without_a_key_brought_by_a_service_is_not_pinned(ask, keyless):
    change = {'resources': {'service_ids': [keyless.service]}}
    for path in ('/preview', ''):
        response = post(ask, path, **change)
        assert response.status_code in (200, 201), response.text
        config = response.json()['config']
        assert config['ssh']['servers'] == [] and not config['ssh']['enabled']
        assert keyless.server['id'] not in config['run_resources']['server_ids']


def test_options_say_what_each_service_and_environment_brings(ask, keyless):
    """brings_code: it resolves to a code project; brings_servers: to a server the run can use (enabled, SSH allowed, a
    key) — so the panel can say what each brings."""
    gateway, staging = uuid4(), uuid4()
    with ask.s.store.connect() as conn:
        project = ask.s.store.project(conn, 'shop')
        conn.execute("INSERT INTO ka.services(id,project_id,name) VALUES (%s,%s,'Gateway')", (gateway, project))
        conn.execute(
            """INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,runtime_name,port)
            VALUES (%s,%s,%s,'Staging','gateway',8080)""",
            (project, gateway, ask.web['id']),
        )
        conn.execute("INSERT INTO ka.environments(id,project_id,name) VALUES (%s,%s,'Staging')", (staging, project))
        conn.execute(
            'INSERT INTO ka.environment_servers(project_id,environment_id,server_id) VALUES (%s,%s,%s)',
            (project, staging, ask.web['id']),
        )
    options = ask.s.client.get(f'/api/sessions/options?document_id={ask.document["id"]}', headers=HEADERS).json()
    flags = {
        item['name']: (item['brings_code'], item['brings_servers'])
        for kind in ('services', 'environments')
        for item in options['resources'][kind]
    }
    # Payments: two code projects, deployed only on prod-1 (SSH not set up); Ledger: only bare-1 (no key).
    assert flags == {
        'Payments': (True, False),
        'Ledger': (False, False),
        'Gateway': (False, True),
        'Production': (True, False),
        'Staging': (False, True),
    }


def test_options_default_to_the_model_last_used_on_the_document(ask):
    url = f'/api/sessions/options?document_id={ask.document["id"]}'
    assert post(ask, model_id=ask.s.gpt).status_code == 201
    assert ask.s.client.get(url, headers=HEADERS).json()['default_model_id'] == ask.s.gpt


def test_start_says_document_not_found_when_the_document_goes_before_the_insert(ask, monkeypatch):
    from knowledge_admin.sessions import start

    real = start.build

    def build_then_delete(conn, project, value, session_id, conninfo):
        built = real(conn, project, value, session_id, conninfo)
        with ask.s.store.connect() as other:  # another transaction deletes the document after build() read it
            other.execute('DELETE FROM ka.documents WHERE id=%s', (ask.document['id'],))
        return built

    monkeypatch.setattr(start, 'build', build_then_delete)
    response = post(ask)
    assert response.status_code == 404 and response.json()['error']['message'] == 'Document not found', response.text


def test_no_starting_point_is_refused_for_an_ad_hoc_run(ask):
    for path in ('/preview', ''):
        response = post(ask, path, started_from=None)
        assert response.status_code == 400, response.text
        assert response.json()['error']['message'].endswith('Choose what the agent starts from.')


def test_the_editable_document_must_exist_and_twice_listed_documents_are_one_editable_entry(ask):
    gone = post(ask, resources={**ask.body['resources'], 'documents': [{'id': str(uuid4()), 'editable': True}]})
    assert gone.status_code == 404 and gone.json()['error']['message'] == 'Document not found'
    twice = [{'id': ask.document['id'], 'editable': False}, {'id': ask.document['id'], 'editable': True}]
    pinned = post(ask, '/preview', resources={**ask.body['resources'], 'documents': twice}).json()['config']
    assert pinned['document_access'] == [{'id': ask.document['id'], 'title': 'Payments runbook', 'editable': True}]


def test_nothing_but_the_context_is_appended_to_the_prompt(ask):
    pinned = post(ask, '/preview', context='').json()['config']
    assert pinned['system_prompt']['content'] == 'You revise documents.'


def test_a_draft_id_is_not_a_document_a_run_can_start_from_or_be_given(ask):
    draft = ask.s.client.post('/api/documentation/drafts', headers=HEADERS, json={'document_id': ask.document['id']})
    assert draft.status_code == 201, draft.text
    for change in (
        {'started_from': {'document_id': draft.json()['id']}},
        {'resources': {**ask.body['resources'], 'documents': [{'id': draft.json()['id'], 'editable': True}]}},
    ):
        refused = post(ask, '/preview', **change)
        assert refused.status_code == 404 and refused.json()['error']['message'] == 'Document not found', change
