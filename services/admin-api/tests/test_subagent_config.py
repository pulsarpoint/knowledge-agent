"""run_subagent's checks and the configuration a subagent runs with: only the given tools (MCP tools by the name the
model sees), only resources from the parent's current lists, its own prompt, no instructions or skills, and only the
credentials it references. Pure: no database, no model."""

import asyncio
import copy

import pytest
from agents import RunHooks

from knowledge_admin.agents.runner import agent
from knowledge_admin.knowledge_agents.subagents import (
    EMPTY_TASK,
    LONG_TASK,
    NO_SPAWN,
    NO_TOOLS,
    NOT_FOR_SUBAGENTS,
    NOT_GIVEN,
    NOT_OFFERED,
    PROMPT,
    check_task,
    referenced_credentials,
    subagent_config,
)
from knowledge_admin.tools.catalog import CATALOG, effective_tools, item, mcp_blocked
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
ON = {'list_files', 'read_file', 'shell_run', 'code_search', 'ssh_run', 'search_documents', 'run_subagent'}
OFFERED = frozenset(ON)  # what the parent's run offers (context['tools'])
MCP = {'mcp_docs__lookup': 'mcp:docs:lookup', 'mcp_docs__search': 'mcp:docs:search'}  # context['mcp_tools']
CREDENTIALS = {'model-key': 'model-secret', 'ssh-key': 'ssh-secret', 'mcp-token': 'mcp-secret'}
NO_RESOURCES = {'code_project_ids': [], 'collection_ids': [], 'server_ids': [], 'service_ids': []}


def parent(**change) -> dict:
    """A fixed lead agent's pinned configuration: two code projects with graphs, all collections, an SSH server, an MCP
    server with two tools, a service, a skill and instructions; shell_run may not be given to subagents."""
    tools = [
        item(
            name,
            'on' if name in ON else 'off',
            'Read narrowly.' if name == 'read_file' else None,
            subagent_allowed=name not in ('shell_run', 'run_subagent'),
        )
        for name in CATALOG
    ]
    value = {
        'id': 'lead',
        'name': 'Lead',
        'description': '',
        'when_to_use': '',
        'enabled': True,
        'revision': 3,
        'resources': 'fixed',
        'model': MODEL,
        'system_prompt': {'content': 'Lead the work.'},
        'instructions': 'Use the runbook.',
        'code': {
            'enabled': True,
            'all_projects': False,
            'graphs': [{'id': 'app', 'name': 'App'}, {'id': 'worker', 'name': 'Worker'}],
            'projects': [
                {'id': 'app', 'name': 'App', 'folder_paths': ['/code/app']},
                {'id': 'worker', 'name': 'Worker', 'folder_paths': ['/code/worker']},
            ],
        },
        'documents': {
            'enabled': True,
            'grounded': False,
            'all_collections': True,
            'collections': [{'id': 'runbooks', 'name': 'runbooks', 'paths': ['/data/runbooks']}],
        },
        'ssh': {
            'enabled': True,
            'servers': [
                {
                    'id': 'prod',
                    'name': 'prod-1',
                    'enabled': True,
                    'ssh_allowed': True,
                    'private_key_ref': {'id': 'ssh-key'},
                    'skills': [],
                    'mcp_servers': [],
                }
            ],
        },
        'skills': {'enabled': True, 'items': [{'id': 'inspect', 'name': 'Inspect', 'enabled': True}]},
        'mcp': {
            'enabled': True,
            'servers': [
                {
                    'id': 'docs',
                    'name': 'Docs',
                    'enabled': True,
                    'bearer_token_ref': {'id': 'mcp-token'},
                    'tools': [{'name': 'lookup'}, {'name': 'search'}],
                }
            ],
        },
        'services': {'enabled': True, 'items': [{'id': 'payments', 'name': 'Payments'}]},
        'tools': {'items': [*tools, item('mcp:docs:lookup', 'on', 'Use for API pages.', group='mcp')]},
        'record': {'run_key': 'run:x'},
    }
    value.update(change)
    return value


