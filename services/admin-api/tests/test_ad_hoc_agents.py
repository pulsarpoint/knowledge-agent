"""The ad-hoc agent of Generate documents: no agents row, the user's prompt and model, exactly the selection as its
resources and the tools of the selected kinds."""
import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from knowledge_admin.agents import load_credentials
from knowledge_admin.knowledge_agents.config import AD_HOC_AGENT, load_model
from knowledge_admin.knowledge_agents.ephemeral import ad_hoc_config
from knowledge_admin.knowledge_agents.run_resources import RunResourceError
from knowledge_admin.tools.catalog import effective_tools
from test_claude_code_sessions import sessions  # noqa: F401  (fixture)
from test_operations import setup as ssh_setup


def chosen(**ids) -> SimpleNamespace:
    kinds = ('code_project_ids', 'collection_ids', 'environment_ids', 'service_ids', 'server_ids')
    return SimpleNamespace(**{kind: [UUID(str(item)) for item in ids.get(kind, [])] for kind in kinds})


def build(s, selection, prompt='You document.') -> dict:
    with s.store.connect() as conn:
        project = s.store.project(conn, 'shop')
        return ad_hoc_config(conn, project, model=load_model(conn, s.gpt), system_prompt=prompt, selection=selection)


def test_the_ad_hoc_agent_gets_exactly_the_selection(sessions):
    s = sessions
    config = build(s, chosen(code_project_ids=[s.repo]))
    assert (config['id'], config['runtime'], config['resources'], config['enabled']) == (AD_HOC_AGENT, 'openai', 'per_run', True)
    assert config['system_prompt'] == {'content': 'You document.'} and config['instructions'] == ''
    assert config['model']['id'] == s.gpt and config['skills']['enabled'] is False and config['mcp']['enabled'] is False
    assert [p['name'] for p in config['code']['projects']] == ['Payments API'] and config['code']['enabled'] is True
    assert (config['documents']['enabled'], config['ssh']['enabled'], config['services']['enabled']) == (False, False, False)
    assert config['run_resources'] == {'code_project_ids': [s.repo], 'collection_ids': [], 'server_ids': [], 'service_ids': []}
    offered = [spec.name for spec, _ in effective_tools(config, [])]
    assert 'read_file' in offered and 'ssh_run' not in offered and 'search_documents' not in offered
    assert 'report_not_covered' not in offered and 'run_subagent' not in offered


def test_an_environment_brings_its_services_code_and_servers(sessions):
    s = sessions
    with s.store.connect() as conn:  # prod-1 gets a usable connection of its own
        conn.execute("UPDATE ka.servers SET username='deploy',enabled=true,ssh_allowed=true WHERE id=%s", (s.server['id'],))
    config = build(s, chosen(environment_ids=[s.environment]))
    assert config['run_resources']['service_ids'] == [s.service]
    assert sorted(config['run_resources']['code_project_ids']) == sorted([s.repo, s.worker])
    assert config['run_resources']['server_ids'] == [s.server['id']]
    assert [item['name'] for item in config['services']['items']] == ['Payments']


def test_a_brought_server_without_a_connection_is_left_out_but_a_chosen_one_is_refused(sessions):
    s = sessions  # prod-1 allows SSH but has no username: not enabled
    with s.store.connect() as conn:
        conn.execute('UPDATE ka.servers SET ssh_allowed=true WHERE id=%s', (s.server['id'],))
    config = build(s, chosen(environment_ids=[s.environment]))
    assert config['run_resources']['server_ids'] == [] and config['ssh']['enabled'] is False
    assert config['run_resources']['service_ids'] == [s.service]
    with pytest.raises(RunResourceError, match='Server prod-1 is not available'):
        build(s, chosen(server_ids=[s.server['id']]))


def test_environment_brings_only_usable_resources(sessions):
    s = sessions
    with s.store.connect() as conn:
        conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id=%s', (s.worker,))
        conn.execute('UPDATE ka.servers SET ssh_allowed=false WHERE id=%s', (s.server['id'],))
    config = build(s, chosen(environment_ids=[s.environment]))
    assert config['run_resources']['code_project_ids'] == [s.repo] and config['run_resources']['server_ids'] == []
    assert config['ssh']['enabled'] is False


def test_a_chosen_server_keeps_its_own_connection_as_a_reference(sessions):
    s = sessions
    server, key = ssh_setup(s.client)
    config = build(s, chosen(server_ids=[server['id']]))
    [pinned] = config['ssh']['servers']
    assert pinned['id'] == server['id'] and pinned['connection_source'] == 'server'
    assert pinned['private_key_ref']['id'] and key['private_key'] not in json.dumps(config)
    assert 'ssh_run' in [spec.name for spec, _ in effective_tools(config, config['ssh']['servers'])]


def test_a_server_that_cannot_be_used_is_refused_by_name(sessions):
    s = sessions
    with s.store.connect() as conn:
        conn.execute('UPDATE ka.servers SET ssh_allowed=false WHERE id=%s', (s.server['id'],))
    with pytest.raises(RunResourceError, match='Server prod-1 is not available'):
        build(s, chosen(server_ids=[s.server['id']]))


def test_a_saved_agent_called_ad_hoc_never_blocks_ad_hoc_turns(sessions):
    s = sessions
    config = build(s, chosen(code_project_ids=[s.repo]))
    with s.store.connect() as conn:
        conn.execute("INSERT INTO ka.agents(id,name,system_prompt,enabled) VALUES ('ad-hoc','Saved lookalike','x',false),('saved-off','Saved off','x',false)")
    assert isinstance(load_credentials(s.store, config), dict)  # no 'Agent is disabled.'
    with pytest.raises(ValueError, match='Agent is disabled.'):
        load_credentials(s.store, {**config, 'id': 'saved-off'})
