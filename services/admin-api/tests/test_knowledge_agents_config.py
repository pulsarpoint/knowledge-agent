"""One agent config: the shape and the model rules."""

import pytest

from knowledge_admin.agents import get_agent_config as agents_loader
from knowledge_admin.knowledge_agents.config import AgentConfig, get_agent_config, model_fits
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)


def test_the_config_has_the_declared_shape(store, saved_agent):
    assert agents_loader is get_agent_config  # moved; existing imports keep working
    config = get_agent_config('operations', conninfo=store.conninfo)
    assert set(config) == AgentConfig.__required_keys__
    assert 'runtime' not in config and 'claude_code_tools' not in config
    assert config['tools']['items']


def test_model_rules():
    claude = {'provider': 'bedrock', 'api_format': 'native', 'model': 'eu.anthropic.claude-sonnet-4-5-20250929-v1:0'}
    gpt = {'provider': 'openai', 'api_format': 'responses', 'model': 'gpt-5'}
    chat = {'provider': 'openai', 'api_format': 'chat_completions', 'model': 'llama'}
    embeddings = {'provider': 'openai', 'api_format': 'embeddings', 'model': 'text-embedding-3-small'}
    assert model_fits(claude) and model_fits(gpt) and not model_fits(chat)
    assert not model_fits(embeddings)  # no agent takes any chat model any more: Chat Completions is refused
    with pytest.raises(TypeError):
        model_fits(chat, coordinator=True)