def modes(sub) -> dict[str, str]:
    return {entry['tool']: entry['mode'] for entry in sub['tools']['items']}


def test_a_subagent_gets_only_the_given_tools_and_resources_and_its_own_prompt():
    lead = parent()
    before = copy.deepcopy(lead)
    sub = subagent_config(
        lead,
        ['read_file', 'mcp_docs__lookup'],
        {**NO_RESOURCES, 'code_project_ids': ['worker']},
        offered=OFFERED,
        mcp_tools=MCP,
    )
    assert lead == before  # the parent's configuration is untouched
    assert {name for name, mode in modes(sub).items() if mode == 'on'} == {'read_file', 'mcp:docs:lookup'}
    assert modes(sub)['run_subagent'] == 'off' and modes(sub)['mcp:docs:search'] == 'off'
    entries = {entry['tool']: entry for entry in sub['tools']['items']}
    assert (entries['read_file']['usage'], entries['mcp:docs:lookup']['usage']) == (
        'Read narrowly.',
        'Use for API pages.',
    )
    assert mcp_blocked(sub, 'docs') == ['search'] and [s['id'] for s in sub['mcp']['servers']] == ['docs']
    assert {spec.name for spec, _ in effective_tools(sub, [])} == {'read_file'}
    assert [p['id'] for p in sub['code']['projects']] == ['worker'] and [g['id'] for g in sub['code']['graphs']] == [
        'worker'
    ]
    assert (sub['documents']['all_collections'], sub['documents']['collections']) == (False, [])
    assert sub['ssh']['servers'] == [] and sub['services']['items'] == []
    assert sub['resources'] == 'per_run' and sub['run_resources'] == {**NO_RESOURCES, 'code_project_ids': ['worker']}
    assert sub['system_prompt'] == {'content': PROMPT} and PROMPT.startswith(
        'You are a helper started by another agent'
    )
    assert (sub['instructions'], sub['skills'], sub['name']) == ('', {'enabled': False, 'items': []}, 'Lead · subagent')
    assert sub['model'] == MODEL and 'record' not in sub


def test_without_resources_a_subagent_keeps_the_parents_lists_and_mode():
    sub = subagent_config(parent(), ['ssh_run'], None, offered=OFFERED, mcp_tools=MCP)
    assert [p['id'] for p in sub['code']['projects']] == ['app', 'worker']
    assert sub['documents']['all_collections'] is True and [s['id'] for s in sub['ssh']['servers']] == ['prod']
    assert sub['resources'] == 'fixed' and 'run_resources' not in sub
    assert (sub['mcp']['enabled'], sub['mcp']['servers']) == (False, [])  # no MCP tool given: no MCP server


@pytest.mark.parametrize(
    'tools, resources, lead, message',
    [
        ([], None, {}, NO_TOOLS),
        (['run_subagent'], None, {}, NO_SPAWN),
        (['ast_grep'], None, {}, NOT_OFFERED.format(name='ast_grep')),
        (
            ['mcp:docs:lookup'],
            None,
            {},
            NOT_OFFERED.format(name='mcp:docs:lookup'),
        ),  # the Tools tab name is not the model's
        (['shell_run'], None, {}, NOT_FOR_SUBAGENTS.format(name='shell_run')),
        (['read_file'], {'code_project_ids': ['billing']}, {}, NOT_GIVEN.format(label='Code project', id='billing')),
        (['read_file'], {'service_ids': ['ledger']}, {}, NOT_GIVEN.format(label='Service', id='ledger')),
        (
            ['read_file'],
            {'collection_ids': ['runbooks']},
            {'documents': {'enabled': False, 'collections': []}},
            NOT_GIVEN.format(label='Collection', id='runbooks'),
        ),  # a kind the parent does not use
        (
            ['read_file'],
            {'code_project_ids': ['app']},  # a per-run parent's lists are its run's resources
            {
                'resources': 'per_run',
                'run_resources': {**NO_RESOURCES, 'code_project_ids': ['worker']},
                'code': {
                    'enabled': True,
                    'all_projects': False,
                    'graphs': [],
                    'projects': [{'id': 'worker', 'name': 'Worker', 'folder_paths': ['/code/worker']}],
                },
            },
            NOT_GIVEN.format(label='Code project', id='app'),
        ),
    ],
)
def test_checks_refuse_with_a_message_naming_the_tool_or_resource(tools, resources, lead, message):
    with pytest.raises(ValueError) as refused:
        subagent_config(parent(**lead), tools, resources, offered=OFFERED, mcp_tools=MCP)
    assert str(refused.value) == message


