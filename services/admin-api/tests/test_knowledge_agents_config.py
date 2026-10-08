"""One agent config for both agent types: the shape, the type and model rules, and the native-only guard."""

import pytest
from knowledge_llm import ModelUnavailable
from knowledge_llm.profiles import profile

from knowledge_admin.agents import get_agent_config as agents_loader
from knowledge_admin.knowledge_agents.config import (CLAUDE_CODE_TOOLS, AgentConfig, get_agent_config,
                                                     model_fits, normalize_tools, require_native)
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)


def test_both_types_load_the_same_config_shape(store, saved_agent):
    assert agents_loader is get_agent_config  # moved; existing imports keep working
    native = get_agent_config('operations', conninfo=store.conninfo)
    assert set(native) == AgentConfig.__required_keys__
    assert (native['runtime'], native['claude_code_tools']) == ('openai', list(CLAUDE_CODE_TOOLS))
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET runtime='claude_code',claude_code_tools='{Read,Bash}' WHERE id='operations'")
    claude = get_agent_config('operations', conninfo=store.conninfo)
    assert (claude['runtime'], claude['claude_code_tools']) == ('claude_code', ['Read', 'Bash'])
    shared = lambda config: {key: value for key, value in config.items() if key not in ('runtime', 'claude_code_tools', 'tools')}
    assert shared(claude) == shared(native)  # switching the type changes only the runtime and its tools
    assert claude['tools'] == {'items': []} and native['tools']['items']


def test_model_rules_and_tool_names():
    claude = {'provider': 'bedrock', 'api_format': 'native', 'model': 'eu.anthropic.claude-sonnet-4-5-20250929-v1:0'}
    gpt = {'provider': 'openai', 'api_format': 'responses', 'model': 'gpt-5'}
    chat = {'provider': 'openai', 'api_format': 'chat_completions', 'model': 'llama'}
    embeddings = {'provider': 'openai', 'api_format': 'embeddings', 'model': 'text-embedding-3-small'}
    assert model_fits('claude_code', claude) and not model_fits('claude_code', gpt)
    assert model_fits('openai', claude) and model_fits('openai', gpt) and not model_fits('openai', chat)
    assert not model_fits('openai', embeddings)  # no agent takes any chat model any more: Chat Completions is refused
    with pytest.raises(TypeError):
        model_fits('openai', chat, coordinator=True)
    assert normalize_tools(['Bash', 'Read', 'Bash']) == ['Read', 'Bash'] and normalize_tools([]) == []
    require_native({'runtime': 'openai'})
    require_native({})  # sessions saved before 0072 have no runtime: native
    with pytest.raises(ValueError, match='Claude Code sessions'):
        require_native({'runtime': 'claude_code'})


def test_claude_code_agents_stay_out_of_native_runs(store, saved_agent):
    client = saved_agent[0]
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET runtime='claude_code' WHERE id='operations'")
    with store.connect() as conn:
        with pytest.raises(ModelUnavailable):
            profile(conn, 'operations', runnable=True)


def test_the_claude_code_tool_names_are_defined_once():
    from knowledge_admin.claude_code import ALLOWED_TOOLS
    from knowledge_admin.knowledge_agents.config import CLAUDE_CODE_TOOLS
    assert ALLOWED_TOOLS is CLAUDE_CODE_TOOLS and CLAUDE_CODE_TOOLS == ('Read', 'Grep', 'Glob', 'Bash')
