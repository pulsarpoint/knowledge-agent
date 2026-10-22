"""What the model is given: every catalog tool with its built-in description (run_subagent, the wake-up and inventory write tools only
with the turn's Subagents host) and every tool of the run's MCP servers, whatever the agent's old tool rows say; no Tool notes."""

import asyncio

from access_helpers import entry
from agents import Runner
from knowledge_admin.agents.runner import agent, get_prompt
from knowledge_admin.tools.catalog import CATALOG, INVENTORY_WRITES, WAKEUPS
from test_sdk_agent import config, responses_api  # noqa: F401  (fixtures)

EVERY = {name for name in CATALOG if name != 'run_subagent'} - WAKEUPS - INVENTORY_WRITES  # no Subagents host


def rows(cfg, on: dict[str, str | None], mcp: dict[str, tuple[str, str | None]] = {}):
    """Old tool rows: exactly `on` switched on (tool → usage or None); MCP rows as name → (mode, usage)."""
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


def test_the_model_request_carries_every_tool_with_its_built_in_description(config, responses_api):
    _, requests = responses_api
    rows(config, {'read_file': 'Read only what the task names.'})
    asyncio.run(agent(config, 'Describe your tools.', credentials={'model-key': 'fixture-api-secret'}))
    tools = {tool['name']: tool for tool in first_request(requests)['tools']}
    assert set(tools) == EVERY
    assert all(tool['description'] == CATALOG[name].usage for name, tool in tools.items())


def test_every_run_offers_every_tool(config, responses_api, monkeypatch):
    seen = {}
    real = Runner.run_streamed

    def spy(assistant, task, *, context, **kwargs):
        seen.update(tools=context['tools'])
        return real(assistant, task, context=context, **kwargs)

    monkeypatch.setattr('knowledge_admin.agents.runner.Runner.run_streamed', spy)
    rows(config, {'read_file': None})
    asyncio.run(agent(config, 'Inspect.', credentials={'model-key': 'fixture-api-secret'}))
    assert seen == {'tools': frozenset(EVERY)}


def test_every_mcp_tool_reaches_the_model_and_the_prompt_has_no_tool_notes(config, responses_api):
    url, requests = responses_api
    config['access']['resources'] += [
        {
            **entry(
                'mcp_server', name, name, url=url.removesuffix('/v1') + '/mcp', enabled=True, bearer_token_ref=None
            ),
            'source': 'given',
        }
        for name in ('docs-one', 'docs-two')
    ]
    rows(config, {}, {'mcp:docs-one:lookup': ('off', None), 'mcp:docs-two:lookup': ('on', 'Ask for one page only.')})
    asyncio.run(agent(config, 'Describe the tools.', credentials={'model-key': 'fixture-api-secret'}))
    body = first_request(requests)
    names = [tool['name'] for tool in body['tools'] if tool['name'] not in CATALOG]
    assert len(names) == 2 and all('lookup' in name for name in names)
    assert 'Tool notes' not in body['instructions'] and 'Ask for one page only.' not in body['instructions']
    assert 'Tool notes' not in get_prompt(config)
