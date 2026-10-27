"""Write with an agent (Documents → New → Write with an agent): an ad-hoc chat that starts with no document. It is
started from a resource (a chat on it: started_from that resource) or from nothing (the person picks the resources);
the agent creates documents with new_document for the run's resources and changes them with update_document and
edit_document. Its runs are purpose documents (the Write with an agent conversations)."""

import asyncio
import json
from uuid import uuid4

import pytest

from access_helpers import ids, of
from knowledge_admin.agents.runner import ssh_servers
from knowledge_admin.capabilities import inventory
from knowledge_admin.sessions.start import NEEDS_RESOURCE, ONE_START, one_start, write_prompt_id
from knowledge_admin.tools.catalog import CATALOG
from test_generations import sessions  # noqa: F401  (fixture)
from test_repositories import HEADERS


def body(s, started=None, **change):
    return {
        'started_from': {'new_document': True, **(started or {})},
        'message': 'Write the API reference.',
        'model_id': s.gpt,
        'system_prompt': 'You document systems.',
        'resources': {'code_project_ids': [s.repo]},
        **change,
    }


def start(s, started=None, **change) -> tuple[dict, dict]:
    """A write run: (session, its tool config)."""
    response = s.client.post('/api/sessions', headers=HEADERS, json=body(s, started, **change))
    assert response.status_code == 201, response.text
    session = response.json()
    return session, {**session['config'], 'session_id': session['id']}


def call(config, tool, **arguments):
    spec = CATALOG[tool]
    return asyncio.run(spec.run({'config': config}, spec.arguments.model_validate(arguments)))


def test_a_run_starts_from_one_place_and_writing_from_at_most_one_resource():
    assert one_start(document=True, new_document=False, resources=0)
    assert one_start(document=False, new_document=False, resources=1)
    assert not one_start(document=False, new_document=False, resources=0)
    assert not one_start(document=True, new_document=False, resources=1)
    assert not one_start(document=False, new_document=False, resources=2)
    assert one_start(document=False, new_document=True, resources=0)  # the resources are picked
    assert one_start(document=False, new_document=True, resources=1)  # a chat on that resource
    assert not one_start(document=True, new_document=True, resources=0)
    assert not one_start(document=False, new_document=True, resources=2)


def test_a_write_chat_without_a_resource_starts_with_the_picked_resources(sessions):
    session, config = start(sessions)
    assert (session['purpose'], session['target_document_id'], session['tags']) == ('documents', None, [])
    assert session['context_snapshot'] == {'new_document': True}
    assert 'document_name' not in session
    assert ids(config, 'code_project', 'given') == [sessions.repo]
    assert of(config, 'document') == [] and 'started_from' not in config
    assert '# Context' not in config['system_prompt']['content']  # no document named in it


def test_a_write_chat_without_a_resource_needs_a_picked_resource(sessions):
    response = sessions.client.post('/api/sessions', headers=HEADERS, json=body(sessions, resources={}))
    assert response.status_code == 400 and NEEDS_RESOURCE in response.json()['error']['message']


def test_a_write_chat_from_a_resource_is_a_chat_on_it(sessions):
    s = sessions
    session, config = start(s, {'service_id': s.service}, resources={})
    assert (session['purpose'], session['tags']) == ('documents', [f'services/{s.service}'])
    assert session['context_snapshot'] == {'new_document': True, 'service_id': s.service}
    assert config['started_from'] == {'type': 'service', 'id': s.service, 'name': 'Payments'}
    assert ids(config, 'service', 'given') == [s.service]
    assert sorted(ids(config, 'code_project')) == sorted([s.repo, s.worker])  # what it brings, as a chat on it


