import pytest

from knowledge_admin.data_root import (
    DATA_FOLDER_RULE,
    FOLDER_RULE,
    data_dir,
    data_folder,
    documentation_folder,
    generated_docs_folder,
    host_path,
    jira_folder,
)


def test_paths_follow_the_container_data_root(monkeypatch):
    monkeypatch.setenv('DATA_ROOT_DIR', '/work/data')
    monkeypatch.setenv('DATA_ROOT', '/opt/knowledge agent/')
    monkeypatch.setenv('JIRA_EXPORT_ROOT', '/work/data/exports/jira')
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


def test_documentation_folders_follow_the_rag_segment_rule():
    for good in ('payments', 'team/payments', 'v1.2_docs-x', 'a/b-c', 'team/services'):
        assert documentation_folder(good) == good
    for bad in (
        '',
        '/payments',
        'payments/',
        'a//b',
        '../payments',
        'team/..',
        '.hidden',
        'team/.git',
        'my docs',
        'team/node_modules',
        'node_modules',
        'services',
        'services/payments',
        'é',
        'x' * 201,
        'Payments',
        'team/Payments',
        'A/b-c',
    ):
        with pytest.raises(ValueError) as error:
            documentation_folder(bad)
        assert str(error.value) == FOLDER_RULE
    assert FOLDER_RULE.startswith(
        'A documentation folder is a path such as payments or team/payments: each segment uses lowercase letters'
    )


def test_generated_documents_folder_is_relative_to_the_data_root(monkeypatch):
    monkeypatch.setenv('DATA_ROOT_DIR', '/work/data')
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', '/work/data/exports/docs')
    assert generated_docs_folder() == 'exports/docs'
    monkeypatch.delenv('DOCUMENT_OUTPUT_ROOT')
    assert generated_docs_folder() == 'generated-documents'
    for outside in ('/work/other/docs', '/work/data'):
        monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', outside)
        with pytest.raises(ValueError, match='DOCUMENT_OUTPUT_ROOT must be a folder inside DATA_ROOT_DIR'):
            generated_docs_folder()


def test_data_folders_follow_the_rag_rule():
    for good in ('jira', 'code/payments', 'Team/V1.2_x-y'):
        assert data_folder(good) == good
    for bad in ('', '/etc', '../etc', 'code/../etc', '.git', 'code/.hidden', 'a//b', 'my folder', 'x' * 501):
        with pytest.raises(ValueError) as error:
            data_folder(bad)
        assert str(error.value) == DATA_FOLDER_RULE
