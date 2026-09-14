from pathlib import Path

import pytest

from docs_wiki import config


def test_bare_folder_auto_wraps(tmp_path):
    docs = tmp_path / "my-app-docs"
    docs.mkdir()
    cfg = config.load_config(docs)
    assert len(cfg.apps) == 1
    assert cfg.apps[0].name == "my-app-docs"
    assert cfg.apps[0].app_root is None
    assert cfg.llm is None


def write_toml(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_toml_multi_app(tmp_path):
    (tmp_path / "a-root").mkdir()
    cfg_file = write_toml(tmp_path / "cfg.toml", f"""
[[apps]]
name = "alpha"
app_root = "{tmp_path / 'a-root'}"

[[apps]]
name = "beta"
""")
    cfg = config.load_config(cfg_file)
    assert [a.name for a in cfg.apps] == ["alpha", "beta"]
    assert cfg.apps[0].app_root == (tmp_path / "a-root").resolve()
    assert cfg.apps[1].app_root is None


def test_duplicate_names_rejected(tmp_path):
    cfg_file = write_toml(tmp_path / "cfg.toml", """
[[apps]]
name = "x"

[[apps]]
name = "x"
""")
    with pytest.raises(config.ConfigError):
        config.load_config(cfg_file)


def test_docs_key_always_rejected(tmp_path):
    # docs-wiki never reads a local docs folder for an app — the RAG
    # service owns indexing via its own `[[collections]]` config — so a
    # `docs` key is rejected unconditionally, whether or not `[rag]` is
    # configured in the same file.
    cfg_file = write_toml(tmp_path / "cfg.toml", """
[[apps]]
name = "shop"
docs = "/wherever"
""")
    with pytest.raises(
            config.ConfigError,
            match=r"app 'shop': docs moved to the RAG service config"):
        config.load_config(cfg_file)


def test_docs_key_rejected_even_with_rag_configured(tmp_path):
    cfg_file = write_toml(tmp_path / "cfg.toml", """
[rag]
url = "http://rag.local:8790"

[[apps]]
name = "shop"
docs = "/wherever"
""")
    with pytest.raises(
            config.ConfigError,
            match=r"app 'shop': docs moved to the RAG service config"):
        config.load_config(cfg_file)


def test_missing_app_root_rejected(tmp_path):
    cfg_file = write_toml(tmp_path / "cfg.toml", f"""
[[apps]]
name = "x"
app_root = "{tmp_path / 'nope'}"
""")
    with pytest.raises(config.ConfigError):
        config.load_config(cfg_file)


def test_missing_target_rejected(tmp_path):
    with pytest.raises(config.ConfigError):
        config.load_config(tmp_path / "absent.toml")


@pytest.mark.parametrize("body", [
    'apps = "oops"',
    "apps = [1, 2]",
    '[[apps]]\nname = 123',
    '[[apps]]\nname = "x"\napp_root = true',
])
def test_malformed_types_raise_config_error(tmp_path, body):
    cfg_file = write_toml(tmp_path / "cfg.toml", body)
    with pytest.raises(config.ConfigError):
        config.load_config(cfg_file)


def test_server_section_must_be_table(tmp_path):
    cfg_file = tmp_path / "cfg.toml"
    cfg_file.write_text('server = 5\n[[apps]]\nname = "x"\n',
                        encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load_config(cfg_file)


def test_resolve_server_defaults_and_overrides():
    assert config.resolve_server(None, env={}) == config.ServerConfig(
        cors_origin=None, session_retention_days=90)
    resolved = config.resolve_server(
        {"cors_origin": "http://localhost:5173", "session_retention_days": 7},
        env={})
    assert resolved.cors_origin == "http://localhost:5173"
    assert resolved.session_retention_days == 7
    over = config.resolve_server({"cors_origin": "http://file.local"},
                                 env={"DOCS_WIKI_CORS_ORIGIN": "http://env.local"})
    assert over.cors_origin == "http://env.local"


def test_resolve_server_rejects_bad_types():
    with pytest.raises(config.ConfigError):
        config.resolve_server({"cors_origin": 5}, env={})
    with pytest.raises(config.ConfigError):
        config.resolve_server({"session_retention_days": "soon"}, env={})
    with pytest.raises(config.ConfigError):
        config.resolve_server({"session_retention_days": -1}, env={})
    with pytest.raises(config.ConfigError):
        config.resolve_server({"session_retention_days": True}, env={})


def test_resolve_server_ui_dist():
    assert config.resolve_server(None, env={}).ui_dist is None
    resolved = config.resolve_server({"ui_dist": "/builds/ui"}, env={})
    assert resolved.ui_dist == "/builds/ui"
    over = config.resolve_server({"ui_dist": "/file"},
                                 env={"DOCS_WIKI_UI_DIST": "/env"})
    assert over.ui_dist == "/env"
    with pytest.raises(config.ConfigError):
        config.resolve_server({"ui_dist": 7}, env={})


def test_rag_section_must_be_table(tmp_path):
    cfg_file = tmp_path / "cfg.toml"
    cfg_file.write_text('rag = 5\n[[apps]]\nname = "x"\n', encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load_config(cfg_file)


def test_resolve_rag_env_overrides_file():
    assert config.resolve_rag(
        {"url": "http://file.local:8790"}, env={}) == "http://file.local:8790"
    assert config.resolve_rag(
        {"url": "http://file.local:8790"},
        env={"DOCS_WIKI_RAG_URL": "http://env.local:8790"},
    ) == "http://env.local:8790"
    assert config.resolve_rag(None, env={}) is None
    assert config.resolve_rag({"url": ""}, env={}) is None
    assert config.resolve_rag(
        None, env={"DOCS_WIKI_RAG_URL": "http://env.local:8790"}
    ) == "http://env.local:8790"


def test_resolve_server_state_dir_and_default():
    default = config.resolve_server(None, env={})
    assert default.state_dir is None
    assert (config.state_root(default)
           == Path.home() / ".local" / "state" / "docs-wiki")

    resolved = config.resolve_server({"state_dir": "/custom/state"}, env={})
    assert resolved.state_dir == "/custom/state"
    assert config.state_root(resolved) == Path("/custom/state")

    over = config.resolve_server(
        {"state_dir": "/file/state"},
        env={"DOCS_WIKI_STATE_DIR": "/env/state"})
    assert over.state_dir == "/env/state"

    tilde = config.resolve_server({"state_dir": "~/state"}, env={})
    assert config.state_root(tilde) == Path.home() / "state"

    with pytest.raises(config.ConfigError):
        config.resolve_server({"state_dir": 7}, env={})


def test_app_without_docs_succeeds_with_rag_configured(tmp_path):
    cfg_file = write_toml(tmp_path / "cfg.toml", """
[rag]
url = "http://rag.local:8790"

[[apps]]
name = "shop"
""")
    cfg = config.load_config(cfg_file)
    assert cfg.apps[0].name == "shop"
    assert cfg.apps[0].app_root is None
    assert cfg.rag == {"url": "http://rag.local:8790"}


def test_code_agent_section_must_be_table(tmp_path):
    cfg_file = tmp_path / "cfg.toml"
    cfg_file.write_text('code_agent = 5\n[[apps]]\nname = "x"\n',
                        encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load_config(cfg_file)


def test_resolve_code_agent_env_overrides_file():
    assert config.resolve_code_agent(
        {"url": "http://file.local:8795"}, env={}) == "http://file.local:8795"
    assert config.resolve_code_agent(
        {"url": "http://file.local:8795"},
        env={"DOCS_WIKI_CODE_AGENT_URL": "http://env.local:8795"},
    ) == "http://env.local:8795"
    assert config.resolve_code_agent(None, env={}) is None
    assert config.resolve_code_agent({"url": ""}, env={}) is None
    assert config.resolve_code_agent(
        None, env={"DOCS_WIKI_CODE_AGENT_URL": "http://env.local:8795"}
    ) == "http://env.local:8795"


def test_config_exposes_code_agent_section(tmp_path):
    cfg_file = write_toml(tmp_path / "cfg.toml", """
[code_agent]
url = "http://code.local:8795"

[[apps]]
name = "shop"
""")
    cfg = config.load_config(cfg_file)
    assert cfg.apps[0].name == "shop"
    assert cfg.code_agent == {"url": "http://code.local:8795"}


def test_config_code_agent_defaults_to_none(tmp_path):
    cfg_file = write_toml(tmp_path / "cfg.toml", """
[[apps]]
name = "shop"
""")
    cfg = config.load_config(cfg_file)
    assert cfg.code_agent is None


def test_llm_max_turns_from_env_and_file():
    # Env wins; file works; absent -> None (agent applies its defaults).
    cfg = config.resolve_llm({"base_url": "http://x", "chat_model": "m",
                              "max_turns": 20}, env={})
    assert cfg.max_turns == 20
    cfg = config.resolve_llm({"base_url": "http://x", "chat_model": "m",
                              "max_turns": 20},
                             env={"DOCS_WIKI_MAX_TURNS": "30"})
    assert cfg.max_turns == 30
    cfg = config.resolve_llm({"base_url": "http://x", "chat_model": "m"}, env={})
    assert cfg.max_turns is None


def test_llm_max_turns_invalid_rejected():
    import pytest as _pytest
    with _pytest.raises(config.ConfigError, match="max_turns"):
        config.resolve_llm({"base_url": "http://x", "chat_model": "m"},
                           env={"DOCS_WIKI_MAX_TURNS": "zero"})
    with _pytest.raises(config.ConfigError, match="max_turns"):
        config.resolve_llm({"base_url": "http://x", "chat_model": "m",
                            "max_turns": 0}, env={})


def test_resolve_server_admin_group_default_and_env(monkeypatch):
    from docs_wiki.config import resolve_server
    assert resolve_server({}).admin_group == "docs-admins"
    assert resolve_server({}, env={"DOCS_WIKI_ADMIN_GROUP": "ops"}).admin_group == "ops"
