"""The configuration a subagent runs with: every tool but run_subagent (its run has no Subagents host), only resources
from the parent's current lists, the parent's MCP servers, its own prompt, no instructions, skills or experts, and only
the credentials it references. Pure: no database, no model."""

import asyncio
import copy

import pytest
from access_helpers import entry, with_access

from knowledge_admin.access import entries
from knowledge_admin.agents.runner import agent, instructions_and_tools
from knowledge_admin.capabilities.mcp import selected_servers
from knowledge_admin.capabilities.subagents import check_run_subagent
from knowledge_admin.knowledge_agents.subagents import (
    EMPTY_TASK,
    LONG_TASK,
    PROMPT,
    check_task,
    referenced_credentials,
    subagent_config,
)
from knowledge_admin.tools.catalog import CATALOG, WAKEUPS
from test_sdk_agent import config, responses_api  # noqa: F401  (fixtures)

MODEL = {
    'id': 'm',
    'name': 'Model',
    'provider': 'openai',
    'api_format': 'responses',
    'model': 'gpt-5',
    'base_url': 'http://model.test/v1',
    'enabled': True,
    'api_key_ref': {'id': 'model-key'},
}
CREDENTIALS = {'model-key': 'model-secret', 'ssh-key': 'ssh-secret', 'mcp-token': 'mcp-secret'}
NO_RESOURCES = {'code_project_ids': [], 'collection_ids': [], 'server_ids': [], 'service_ids': []}


ENTRIES = [
    entry(
        'server',
        'prod',
        'prod-1',
        hostname='prod.test',
        port=22,
        username='ops',
        allow_sudo=False,
        known_hosts='',
        enabled=True,
        ssh_allowed=True,
        private_key_ref={'id': 'ssh-key'},
    ),
    entry('service', 'payments', 'Payments'),
    entry(
        'code_project',
        'app',
        'App',
        folder_paths=['/code/app'],
        graph_path='/index/app/graph.sqlite',
        code_root='/code',
    ),
    entry(
        'code_project',
        'worker',
        'Worker',
        folder_paths=['/code/worker'],
        graph_path='/index/worker/graph.sqlite',
        code_root='/code',
    ),
    entry('collection', 'runbooks', 'runbooks', paths=['/data/runbooks']),
    entry('skill', 'inspect', 'Inspect', description='Inspect first.'),
    entry('mcp_server', 'docs', 'Docs', url='http://docs.test/mcp', enabled=True, bearer_token_ref={'id': 'mcp-token'}),
]


def parent(without=(), content_editor=False, **change) -> dict:
    """A fixed lead agent's pinned configuration: its access holds an SSH server, a service, two code projects with
    graphs, a collection, a skill and an MCP server (but the ids in without); instructions."""
    value = {
        'id': 'lead',
        'name': 'Lead',
        'description': '',
        'when_to_use': '',
        'enabled': True,
        'revision': 3,
        'model': MODEL,
        'system_prompt': {'content': 'Lead the work.'},
        'instructions': 'Use the runbook.',
        'grounded': False,
        'record': {'run_key': 'run:x'},
    }
    value.update(change)
    return with_access(value, [item for item in ENTRIES if item['id'] not in without], content_editor)


def test_a_subagent_gets_every_tool_but_run_subagent_and_the_wakeups_the_given_resources_and_its_own_prompt():
    lead = parent()
    lead['experts'] = [{'id': 'e', 'name': 'E', 'tool': 'ask_e', 'description': 'Ask E.', 'accepts': []}]
    before = copy.deepcopy(lead)
    sub = subagent_config(lead, {**NO_RESOURCES, 'code_project_ids': ['worker']})
    assert lead == before  # the parent's configuration is untouched
    _, tools, experts, _ = instructions_and_tools(sub, subagents=False)
    offered = [name for name in CATALOG if name != 'run_subagent' and name not in WAKEUPS]
    assert [tool.name for tool in tools] == offered and experts == []
    assert [s['id'] for s in selected_servers(sub)] == ['docs'] and 'tools' not in sub
    assert [p['id'] for p in entries(sub['access'], 'code_project')] == ['worker']
    assert entries(sub['access'], 'code_project')[0]['graph_path'] == '/index/worker/graph.sqlite'
    assert entries(sub['access'], 'collection') == [] and entries(sub['access'], 'server') == []
    assert sub['system_prompt'] == {'content': PROMPT} and PROMPT.startswith(
        'You are a helper started by another agent'
    )
    assert (sub['instructions'], sub['name']) == ('', 'Lead · subagent') and entries(sub['access'], 'skill') == []
    assert sub['model'] == MODEL and 'record' not in sub and sub['experts'] == []
    # its access: the parent's narrowed to the given code project, and the parent's MCP servers
    assert [(e['type'], e['id']) for e in sub['access']['resources']] == [
        ('code_project', 'worker'),
        ('mcp_server', 'docs'),
    ]
    assert sub['access']['roles'] == {'content_editor': False, 'inventory_editor': False}


