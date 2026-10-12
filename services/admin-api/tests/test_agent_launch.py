"""Starting an Ask agent run on a document through /agent-sessions: the panel's options, Preview equal to what Start
pins, refusals, the document's runs, the session routes, and the runs going with their document."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from knowledge_admin.agent_launch import DEFAULT_REVISION_PROMPT
from test_discovery_runs import ssh_ready
from test_generations import sessions  # noqa: F401  (fixture)
from test_repositories import HEADERS


@pytest.fixture
def ask(sessions):
    s = sessions
    web = s.client.post('/api/servers', headers=HEADERS, json={'name': 'web-1', 'hostname': '10.0.0.7', 'ssh_allowed': True}).json()
    ssh_ready(SimpleNamespace(client=s.client, server=web), web)
    document = s.client.post('/api/documentation/documents', headers=HEADERS, json={
        'slug': 'payments-runbook', 'title': 'Payments runbook', 'markdown': '# Payments runbook\n\n## Backup\n\nOld steps.',
        'service_ids': [s.service], 'server_ids': [web['id']]}).json()
    body = {'kind': 'revision', 'target': {'document_id': document['id']}, 'instruction': 'Add a section on log rotation.\nKeep it short.',
            'model_id': s.gpt, 'system_prompt': 'You revise documents.', 'resources': {'service_ids': [s.service], 'server_ids': [web['id']]},
            'tools': ['ssh_run', 'get_service_info'], 'expert_ids': [], 'subagents': False}
    return SimpleNamespace(s=s, web=web, document=document, body=body)


def post(ask, path='', **change):
    return ask.s.client.post(f'/api/agent-sessions{path}', headers=HEADERS, json={**ask.body, **change})


def test_options_mark_the_documents_links_and_unusable_servers(ask):
    options = ask.s.client.get(f"/api/agent-sessions/options?kind=revision&document_id={ask.document['id']}", headers=HEADERS).json()
    servers = {item['name']: item for item in options['resources']['servers']}
    assert servers['web-1']['linked'] and servers['web-1']['usable']
    assert not servers['prod-1']['linked'] and servers['prod-1']['reason'] == 'SSH is not set up for this server.'
    assert [item['name'] for item in options['resources']['services'] if item['linked']] == ['Payments']
    assert options['default_prompt'] == {'id': None, 'name': 'Built-in', 'content': DEFAULT_REVISION_PROMPT}
    assert [item['name'] for item in options['fixed_tools']] == ['get_doc', 'save_doc', 'edit_doc']
    tools = {item['name']: item for item in options['tools']}
    assert tools['ssh_run']['requires'] == 'at least one server' and 'run_subagent' not in tools and 'propose_service' not in tools
    assert ask.s.gpt in {item['id'] for item in options['models']}


def test_preview_is_exactly_what_start_pins(ask):
    preview = post(ask, '/preview')
    assert preview.status_code == 200, preview.text
    started = post(ask)
    assert started.status_code == 201, started.text
    session = started.json()
    assert (session['purpose'], session['target_document_id'], session['title']) == (
        'revision', ask.document['id'], 'Add a section on log rotation.')
    pinned = session['config']
    assert pinned['revision'] == {'session_id': session['id'], 'document_id': ask.document['id']}
    assert {**preview.json()['config'], 'revision': pinned['revision']} == pinned
    assert preview.json()['first_message'] == ask.body['instruction']
    modes = {item['tool']: item for item in pinned['tools']['items']}
    assert all(modes[name]['mode'] == 'on' and not modes[name]['subagent_allowed'] for name in ('get_doc', 'save_doc', 'edit_doc'))
    assert modes['ssh_run']['mode'] == 'on' and modes['get_server_info_basic']['mode'] == 'off'
    assert modes['run_subagent']['mode'] == 'off' and pinned['experts'] == []
    assert 'You revise documents.' in pinned['system_prompt']['content'] and '## Backup' in pinned['system_prompt']['content']
    assert 'Old steps.' not in pinned['system_prompt']['content']  # the outline, not the text
    assert 'bedrock-secret-token' not in started.text and 'PRIVATE KEY' not in started.text
    assert session['inputs'][0]['text'] == ask.body['instruction']


def test_start_refuses_what_cannot_run(ask):
    web = [ask.web['id']]
    for change, message in (
            ({'tools': ['read_file'], 'resources': {'server_ids': web}}, 'read_file needs a code project or a collection.'),
            # Payments is deployed only on prod-1, whose SSH is not set up, so it brings no server.
            ({'tools': ['ssh_run'], 'resources': {'service_ids': [ask.s.service]}}, 'ssh_run needs at least one server.'),
            ({'tools': ['nope']}, 'Unknown tool: nope.'),
            ({'expert_ids': ['00000000-0000-0000-0000-000000000001']},
             'This expert is not available: 00000000-0000-0000-0000-000000000001.')):
        for path in ('/preview', ''):
            response = post(ask, path, **change)
            assert response.status_code == 400 and response.json()['error']['message'] == message, response.text


def test_start_refuses_an_unusable_server_a_disabled_model_and_a_missing_document(ask):
    prod = ask.s.server['id']
    assert post(ask, resources={'server_ids': [prod]}).json()['error']['message'] in (
        'Server prod-1: SSH is not set up for this server.', 'Server prod-1: SSH is not allowed for this server.')
    assert post(ask, model_id=str(uuid4())).json()['error']['message'] == 'Choose an enabled model.'
    gone = post(ask, target={'document_id': str(uuid4())})
    assert gone.status_code == 404


def test_the_documents_runs_are_listed_open_first_and_use_the_session_routes(ask):
    first = post(ask).json()
    second = post(ask, instruction='Remove the old backup steps.').json()
    assert ask.s.client.post(f"/api/generations/sessions/{first['id']}/close", headers=HEADERS).status_code == 200
    listed = ask.s.client.get(f"/api/agent-sessions?document_id={ask.document['id']}", headers=HEADERS).json()['sessions']
    assert [row['id'] for row in listed] == [second['id'], first['id']]
    assert listed[0]['title'] == 'Remove the old backup steps.' and listed[0]['draft_id'] is None
    sent = ask.s.client.post(f"/api/generations/sessions/{second['id']}/messages", headers=HEADERS, json={'text': 'Also mention cron.'})
    assert sent.status_code == 202, sent.text
    with ask.s.store.connect() as conn:
        actions = [row['action'] for row in conn.execute("SELECT action,details FROM ka.audit_events WHERE action='agent_run.created'")]
        details = conn.execute("SELECT details FROM ka.audit_events WHERE action='agent_run.created' ORDER BY id LIMIT 1").fetchone()['details']
    assert actions == ['agent_run.created'] * 2
    assert details['kind'] == 'revision' and details['document_id'] == ask.document['id'] and 'get_server_info_basic' in details['tools_off']


def test_deleting_the_document_deletes_its_runs(ask):
    session = post(ask).json()
    current = ask.s.client.get(f"/api/documentation/documents/{ask.document['id']}", headers=HEADERS).json()
    deleted = ask.s.client.delete(f"/api/documentation/documents/{ask.document['id']}?revision={current['revision']}&confirmed=true",
                                  headers=HEADERS)
    assert deleted.status_code in (200, 204), deleted.text
    assert ask.s.client.get(f"/api/generations/sessions/{session['id']}", headers=HEADERS).status_code == 404
