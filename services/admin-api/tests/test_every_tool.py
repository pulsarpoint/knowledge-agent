"""Every run gets every tool: the whole catalog (run_subagent except in a subagent, the wake-up tools only in a session
turn), every expert (narrowed by the request's expert_ids), every tool of the MCP servers in its access; the access
object alone decides what a tool may do. A request carries no tool switches; an ad-hoc run asks for the content editor role with content_editor."""

import asyncio

from knowledge_admin.agents.runner import instructions_and_tools
from knowledge_admin.capabilities.documents import check_report_not_covered, report_not_covered
from knowledge_admin.knowledge_agents.subagents import subagent_config
from knowledge_admin.tools.catalog import CATALOG, WAKEUPS
from test_discovery_runs import config_of, ssh_ready
from test_generations import sessions  # noqa: F401  (fixture)
from test_sessions_saved_agent import agent, start  # noqa: F401  (fixture)
from test_sessions_world import ask  # noqa: F401  (fixture)
from test_tool_catalog import MCP, config
from knowledge_admin.capabilities.mcp import selected_servers
from discovery_world import world  # noqa: F401  (fixture)
from test_repositories import HEADERS

EVERY = list(CATALOG)
NOT_GROUNDED = 'This run does not answer only from documents.'
EXPERT = {
    'id': 'e1',
    'name': 'Payments expert',
    'when_to_use': 'Payments.',
    'accepts': [],
    'tool': 'ask_payments_expert',
    'description': 'Ask Payments expert, an expert agent. Payments. It uses its own resources.',
}


def names(c, subagents=True) -> list[str]:
    _, tools, experts, _ = instructions_and_tools(c, subagents=subagents)
    return [tool.name for tool in [*tools, *experts]]


def test_a_saved_agent_an_ad_hoc_run_and_a_grounded_agent_get_every_tool():
    saved = config()
    saved['experts'] = [EXPERT]
    assert names(saved) == [*EVERY, 'ask_payments_expert']
    ad_hoc = config()  # nothing in its access
    ad_hoc['id'] = 'ad-hoc'
    assert names(ad_hoc) == EVERY
    grounded = config(documents=True, grounded=True)
    assert names(grounded) == EVERY
    # every tool has its built-in description
    _, tools, _, _ = instructions_and_tools(saved, subagents=True)
    assert all(tool.description == CATALOG[tool.name].usage for tool in tools)


def test_a_subagent_gets_every_tool_but_run_subagent_and_the_wakeups_and_no_experts():
    parent = config(ssh=True)
    parent['experts'] = [EXPERT]
    sub = subagent_config(parent, None)
    assert names(sub, subagents=False) == [name for name in EVERY if name != 'run_subagent' and name not in WAKEUPS]
    assert sub['experts'] == [] and not sub['access']['roles']['content_editor']


def test_a_subagent_keeps_the_mcp_servers_of_its_parents_access():
    parent = config(entries=[MCP])
    assert [entry['id'] for entry in parent['access']['resources'] if entry['type'] == 'mcp_server'] == ['m1']
    for resources in (None, {'code_project_ids': []}):
        sub = subagent_config(parent, resources)
        assert [entry['id'] for entry in sub['access']['resources'] if entry['type'] == 'mcp_server'] == ['m1']
        assert [server['id'] for server in selected_servers(sub)] == ['m1']


def test_an_internal_llm_call_gets_no_tools():
    """The agent designer and the tuning reviewer (tools=False): no catalog tools, experts or MCP servers."""
    c = config(entries=[MCP])
    c['experts'] = [EXPERT]
    _, tools, experts, servers = instructions_and_tools(c, subagents=True, tools=False)
    assert (tools, experts, servers) == ([], [], [])


def test_run_subagent_has_no_tools_argument():
    assert set(CATALOG['run_subagent'].arguments.model_json_schema()['properties']) == {'task', 'resources'}


def test_the_prompt_has_no_tool_notes():
    c = config(entries=[MCP])
    prompt, _, _, _ = instructions_and_tools(c, subagents=True)
    assert 'Tool notes' not in prompt


def test_report_not_covered_refuses_outside_a_grounded_run():
    access = {'resources': [], 'roles': {'content_editor': False}}
    assert check_report_not_covered(access, reason='missing') == NOT_GROUNDED
    assert check_report_not_covered(access, grounded=True, reason='missing') is None
    plain = config(documents=True)
    assert asyncio.run(report_not_covered({'config': plain}, 'gap')) == NOT_GROUNDED
    grounded = config(documents=True, grounded=True)
    context = {'config': grounded}
    assert asyncio.run(report_not_covered(context, 'gap')).startswith('Recorded.')
    assert context['grounding']['reported'] == 'gap'


# Through /api/sessions


