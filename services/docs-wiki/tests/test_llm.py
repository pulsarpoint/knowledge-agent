import pytest

from docs_wiki import config


def cfg(**over):
    base = {"base_url": "http://llm.local/v1", "chat_model": "c"}
    base.update(over)
    return base


def test_resolve_llm_env_overrides_file():
    resolved = config.resolve_llm(cfg(), env={
        "DOCS_WIKI_LLM_BASE_URL": "http://env.local/v1",
        "DOCS_WIKI_CHAT_MODEL": "env-chat",
    })
    assert resolved.base_url == "http://env.local/v1"
    assert resolved.chat_model == "env-chat"


def test_resolve_llm_requires_both():
    assert config.resolve_llm({"base_url": "http://x/v1"}, env={}) is None
    assert config.resolve_llm(None, env={}) is None
    assert config.resolve_llm(None, env={
        "DOCS_WIKI_LLM_BASE_URL": "http://x/v1",
        "DOCS_WIKI_CHAT_MODEL": "c",
    }) is not None


def test_llm_section_must_be_table(tmp_path):
    cfg_file = tmp_path / "cfg.toml"
    cfg_file.write_text(
        'llm = "oops"\n[[apps]]\nname = "x"\n', encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load_config(cfg_file)


def test_load_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("DOCS_WIKI_CHAT_MODEL", raising=False)
    env_file = tmp_path / "my.env"
    env_file.write_text("DOCS_WIKI_CHAT_MODEL=from-file\n", encoding="utf-8")
    config.load_env_file(env_file)
    import os
    assert os.environ["DOCS_WIKI_CHAT_MODEL"] == "from-file"
    monkeypatch.delenv("DOCS_WIKI_CHAT_MODEL", raising=False)
    with pytest.raises(config.ConfigError):
        config.load_env_file(tmp_path / "absent.env")


def _openai_stream(transport, state, script):
    import asyncio
    import httpx as _httpx
    from openai import AsyncOpenAI

    state["chat_script"] = list(script)

    async def run():
        client = AsyncOpenAI(
            base_url="http://llm.local/v1", api_key="test",
            http_client=_httpx.AsyncClient(transport=transport))
        chunks = []
        stream = await client.chat.completions.create(
            model="c", stream=True,
            messages=[{"role": "user", "content": "hi"}])
        async for chunk in stream:
            chunks.append(chunk)
        await client.close()
        return chunks

    return asyncio.run(run())


def test_fake_streams_content(fake_llm):
    transport, state = fake_llm
    chunks = _openai_stream(transport, state, [{"text": "Hello world"}])
    text = "".join(c.choices[0].delta.content or "" for c in chunks
                   if c.choices)
    assert text == "Hello world"
    assert chunks[-1].choices[0].finish_reason == "stop"
    assert state["chat_requests"], "stream requests must be recorded"


def test_fake_streams_tool_call(fake_llm):
    transport, state = fake_llm
    chunks = _openai_stream(
        transport, state,
        [{"tool": {"name": "search_docs", "arguments": {"query": "refund"}}}])
    calls = [tc for c in chunks if c.choices
             for tc in (c.choices[0].delta.tool_calls or [])]
    assert calls and calls[0].function.name == "search_docs"
    assert chunks[-1].choices[0].finish_reason == "tool_calls"
