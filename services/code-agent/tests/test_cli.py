import os

import pytest

from code_agent import cli


def _write_config(tmp_path, repo_path, backend: str | None = None) -> str:
    cfg_file = tmp_path / "config.toml"
    text = f'[[repos]]\nname = "app"\npath = "{repo_path}"\n'
    if backend:
        text += f'\n[runtime]\nbackend = "{backend}"\n'
    cfg_file.write_text(text, encoding="utf-8")
    return str(cfg_file)


def test_serve_missing_target_exits_2(tmp_path, capsys):
    assert cli.main(["serve", str(tmp_path / "missing.toml")]) == 2
    assert "error:" in capsys.readouterr().err


def test_serve_env_file_loads_vars(tmp_path, monkeypatch):
    repo_path = tmp_path / "app"
    repo_path.mkdir()
    cfg_file = _write_config(tmp_path, repo_path)
    env_file = tmp_path / ".env"
    env_file.write_text("CODE_AGENT_MODEL=from-env-file\n", encoding="utf-8")
    calls = {}
    monkeypatch.setattr(
        cli, "_run_server",
        lambda app, host, port: calls.setdefault("served", True))

    assert cli.main(["serve", cfg_file, "--env-file", str(env_file)]) == 0
    assert calls["served"]
    assert os.environ["CODE_AGENT_MODEL"] == "from-env-file"


def test_serve_default_port_is_8795(tmp_path, monkeypatch):
    # The plan fixes the canonical service port at 8795 (clear of
    # docs-wiki 8765 and rag 8790); the deploy Dockerfile EXPOSEs 8795
    # and its CMD never passes --port, so the default here must match or
    # a container binds the wrong port silently.
    repo_path = tmp_path / "app"
    repo_path.mkdir()
    cfg_file = _write_config(tmp_path, repo_path)
    captured = {}
    monkeypatch.setattr(
        cli, "_run_server",
        lambda app, host, port: captured.setdefault("port", port))

    assert cli.main(["serve", cfg_file]) == 0
    assert captured["port"] == 8795


def test_serve_starts_server_with_resolved_config(tmp_path, monkeypatch):
    repo_path = tmp_path / "app"
    repo_path.mkdir()
    cfg_file = _write_config(tmp_path, repo_path)
    captured = {}

    def fake_run_server(app, host, port):
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr(cli, "_run_server", fake_run_server)
    assert cli.main(["serve", cfg_file, "--host", "0.0.0.0",
                     "--port", "9001"]) == 0
    assert captured == {"host": "0.0.0.0", "port": 9001}