def test_an_mcp_tool_switched_off_for_the_parent_stays_off_for_the_subagent():
    lead = parent()
    lead['mcp']['servers'][0]['tools'] = []  # the snapshot does not list the server's tools
    lead['tools']['items'].append(item('mcp:docs:search', 'off', group='mcp'))
    sub = subagent_config(
        lead, ['mcp_docs__lookup'], None, offered=OFFERED, mcp_tools={'mcp_docs__lookup': 'mcp:docs:lookup'}
    )
    assert modes(sub)['mcp:docs:lookup'] == 'on' and mcp_blocked(sub, 'docs') == ['search']


def test_a_task_is_required_and_at_most_20000_characters():
    check_task('x' * 20_000)
    for task, message in (('  ', EMPTY_TASK), ('x' * 20_001, LONG_TASK)):
        with pytest.raises(ValueError) as refused:
            check_task(task)
        assert str(refused.value) == message


def test_a_subagent_gets_only_the_credentials_its_configuration_references():
    lead = parent()
    narrow = subagent_config(
        lead, ['read_file'], {**NO_RESOURCES, 'code_project_ids': ['worker']}, offered=OFFERED, mcp_tools=MCP
    )
    assert referenced_credentials(narrow, CREDENTIALS) == {'model-key': 'model-secret'}
    wide = subagent_config(lead, ['ssh_run', 'mcp_docs__lookup'], None, offered=OFFERED, mcp_tools=MCP)
    assert referenced_credentials(wide, CREDENTIALS) == CREDENTIALS


def test_the_run_context_names_each_mcp_tool_as_the_model_sees_it(config, responses_api):
    url, requests = responses_api
    config['mcp'] = {
        'enabled': True,
        'servers': [
            {
                'id': 'docs-one',
                'name': 'Docs',
                'description': 'Fixture',
                'url': url.removesuffix('/v1') + '/mcp',
                'enabled': True,
                'bearer_token_ref': None,
            }
        ],
    }
    seen: dict[str, str] = {}

    class Capture(RunHooks):
        async def on_agent_start(self, context, agent):
            seen.update(context.context['mcp_tools'])

    asyncio.run(agent(config, 'Describe the tools.', credentials={'model-key': 'fixture-api-secret'}, hooks=Capture()))
    request = next(r for r in requests if r['path'] == '/v1/responses')
    [visible] = [tool['name'] for tool in request['body']['tools'] if 'lookup' in tool['name']]
    assert seen == {visible: 'mcp:docs-one:lookup'}


def test_an_mcp_tool_not_for_subagents_or_blocked_for_the_parent_is_refused_under_its_visible_name():
    lead = parent()
    lead['tools']['items'][-1]['subagent_allowed'] = False  # mcp:docs:lookup
    with pytest.raises(ValueError) as refused:
        subagent_config(lead, ['mcp_docs__lookup'], None, offered=OFFERED, mcp_tools=MCP)
    assert str(refused.value) == NOT_FOR_SUBAGENTS.format(name='mcp_docs__lookup')
    lead = parent()
    lead['tools']['items'].append(item('mcp:docs:search', 'off', group='mcp'))
    run_tools = {'mcp_docs__lookup': 'mcp:docs:lookup'}  # the parent's run does not offer search
    with pytest.raises(ValueError) as refused:
        subagent_config(lead, ['mcp_docs__search'], None, offered=OFFERED, mcp_tools=run_tools)
    assert str(refused.value) == NOT_OFFERED.format(name='mcp_docs__search')


