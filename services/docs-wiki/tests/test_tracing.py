from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest

from docs_wiki import tracing
from docs_wiki.config import App, Config, ServerConfig
from docs_wiki.webapp import create_app


@pytest.fixture(autouse=True)
def reset_tracing_state():
    tracing._enabled = False
    yield
    tracing._enabled = False


def test_setup_skipped_without_endpoint():
    assert tracing.setup(None, None) is False
    assert tracing.enabled() is False
    assert tracing.setup("", "key") is False


def test_setup_registers_phoenix(monkeypatch):
    register = MagicMock(return_value="tp")
    monkeypatch.setattr("phoenix.otel.register", register)
    assert tracing.setup("http://phoenix:6006/v1/traces", "secret") is True
    assert tracing.enabled() is True
    kwargs = register.call_args.kwargs
    assert kwargs["endpoint"] == "http://phoenix:6006/v1/traces"
    assert kwargs["project_name"] == "docs-wiki"
    assert kwargs["batch"] is True
    assert kwargs["api_key"] == "secret"


def test_setup_idempotent_when_enabled(monkeypatch):
    tracing._enabled = True
    register = MagicMock()
    monkeypatch.setattr("phoenix.otel.register", register)
    assert tracing.setup("http://phoenix:6006/v1/traces", "k") is True
    register.assert_not_called()


def test_setup_failure_logs_and_continues(monkeypatch, caplog):
    monkeypatch.setattr("phoenix.otel.register",
                        MagicMock(side_effect=RuntimeError("boom")))
    assert tracing.setup("http://phoenix:6006/v1/traces", None) is False
    assert tracing.enabled() is False
    assert any("tracing setup failed" in r.message for r in caplog.records)


def test_create_app_invokes_tracing_setup(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("docs_wiki.tracing.setup",
                        lambda endpoint, key: calls.append((endpoint, key)) or False)
    monkeypatch.setenv("DOCS_WIKI_PHOENIX_ENDPOINT", "http://x/v1/traces")
    monkeypatch.setenv("DOCS_WIKI_PHOENIX_API_KEY", "k")
    cfg = Config(apps=[App(name="shop", app_root=None)], llm=None)
    server = ServerConfig(cors_origin=None, session_retention_days=90,
                          state_dir=str(tmp_path / "state"))
    create_app(cfg, server=server)
    assert calls == [("http://x/v1/traces", "k")]