def test_a_write_chat_from_a_server_without_ssh_starts_with_its_server(sessions, monkeypatch):
    """A chat on a server needs SSH; writing about it does not: the run holds the server and cannot run commands."""
    s = sessions
    monkeypatch.setattr(inventory, '_store', s.store)
    session, config = start(s, {'server_id': s.server['id']}, resources={})
    assert ids(config, 'server', 'given') == [s.server['id']]
    assert ssh_servers(config) == []
    assert json.loads(call(config, 'get_resource', resource_type='server', resource_id=s.server['id']))['name'] == (
        'prod-1'
    )
    chat = {**body(s), 'started_from': {'server_id': s.server['id']}, 'resources': {}}
    assert s.client.post('/api/sessions', headers=HEADERS, json=chat).status_code == 400  # a chat still needs SSH


def test_the_agent_writes_new_documents_for_the_runs_resources(sessions, monkeypatch):
    s = sessions
    monkeypatch.setattr(inventory, '_store', s.store)
    _, config = start(s, {'service_id': s.service}, resources={})
    first = call(config, 'new_document', resource={'type': 'service', 'id': s.service}, title='Runbook', content='# R')
    second = call(config, 'new_document', resource={'type': 'code_project', 'id': s.repo}, title='API', content='# A')
    assert 'built for service Payments' in first and 'built for code project Payments API' in second


@pytest.mark.parametrize(
    'started',
    [
        {'document_id': str(uuid4())},
        {'service_id': str(uuid4()), 'server_id': str(uuid4())},
    ],
)
def test_a_write_chat_starts_from_at_most_one_resource_and_never_a_document(sessions, started):
    response = sessions.client.post('/api/sessions', headers=HEADERS, json=body(sessions, started))
    assert response.status_code == 400 and ONE_START in response.json()['error']['message']


@pytest.mark.parametrize(
    'change',
    [
        {'started_from': {'new_document': {'name': 'Payments'}}},  # the form-bound start is gone
        {'resources': {'code_project_ids': [], 'documents': [{'new': {'name': 'Payments'}}]}},
        {'started_from': {'new_document': False}},
    ],
)
def test_the_old_new_document_start_is_refused(sessions, change):
    response = sessions.client.post('/api/sessions', headers=HEADERS, json=body(sessions, **change))
    assert response.status_code == 400


def test_a_write_chat_from_a_missing_resource_is_refused(sessions):
    response = sessions.client.post(
        '/api/sessions', headers=HEADERS, json=body(sessions, {'service_id': str(uuid4())}, resources={})
    )
    assert (response.status_code, response.json()['error']['message']) == (404, 'Service not found.')


def test_options_for_writing_offer_the_write_prompts_with_or_without_a_resource(sessions):
    s = sessions
    with s.store.connect() as conn:
        seeded = str(write_prompt_id(s.store.project(conn, 'shop')))
    plain = s.client.get('/api/sessions/options?new_document=1', headers=HEADERS).json()
    assert plain['default_prompt_id'] == seeded and plain['discovery_prompts'] == []
    assert not any(item['linked'] for item in plain['resources']['services'])
    on = s.client.get(f'/api/sessions/options?new_document=1&service_id={s.service}', headers=HEADERS).json()
    assert on['default_prompt_id'] == seeded and on['discovery_prompts'] == []
    assert [item['id'] for item in on['resources']['services'] if item['linked']] == [s.service]


@pytest.mark.parametrize('query', [f'document_id={uuid4()}', f'service_id={uuid4()}&server_id={uuid4()}'])
def test_options_for_writing_take_at_most_one_resource(sessions, query):
    response = sessions.client.get(f'/api/sessions/options?new_document=1&{query}', headers=HEADERS)
    assert (response.status_code, response.json()['error']['message']) == (400, ONE_START)


def test_the_write_chats_of_a_resource_are_listed_apart_from_its_chats(sessions):
    s = sessions
    written, _ = start(s, {'service_id': s.service}, resources={})
    chat = {**body(s), 'started_from': {'service_id': s.service}, 'resources': {}}
    talked = s.client.post('/api/sessions', headers=HEADERS, json=chat).json()
    tag = f'services/{s.service}'

    def listed(purpose):
        found = s.client.get('/api/sessions', headers=HEADERS, params={'tag': tag, 'purpose': purpose}).json()
        return [item['id'] for item in found['sessions']]

    assert listed('documents') == [written['id']] and listed('resource') == [talked['id']]
