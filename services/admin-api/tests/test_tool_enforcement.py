"""What the model is given: exactly the selected catalog tools, the MCP tools not switched off, edited usage as
the description or as Tool notes, and per-run narrowing unchanged."""

import asyncio

from agents import Runner
from implied_tools import implied_tools
from knowledge_admin.agents.runner import agent, get_prompt
from knowledge_admin.tools.catalog import CATALOG
from test_sdk_agent import config, responses_api  # noqa: F401  (fixtures)


def choose(cfg, on: dict[str, str | None], mcp: dict[str, tuple[str, str | None]] = {}):
    """Switch on exactly `on` (tool → usage or None); MCP rows as name → (mode, usage)."""
    items = [
        {
            'tool': name,
            'group': spec.group,
            'mode': 'on' if name in on else 'off',
            'usage': on.get(name),
            'subagent_allowed': True,
        }
        for name, spec in CATALOG.items()
    ]
    items += [
        {'tool': name, 'group': 'mcp', 'mode': mode, 'usage': usage, 'subagent_allowed': True}
        for name, (mode, usage) in mcp.items()
    ]
    cfg['tools'] = {'items': items}


def first_request(requests):
    return next(r for r in requests if r['path'] == '/v1/responses')['body']


def test_the_model_request_carries_exactly_the_selected_tools(config, responses_api):
    _, requests = responses_api
    choose(config, {'read_file': 'Read only what the task names.', 'search_documents': None, 'ssh_run': None})
    asyncio.run(agent(config, 'Describe your tools.', credentials={'model-key': 'fixture-api-secret'}))
    body = first_request(requests)
    tools = {tool['name']: tool for tool in body['tools']}
    # SSH is off in the fixture: ssh_run's need is unmet; get_resource is on in every run with resources,
    # get_available_resources in every run
    assert set(tools) == {'read_file', 'search_documents', 'get_resource', 'get_available_resources'}
    assert tools['read_file']['description'] == 'Read only what the task names.'
    assert tools['search_documents']['description'] == CATALOG['search_documents'].usage


def test_the_shell_is_switched_by_the_shell_tool(config, responses_api, monkeypatch):
    seen = {}
    real = Runner.run_streamed

    def spy(assistant, task, *, context, **kwargs):
        seen.update(tools=context['tools'], shell=context['shell_enabled'])
        return real(assistant, task, context=context, **kwargs)

    monkeypatch.setattr('knowledge_admin.agents.runner.Runner.run_streamed', spy)
    choose(config, {'read_file': None})
    asyncio.run(agent(config, 'Inspect.', credentials={'model-key': 'fixture-api-secret'}))
    assert seen == {'tools': frozenset({'read_file', 'get_resource', 'get_available_resources'}), 'shell': False}
    choose(config, {'shell_run': None})
    asyncio.run(agent(config, 'Inspect.', credentials={'model-key': 'fixture-api-secret'}))
    assert seen == {'tools': frozenset({'shell_run', 'get_resource', 'get_available_resources'}), 'shell': True}


def test_mcp_tools_switched_off_never_reach_the_model(config, responses_api):
    url, requests = responses_api
    servers = [
        {
            'id': name,
            'name': name,
            'description': 'Fixture',
            'url': url.removesuffix('/v1') + '/mcp',
            'enabled': True,
            'bearer_token_ref': None,
        }
        for name in ('docs-one', 'docs-two')
    ]
    config['mcp'] = {'enabled': True, 'servers': servers}
    choose(config, {}, {'mcp:docs-one:lookup': ('off', None), 'mcp:docs-two:lookup': ('on', 'Ask for one page only.')})
    asyncio.run(agent(config, 'Describe the tools.', credentials={'model-key': 'fixture-api-secret'}))
    body = first_request(requests)
    names = [tool['name'] for tool in body['tools'] if tool['name'] not in ('get_resource', 'get_available_resources')]
    assert len(names) == 1 and 'docs-two' in names[0] and 'lookup' in names[0]
    assert 'Tool notes:\n- docs-two/lookup: Ask for one page only.' in body['instructions']


def test_prompt_tool_notes_name_the_server_and_only_edited_tools(config):
    config['mcp'] = {'enabled': True, 'servers': [{'id': 'm1', 'name': 'Docs', 'description': '', 'enabled': True}]}
    choose(
        config,
        {},
        {
            'mcp:m1:lookup': ('on', 'Use for API pages.'),
            'mcp:m1:search': ('on', None),
            'mcp:m1:delete': ('off', 'Never.'),
        },
    )
    prompt = get_prompt(config, config['mcp']['servers'])
    assert prompt.endswith('Tool notes:\n- Docs/lookup: Use for API pages.')
    choose(config, {})
    assert 'Tool notes' not in get_prompt(config, config['mcp']['servers'])


def test_the_implied_tools_keep_todays_tools(config, responses_api):
    _, requests = responses_api
    config['tools'] = implied_tools(config)
    asyncio.run(agent(config, 'Inspect.', credentials={'model-key': 'fixture-api-secret'}))
    names = {tool['name'] for tool in first_request(requests)['tools']}
    expected = {entry['tool'] for entry in implied_tools(config)['items'] if entry['mode'] == 'on'} - {'code_graph'}
    expected |= {'get_resource', 'get_available_resources'}  # on in every run with resources; in every run
    assert names == expected
