"""The admin API's command line."""

import sys

from knowledge_admin import cli


def test_uvicorn_gives_open_session_streams_five_seconds_to_close_on_shutdown(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, 'argv', ['knowledge-admin', 'target'])
    monkeypatch.setattr(cli, 'load_env_file', lambda path: None)
    monkeypatch.setattr(cli, 'load_config', lambda target: type('Cfg', (), {'server': None})())
    monkeypatch.setattr(cli, 'resolve_server', lambda server: 'server')
    monkeypatch.setattr(cli, 'create_app', lambda *args, **kwargs: 'app')
    monkeypatch.delenv('CREDENTIAL_ENCRYPTION_KEY', raising=False)
    monkeypatch.setattr(cli.uvicorn, 'run', lambda app, **kwargs: calls.append((app, kwargs)))
    assert cli.main() == 0
    assert calls == [('app', {'host': '127.0.0.1', 'port': 8766, 'timeout_graceful_shutdown': 5})]


def test_the_admin_api_starts_without_a_rag_url(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(sys, 'argv', ['knowledge-admin', str(tmp_path)])
    monkeypatch.setattr(cli, 'load_env_file', lambda path: None)
    monkeypatch.delenv('DOCS_WIKI_RAG_URL', raising=False)
    monkeypatch.delenv('CREDENTIAL_ENCRYPTION_KEY', raising=False)
    monkeypatch.setattr(cli.uvicorn, 'run', lambda app, **kwargs: calls.append(app))
    assert cli.main() == 0
    assert len(calls) == 1
    assert not hasattr(cli, 'httpx')
