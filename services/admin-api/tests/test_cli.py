"""The admin API's command line."""

import sys

from knowledge_admin import cli, logs


def test_uvicorn_gives_open_session_streams_five_seconds_to_close_on_shutdown(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, 'argv', ['knowledge-admin', 'target'])
    monkeypatch.setattr(cli, 'load_env_file', lambda path: None)
    monkeypatch.setattr(cli, 'load_config', lambda target: type('Cfg', (), {'server': None})())
    monkeypatch.setattr(cli, 'resolve_server', lambda server: 'server')
    monkeypatch.setattr(cli, 'create_app', lambda *args, **kwargs: 'app')
    monkeypatch.delenv('CREDENTIAL_ENCRYPTION_KEY', raising=False)
    monkeypatch.delenv('LOG_LEVEL', raising=False)
    monkeypatch.setattr(cli, 'configure_logging', lambda level: 'info')
    monkeypatch.setattr(cli.uvicorn, 'run', lambda app, **kwargs: calls.append((app, kwargs)))
    assert cli.main() == 0
    assert calls == [('app', {'host': '127.0.0.1', 'port': 8766, 'timeout_graceful_shutdown': 5, 'log_level': 'info'})]


def test_log_level_configures_logging_and_uvicorn_and_a_bad_one_stops_the_start(monkeypatch, capsys):
    calls, levels = [], []
    monkeypatch.setattr(sys, 'argv', ['knowledge-admin', 'target'])
    monkeypatch.setattr(cli, 'load_env_file', lambda path: None)
    monkeypatch.setattr(cli, 'load_config', lambda target: type('Cfg', (), {'server': None})())
    monkeypatch.setattr(cli, 'resolve_server', lambda server: 'server')
    monkeypatch.setattr(cli, 'create_app', lambda *args, **kwargs: 'app')
    monkeypatch.delenv('CREDENTIAL_ENCRYPTION_KEY', raising=False)
    monkeypatch.setattr(cli, 'configure_logging', lambda level: levels.append(level) or level.lower())
    monkeypatch.setattr(cli.uvicorn, 'run', lambda app, **kwargs: calls.append(kwargs['log_level']))
    monkeypatch.setenv('LOG_LEVEL', 'DEBUG')
    assert cli.main() == 0
    assert levels == ['DEBUG'] and calls == ['debug']
    monkeypatch.setattr(cli, 'configure_logging', logs.configure)
    monkeypatch.setenv('LOG_LEVEL', 'verbose')
    assert cli.main() == 2
    assert 'LOG_LEVEL must be debug, info or warning, not "verbose"' in capsys.readouterr().err
    assert calls == ['debug']


def test_the_admin_api_starts_without_a_rag_url(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(sys, 'argv', ['knowledge-admin', str(tmp_path)])
    monkeypatch.setattr(cli, 'load_env_file', lambda path: None)
    monkeypatch.delenv('DOCS_WIKI_RAG_URL', raising=False)
    monkeypatch.delenv('CREDENTIAL_ENCRYPTION_KEY', raising=False)
    monkeypatch.setattr(cli, 'configure_logging', lambda level: 'info')
    monkeypatch.setattr(cli.uvicorn, 'run', lambda app, **kwargs: calls.append(app))
    assert cli.main() == 0
    assert len(calls) == 1
    assert not hasattr(cli, 'httpx')
