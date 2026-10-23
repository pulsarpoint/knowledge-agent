"""The ad-hoc agent (Documents → New, Ask agent, chats on a resource): no agents row, the user's prompt and model, and
an access object of exactly what it was picked and what that brings, built as a saved agent's run is (run_access)."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from access_helpers import of
from knowledge_admin.agents.runner import load_credentials, ssh_servers
from knowledge_admin.knowledge_agents.config import AD_HOC_AGENT
from test_discovery_runs import ssh_ready
from test_generations import sessions  # noqa: F401  (fixture)
from test_operations import setup as ssh_setup
from test_repositories import HEADERS

NEW = {'folder': 'payments-api-docs', 'name': 'Payments – API reference'}


def preview(s, prompt='You document.', **resources):
    body = {
        'started_from': {'new_document': NEW},
        'message': 'Write it.',
        'model_id': s.gpt,
        'system_prompt': prompt,
        'resources': {**resources, 'documents': [{'new': NEW}]},
    }
    return s.client.post('/api/sessions/preview', headers=HEADERS, json=body)


def build(s, prompt='You document.', **resources) -> dict:
    response = preview(s, prompt, **resources)
    assert response.status_code == 200, response.text
    return response.json()['config']


def refusal(s, **resources) -> str:
    response = preview(s, **resources)
    assert response.status_code == 400, response.text
    return response.json()['error']['message']


def reached(config: dict, type_: str) -> list[str]:
    return [item['id'] for item in of(config, type_)]


def test_the_ad_hoc_agent_gets_exactly_the_selection(sessions):
    s = sessions
    config = build(s, code_project_ids=[s.repo])
    assert (config['id'], config['enabled']) == (AD_HOC_AGENT, True)
    assert config['system_prompt']['content'].startswith('You document.') and config['instructions'] == ''
    assert config['model']['id'] == s.gpt
    assert [(item['type'], item['name'], item['source']) for item in config['access']['resources']] == [
        ('code_project', 'Payments API', 'given'),
        ('document', NEW['name'], 'given'),
    ]
    for key in ('code', 'documents', 'ssh', 'services', 'skills', 'mcp', 'run_resources', 'chosen_resources', 'tools'):
        assert key not in config


def test_an_environment_brings_its_services_code_and_servers(sessions):
    s = sessions
    with s.store.connect() as conn:  # prod-1 gets a usable connection of its own
        conn.execute('UPDATE ka.servers SET ssh_allowed=true WHERE id=%s', (s.server['id'],))
    ssh_ready(SimpleNamespace(client=s.client, server=s.server))
    config = build(s, environment_ids=[s.environment])
    assert reached(config, 'environment') == [s.environment]
    assert reached(config, 'service') == [s.service]
    assert sorted(reached(config, 'code_project')) == sorted([s.repo, s.worker])
    assert reached(config, 'server') == [s.server['id']]
    assert {item['source'] for item in config['access']['resources'] if item['type'] != 'document'} == {
        'given',
        f'via environment {s.environment}',
        f'via service {s.service}',
    }


def test_a_brought_server_without_a_connection_is_held_but_a_chosen_one_is_refused(sessions):
    s = sessions  # prod-1 allows SSH but has no username or key: not enabled
    with s.store.connect() as conn:
        conn.execute('UPDATE ka.servers SET ssh_allowed=true WHERE id=%s', (s.server['id'],))
    config = build(s, environment_ids=[s.environment])
    assert reached(config, 'server') == [s.server['id']] and reached(config, 'service') == [s.service]
    assert ssh_servers(config) == []  # held and read; SSH skips it
    assert 'prod-1' in refusal(s, server_ids=[s.server['id']])


def test_environment_brings_only_usable_resources(sessions):
    s = sessions
    with s.store.connect() as conn:
        conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id=%s', (s.worker,))
        conn.execute('UPDATE ka.servers SET ssh_allowed=false WHERE id=%s', (s.server['id'],))
    config = build(s, environment_ids=[s.environment])
    assert reached(config, 'code_project') == [s.repo] and reached(config, 'server') == []


def test_a_chosen_server_keeps_its_own_connection_as_a_reference(sessions):
    s = sessions
    server, key = ssh_setup(s.client)
    config = build(s, server_ids=[server['id']])
    [pinned] = of(config, 'server')
    assert pinned['id'] == server['id'] and pinned['username'] == server['username']
    assert pinned['private_key_ref']['id'] and key['private_key'] not in json.dumps(config)


def test_what_cannot_be_given_is_refused_by_name(sessions):
    s = sessions
    unknown = str(uuid4())
    assert refusal(s, code_project_ids=[unknown]) == (
        f'Code project {unknown} is not available: it was removed, archived or disabled.'
    )
    assert refusal(s, service_ids=[unknown]) == (
        f'Service {unknown} is not available: it was removed, archived or disabled.'
    )
    assert (
        refusal(s, server_ids=[unknown]) == f'Server {unknown} is not available: it was removed, archived or disabled.'
    )
    assert refusal(s, environment_ids=[unknown]) == (
        f'Environment {unknown} is not available: it was removed, archived or disabled.'
    )
    with s.store.connect() as conn:
        conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id=%s', (s.worker,))
    assert refusal(s, code_project_ids=[s.worker]) == (
        'Code project Payments worker is not available: it was removed, archived or disabled.'
    )


def test_a_saved_agent_called_ad_hoc_never_blocks_ad_hoc_turns(sessions):
    s = sessions
    config = build(s, code_project_ids=[s.repo])
    with s.store.connect() as conn:
        conn.execute(
            "INSERT INTO ka.agents(id,name,system_prompt,enabled) VALUES ('ad-hoc','Saved lookalike','x',false),('saved-off','Saved off','x',false)"
        )
    assert isinstance(load_credentials(s.store, config), dict)  # no 'Agent is disabled.'
    with pytest.raises(ValueError, match='Agent is disabled.'):
        load_credentials(s.store, {**config, 'id': 'saved-off'})
