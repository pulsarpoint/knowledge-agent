"""The configuration carries the agent's tools; versions keep them; configurations saved before 0078 get the implied
tools; Claude Code agents have none."""
import pytest

from knowledge_admin.knowledge_agents.config import get_agent_config
from knowledge_admin.tools.catalog import CATALOG, implied_tools
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_agent_resources import new_version
from test_claude_code_agents import claude_code_agent
from test_repositories import HEADERS


def modes(config) -> dict[str, str]:
    return {entry['tool']: entry['mode'] for entry in config['tools']['items']}


def test_the_configuration_lists_every_catalog_tool_and_the_changed_mcp_tools(store, saved_agent):
    mcp = saved_agent[4]
    with store.connect() as conn:
        conn.execute("DELETE FROM ka.agent_tools WHERE agent_id='operations'")
        conn.execute("INSERT INTO ka.agent_tools(agent_id,tool,usage) VALUES ('operations','read_file','Read narrowly.')")
        conn.execute("INSERT INTO ka.agent_tools(agent_id,tool,mode) VALUES ('operations',%s,'off')", (f"mcp:{mcp['id']}:lookup",))
    config = get_agent_config('operations', conninfo=store.conninfo)
    assert [entry['tool'] for entry in config['tools']['items']] == [*CATALOG, f"mcp:{mcp['id']}:lookup"]
    assert modes(config)['read_file'] == 'on' and modes(config)['list_files'] == 'off'
    assert config['tools']['items'][1] == {'tool': 'read_file', 'group': 'code', 'mode': 'on',
                                           'usage': 'Read narrowly.', 'subagent_allowed': True}
    assert config['tools']['items'][-1]['group'] == 'mcp' and modes(config)[f"mcp:{mcp['id']}:lookup"] == 'off'


def test_versions_keep_the_tools(store, saved_agent):
    client = saved_agent[0]
    with store.connect() as conn:
        # the fixture switches capabilities on after the 0078 backfill, so the rows are set here
        conn.execute("DELETE FROM ka.agent_tools WHERE agent_id='operations'")
        conn.execute("INSERT INTO ka.agent_tools(agent_id,tool) VALUES ('operations','read_file')")
    version = new_version(client, 'Only read_file')
    with store.connect() as conn:
        conn.execute("INSERT INTO ka.agent_tools(agent_id,tool) VALUES ('operations','ssh_run')")
    pinned = get_agent_config('operations', conninfo=store.conninfo, version_id=version['id'])
    assert {tool for tool, mode in modes(pinned).items() if mode == 'on'} == {'read_file'}
    working = get_agent_config('operations', conninfo=store.conninfo, working_copy=True)
    assert {tool for tool, mode in modes(working).items() if mode == 'on'} == {'read_file', 'ssh_run'}


def test_versions_and_pinned_configs_without_tools_get_the_implied_tools(store, saved_agent):
    client = saved_agent[0]
    version = new_version(client, 'Before 0078')
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_versions SET config=config-'tools' WHERE agent='operations'")
    old = get_agent_config('operations', conninfo=store.conninfo, version_id=version['id'])
    assert old['tools'] == implied_tools(old)


def test_claude_code_agents_have_no_catalog_tools(store, saved_agent):
    writer = claude_code_agent(saved_agent[0])
    assert get_agent_config(writer, conninfo=store.conninfo)['tools'] == {'items': []}
