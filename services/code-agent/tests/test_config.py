import os

import pytest

from code_agent import config


def test_missing_name_names_repo_index(tmp_path):
    repo_path = tmp_path / "app"
    repo_path.mkdir()
    bad = tmp_path / "no-name.toml"
    bad.write_text(f'[[repos]]\npath = "{repo_path}"\n', encoding="utf-8")
    with pytest.raises(config.ConfigError, match=r"repos\[0\]"):
        config.load_config(bad)


def test_missing_path_names_repo_index(tmp_path):
    bad = tmp_path / "no-path.toml"
    bad.write_text('[[repos]]\nname = "app"\n', encoding="utf-8")
    with pytest.raises(config.ConfigError, match=r"repos\[0\]"):
        config.load_config(bad)


def test_second_entry_missing_field_names_its_index(tmp_path):
    repo_path = tmp_path / "app"
    repo_path.mkdir()
    bad = tmp_path / "second-bad.toml"
    bad.write_text(
        f'[[repos]]\nname = "a"\npath = "{repo_path}"\n\n'
        '[[repos]]\npath = "x"\n', encoding="utf-8")
    with pytest.raises(config.ConfigError, match=r"repos\[1\]"):
        config.load_config(bad)


def test_nonexistent_repo_path_errors(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'[[repos]]\nname = "app"\npath = "{tmp_path / "missing"}"\n',
        encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load_config(cfg_file)


def test_missing_config_file_errors(tmp_path):
    with pytest.raises(config.ConfigError):
        config.load_config(tmp_path / "nope.toml")


def test_load_env_file_sets_missing_vars(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("CODE_AGENT_MODEL=from-file\n", encoding="utf-8")
    config.load_env_file(env_file)
    assert os.environ["CODE_AGENT_MODEL"] == "from-file"


def test_load_env_file_does_not_override_existing(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_AGENT_MODEL", "already-set")
    env_file = tmp_path / ".env"
    env_file.write_text("CODE_AGENT_MODEL=from-file\n", encoding="utf-8")
    config.load_env_file(env_file)
    assert os.environ["CODE_AGENT_MODEL"] == "already-set"


def test_load_env_file_missing_explicit_path_errors(tmp_path):
    with pytest.raises(config.ConfigError):
        config.load_env_file(tmp_path / "nope.env")


def test_load_env_file_none_with_no_default_is_noop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config.load_env_file(None)
    assert "CODE_AGENT_MODEL" not in os.environ


def test_repository_config_does_not_need_runtime(tmp_path):
    repo = tmp_path / 'app'
    repo.mkdir()
    path = tmp_path / 'config.toml'
    path.write_text(f'[[repos]]\nname = "app"\npath = "{repo}"\n')
    assert config.load_config(path) == config.Config([config.Repo('app', repo.resolve())])