def body(world, **change) -> dict:
    return {
        'started_from': {'server_id': world.server['id']},
        'model_id': world.model,
        'system_prompt': 'You answer.',
        'message': 'Is it alive?',
        **change,
    }


def test_a_request_with_tools_or_subagents_is_refused(world):
    ssh_ready(world)
    for change in ({'tools': ['ssh_run']}, {'subagents': True}):
        for path in ('/preview', ''):
            response = world.client.post(f'/api/sessions{path}', headers=HEADERS, json=body(world, **change))
            assert response.status_code == 400, response.text


def test_a_resource_run_offers_every_tool(world):
    ssh_ready(world)
    preview = world.client.post('/api/sessions/preview', headers=HEADERS, json=body(world))
    assert preview.status_code == 200, preview.text
    assert [tool['name'] for tool in preview.json()['tools']] == EVERY  # no experts in this workspace


def test_the_propose_wizard_asks_for_the_content_editor_role_and_a_plain_chat_cannot_propose(world):
    ssh_ready(world)
    created = world.client.post(
        '/api/sessions', headers=HEADERS, json=body(world, tags=['proposals'], content_editor=True)
    )
    assert created.status_code == 201, created.text
    wizard = config_of(world, created.json()['id'])
    assert wizard['access']['roles']['content_editor'] is True
    spec = CATALOG['propose_document']
    arguments = spec.arguments.model_validate({'title': 'Disks', 'markdown': '# Disks', 'description': ''})
    assert asyncio.run(spec.run({'config': wizard}, arguments)).startswith('Proposal 1:')
    plain = world.client.post('/api/sessions', headers=HEADERS, json=body(world))
    assert plain.status_code == 201, plain.text
    chat = config_of(world, plain.json()['id'])
    assert chat['access']['roles']['content_editor'] is False
    assert asyncio.run(spec.run({'config': chat}, arguments)) == (
        'This agent may not change content (content editor is off).'
    )


def test_a_saved_agent_gets_every_tool_and_expert_and_may_only_switch_the_role_off(agent):
    response = start(agent.client, '/preview', agent_id=agent.id)
    assert response.status_code == 200, response.text
    shown = response.json()
    assert [tool['name'] for tool in shown['tools']] == [*EVERY, 'ask_payments_expert']
    assert [entry['id'] for entry in shown['config']['experts']] == [agent.expert]
    narrowed = start(agent.client, '/preview', agent_id=agent.id, expert_ids=[]).json()
    assert narrowed['config']['experts'] == [] and 'ask_payments_expert' not in str(narrowed['tools'])
    refused = start(agent.client, '/preview', agent_id=agent.id, content_editor=True)
    assert refused.status_code == 400
    assert refused.json()['error']['message'] == 'A run cannot make a saved agent a content editor.'
    off = start(agent.client, '/preview', agent_id=agent.id, content_editor=False)
    assert off.status_code == 200 and off.json()['config']['access']['roles']['content_editor'] is False


def test_a_saved_agent_that_is_a_content_editor_keeps_or_drops_the_role(agent, ask):
    from knowledge_admin.access import is_content_editor

    with ask.s.store.connect() as conn:
        conn.execute(
            """UPDATE ka.agents SET access=jsonb_set(access,'{roles,content_editor}','true') WHERE id=%s""", (agent.id,)
        )
    kept = start(agent.client, '/preview', agent_id=agent.id, content_editor=True).json()['config']['access']
    assert is_content_editor(kept)
    assert is_content_editor(start(agent.client, '/preview', agent_id=agent.id).json()['config']['access'])
    off = start(agent.client, '/preview', agent_id=agent.id, content_editor=False).json()['config']['access']
    assert not is_content_editor(off)


def test_the_options_carry_no_tools(agent, ask):
    options = agent.client.get(f'/api/sessions/options?agent_id={agent.id}', headers=HEADERS).json()
    assert 'tools' not in options and 'subagents_allowed' not in options['agent']
    plain = ask.s.client.get(f'/api/sessions/options?document_id={ask.document["id"]}', headers=HEADERS).json()
    assert 'tools' not in plain


def test_a_search_counts_as_grounding_only_in_a_grounded_run():
    """search_documents is offered in every run; a refused or plain search must not make an ungrounded turn
    'not covered'."""
    from types import SimpleNamespace

    from knowledge_admin.knowledge_agents.native_sessions import grounding_of

    state = {'excerpts': [], 'searches': 1, 'empty_searches': 1, 'reported': None}

    def turn(c):
        return SimpleNamespace(context_wrapper=SimpleNamespace(context={'config': c, 'grounding': dict(state)}))

    assert grounding_of(turn(config()), 'An answer.') is None
    assert grounding_of(turn(config(documents=True)), 'An answer.') is None
    grounded = grounding_of(turn(config(documents=True, grounded=True)), 'An answer.')
    assert grounded is not None and grounded['not_covered'] is True