def test_a_subagent_gets_an_ssh_key_only_with_ssh_run_or_for_a_kept_mcp_server_on_that_server():
    lead = parent()
    lead['ssh']['servers'].append(
        {
            'id': 'logs-host',
            'name': 'logs-1',
            'enabled': True,
            'ssh_allowed': True,
            'private_key_ref': {'id': 'logs-key'},
            'skills': [],
            'mcp_servers': [
                {'id': 'logs', 'name': 'Logs', 'enabled': True, 'bearer_token_ref': None, 'tools': [{'name': 'tail'}]}
            ],
        }
    )
    credentials = {**CREDENTIALS, 'logs-key': 'logs-secret'}
    tools = {**MCP, 'mcp_logs__tail': 'mcp:logs:tail'}
    reading = subagent_config(lead, ['read_file'], None, offered=OFFERED, mcp_tools=tools)
    assert referenced_credentials(reading, credentials) == {'model-key': 'model-secret'}
    assert reading['ssh']['servers'] == []  # no SSH server it cannot use
    tailing = subagent_config(lead, ['mcp_logs__tail'], None, offered=OFFERED, mcp_tools=tools)
    assert referenced_credentials(tailing, credentials) == {'model-key': 'model-secret', 'logs-key': 'logs-secret'}
    assert [server['id'] for server in tailing['ssh']['servers']] == ['logs-host']
    running = subagent_config(lead, ['ssh_run'], None, offered=OFFERED, mcp_tools=tools)
    assert referenced_credentials(running, credentials) == {
        'model-key': 'model-secret',
        'ssh-key': 'ssh-secret',
        'logs-key': 'logs-secret',
    }
    per_run = subagent_config(
        lead, ['read_file'], {**NO_RESOURCES, 'server_ids': ['prod']}, offered=OFFERED, mcp_tools=tools
    )
    assert (per_run['ssh']['servers'], per_run['run_resources']['server_ids']) == ([], [])


def test_a_subagents_mcp_servers_offer_only_its_given_tools(config, responses_api):
    """An allowlist, not the parent's blocklist: a server tool the configuration does not list (lookup, added to the
    server later) stays out of a subagent's run."""
    url, requests = responses_api
    config['mcp'] = {
        'enabled': True,
        'servers': [
            {
                'id': 'docs-one',
                'name': 'Docs',
                'description': 'Fixture',
                'url': url.removesuffix('/v1') + '/mcp',
                'enabled': True,
                'bearer_token_ref': None,
                'tools': [],
            }
        ],
    }
    sub = subagent_config(
        config,
        ['mcp_docs-one__other'],
        None,
        offered=frozenset(),
        mcp_tools={'mcp_docs-one__other': 'mcp:docs-one:other'},
    )
    assert mcp_blocked(sub, 'docs-one') == []  # a blocklist would let lookup through
    asyncio.run(agent(sub, 'Describe the tools.', credentials={'model-key': 'fixture-api-secret'}, stream=False))
    request = next(r for r in requests if r['path'] == '/v1/responses')
    assert not [tool['name'] for tool in request['body'].get('tools', []) if 'lookup' in tool['name']]
    asyncio.run(agent(config, 'Describe the tools.', credentials={'model-key': 'fixture-api-secret'}, stream=False))
    request = [r for r in requests if r['path'] == '/v1/responses'][-1]
    assert [tool['name'] for tool in request['body']['tools'] if 'lookup' in tool['name']]  # the parent still has it