def test_without_resources_a_subagent_keeps_the_parents_lists():
    sub = subagent_config(parent(), None)
    assert [s['id'] for s in selected_servers(sub)] == ['docs']
    assert entries(sub['access'], 'server')[0]['hostname'] == 'prod.test'  # the connection comes with its entry
    # all of the parent's resources but its skills
    assert [(e['type'], e['id']) for e in sub['access']['resources']] == [
        ('server', 'prod'),
        ('service', 'payments'),
        ('code_project', 'app'),
        ('code_project', 'worker'),
        ('collection', 'runbooks'),
        ('mcp_server', 'docs'),
    ]


def test_a_subagent_never_gets_more_than_its_parent_and_never_changes_content():
    lead = parent(without=['worker'], content_editor=True)
    with pytest.raises(ValueError, match="code project worker is not in the agent's access"):
        subagent_config(lead, {**NO_RESOURCES, 'code_project_ids': ['worker']})
    sub = subagent_config(lead, None)
    assert sub['access']['roles'] == {'content_editor': False, 'inventory_editor': False}


WIDER = "{kind} {id} is not in the agent's access"  # access.narrow: a subagent never gets more than its parent


def test_a_service_the_run_holds_through_a_server_can_be_given_to_a_subagent():
    """The access object alone decides: a service held via its server has no entry of its own in the parent's
    service list, and is given all the same."""
    lead = parent(without=['payments'])
    via = {'type': 'service', 'id': 'deployed', 'name': 'Deployed', 'source': 'via server prod'}
    lead['access']['resources'].append(via)
    resources = {**NO_RESOURCES, 'service_ids': ['deployed']}
    assert check_run_subagent(lead['access'], resources=resources) is None
    sub = subagent_config(lead, resources)
    assert [(e['type'], e['id'], e['source']) for e in sub['access']['resources']] == [
        ('service', 'deployed', 'via server prod'),
        ('mcp_server', 'docs', 'given'),
    ]


@pytest.mark.parametrize(
    'resources, lead, message',
    [
        ({'code_project_ids': ['billing']}, {}, WIDER.format(kind='code project', id='billing')),
        ({'service_ids': ['ledger']}, {}, WIDER.format(kind='service', id='ledger')),
        (
            {'collection_ids': ['runbooks']},
            {'without': ['runbooks']},
            WIDER.format(kind='collection', id='runbooks'),
        ),  # a kind the parent does not use
        (
            {'code_project_ids': ['app']},  # a per-run parent's access is its run's
            {'without': ['app']},
            WIDER.format(kind='code project', id='app'),
        ),
    ],
)
def test_checks_refuse_with_a_message_naming_the_resource(resources, lead, message):
    with pytest.raises(ValueError) as refused:
        subagent_config(parent(**lead), resources)
    assert str(refused.value) == message


def test_a_task_is_required_and_at_most_20000_characters():
    check_task('x' * 20_000)
    for task, message in (('  ', EMPTY_TASK), ('x' * 20_001, LONG_TASK)):
        with pytest.raises(ValueError) as refused:
            check_task(task)
        assert str(refused.value) == message


def test_a_subagent_gets_only_the_credentials_its_configuration_references():
    lead = parent()
    narrow = subagent_config(lead, {**NO_RESOURCES, 'code_project_ids': ['worker']})
    assert referenced_credentials(narrow, CREDENTIALS) == {'model-key': 'model-secret', 'mcp-token': 'mcp-secret'}
    wide = subagent_config(lead, None)
    assert referenced_credentials(wide, CREDENTIALS) == CREDENTIALS
    per_run = subagent_config(lead, {**NO_RESOURCES, 'server_ids': ['prod']})
    assert [server['id'] for server in entries(per_run['access'], 'server')] == ['prod']
    assert referenced_credentials(per_run, CREDENTIALS) == CREDENTIALS


def test_a_subagents_mcp_servers_offer_every_tool(config, responses_api):
    url, requests = responses_api
    config['access']['resources'].append(
        entry(
            'mcp_server', 'docs-one', 'Docs', url=url.removesuffix('/v1') + '/mcp', enabled=True, bearer_token_ref=None
        )
    )
    sub = subagent_config(config, None)
    asyncio.run(agent(sub, 'Describe the tools.', credentials={'model-key': 'fixture-api-secret'}, stream=False))
    request = next(r for r in requests if r['path'] == '/v1/responses')
    names = [tool['name'] for tool in request['body'].get('tools', [])]
    assert [name for name in names if 'lookup' in name] and 'run_subagent' not in names


def test_a_subagent_is_never_an_inventory_editor():
    lead = parent()
    lead['access']['roles'] = {'content_editor': True, 'inventory_editor': True}
    assert subagent_config(lead, None)['access']['roles'] == {'content_editor': False, 'inventory_editor': False}
