"""The admin API's command line."""

import sys

from knowledge_admin import cli


def test_uvicorn_gives_open_session_streams_five_seconds_to_close_on_shutdown(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, 'argv', ['knowledge-admin', 'target'])
    monkeypatch.setattr(cli, 'load_env_file', lambda path: None)
    monkeypatch.setattr(cli, 'load_config', lambda target: type('Cfg', (), {'server': None, 'rag': None})())
    monkeypatch.setattr(cli, 'resolve_server', lambda server: 'server')
    monkeypatch.setattr(cli, 'resolve_rag', lambda rag: 'http://rag.test')
    monkeypatch.setattr(cli, 'create_app', lambda *args, **kwargs: 'app')
    monkeypatch.delenv('CREDENTIAL_ENCRYPTION_KEY', raising=False)
    monkeypatch.setattr(cli.uvicorn, 'run', lambda app, **kwargs: calls.append((app, kwargs)))
    assert cli.main() == 0
    assert calls == [('app', {'host': '127.0.0.1', 'port': 8766, 'timeout_graceful_shutdown': 5})]
