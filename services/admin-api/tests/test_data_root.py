import pytest

from knowledge_admin.data_root import data_dir, data_path, host_path, jira_folder


def test_paths_follow_the_container_data_root(monkeypatch):
    monkeypatch.setenv('DATA_ROOT_DIR', '/work/data')
    monkeypatch.setenv('DATA_ROOT', '/opt/knowledge agent/')
    monkeypatch.setenv('JIRA_EXPORT_ROOT', '/work/data/exports/jira')
    assert str(data_path('exports/tools')) == '/work/data/exports/tools'
    assert host_path('/work/data') == '/opt/knowledge agent'
    assert host_path('/work/data/code') == '/opt/knowledge agent/code'
    assert host_path('/elsewhere/code') is None and host_path(None) is None
    assert jira_folder() == 'exports/jira'
    monkeypatch.delenv('DATA_ROOT')
    assert host_path('/work/data/code') is None


def test_defaults_and_misconfiguration(monkeypatch):
    for name in ('DATA_ROOT_DIR', 'JIRA_EXPORT_ROOT', 'DATA_ROOT'):
        monkeypatch.delenv(name, raising=False)
    assert str(data_dir()) == '/work/data' and jira_folder() == 'jira'
    monkeypatch.setenv('DATA_ROOT_DIR', 'relative')
    with pytest.raises(ValueError, match='DATA_ROOT_DIR'):
        data_dir()
    monkeypatch.setenv('DATA_ROOT_DIR', '/work/data')
    for outside in ('/work/other/jira', '/work/data'):
        monkeypatch.setenv('JIRA_EXPORT_ROOT', outside)
        with pytest.raises(ValueError, match='JIRA_EXPORT_ROOT'):
            jira_folder()
