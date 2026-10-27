import pytest

from knowledge_admin.data_root import (
    DATA_FOLDER_RULE,
    data_dir,
    data_folder,
    host_path,
)


def test_paths_follow_the_container_data_root(monkeypatch):
    monkeypatch.setenv('DATA_ROOT_DIR', '/work/data')
    monkeypatch.setenv('DATA_ROOT', '/opt/knowledge agent/')
    assert host_path('/work/data') == '/opt/knowledge agent'
    assert host_path('/work/data/code') == '/opt/knowledge agent/code'
    assert host_path('/elsewhere/code') is None and host_path(None) is None
    monkeypatch.delenv('DATA_ROOT')
    assert host_path('/work/data/code') is None


def test_defaults_and_misconfiguration(monkeypatch):
    for name in ('DATA_ROOT_DIR', 'DATA_ROOT'):
        monkeypatch.delenv(name, raising=False)
    assert str(data_dir()) == '/work/data'
    monkeypatch.setenv('DATA_ROOT_DIR', 'relative')
    with pytest.raises(ValueError, match='DATA_ROOT_DIR'):
        data_dir()


def test_data_folders_follow_the_rag_rule():
    for good in ('jira', 'code/payments', 'Team/V1.2_x-y'):
        assert data_folder(good) == good
    for bad in ('', '/etc', '../etc', 'code/../etc', '.git', 'code/.hidden', 'a//b', 'my folder', 'x' * 501):
        with pytest.raises(ValueError) as error:
            data_folder(bad)
        assert str(error.value) == DATA_FOLDER_RULE
