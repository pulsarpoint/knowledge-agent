"""The configuration carries the agent's tools."""

from knowledge_admin.knowledge_agents.config import get_agent_config
from knowledge_admin.tools.catalog import CATALOG
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)


def modes(config) -> dict[str, str]:
    return {entry['tool']: entry['mode'] for entry in config['tools']['items']}


def test_the_configuration_lists_every_catalog_tool_and_the_changed_mcp_tools(store, saved_agent):
    mcp = saved_agent[4]
    with store.connect() as conn:
        conn.execute("DELETE FROM ka.agent_tools WHERE agent_id='operations'")
        conn.execute(
            "INSERT INTO ka.agent_tools(agent_id,tool,usage) VALUES ('operations','read_file','Read narrowly.')"
        )
        conn.execute(
            "INSERT INTO ka.agent_tools(agent_id,tool,mode) VALUES ('operations',%s,'off')",
            (f'mcp:{mcp["id"]}:lookup',),
        )
    config = get_agent_config('operations', conninfo=store.conninfo)
    assert [entry['tool'] for entry in config['tools']['items']] == [*CATALOG, f'mcp:{mcp["id"]}:lookup']
    assert modes(config)['read_file'] == 'on' and modes(config)['list_files'] == 'off'
    assert config['tools']['items'][1] == {
        'tool': 'read_file',
        'group': 'code',
        'mode': 'on',
        'usage': 'Read narrowly.',
        'subagent_allowed': True,
    }
    assert config['tools']['items'][-1]['group'] == 'mcp' and modes(config)[f'mcp:{mcp["id"]}:lookup'] == 'off'
