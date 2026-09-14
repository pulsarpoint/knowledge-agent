"""Exercise source access against a real Git index and untrusted file paths."""
import subprocess
from pathlib import Path
from code_agent.config import Repo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from knowledge_llm import ModelUnavailable

from code_agent import source_tools


@pytest.fixture
def source_api(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    root.mkdir()
    (root / 'src').mkdir()
    (root / 'src/main.py').write_text('start\nFind.Me\nfinish\n')
    (root / 'README.md').write_text('Overview\nfind.me here\n')
    (root / 'binary').write_bytes(b'\x00Find.Me')
    (root / 'large').write_bytes(b'x' * (source_tools.MAX_FILE_BYTES + 1))
    subprocess.run(['git', 'init', str(root)], check=True, capture_output=True)
    (root / 'outside-link').symlink_to(tmp_path / 'private')
    (tmp_path / 'private').write_text('PRIVATE')
    (root / 'inside-link').symlink_to(root / 'untracked')
    (root / 'git-link').symlink_to(root / '.git/config')
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True, capture_output=True)
    (root / 'untracked').write_text('LOCAL-ONLY')
    selected = {'revision': 7, 'repositories_enabled': True, 'all_repositories': False, 'repository_ids': ['allowed']}
    monkeypatch.setattr(source_tools, 'load_profile', lambda agent_id: selected)
    app = FastAPI()
    app.include_router(source_tools.build_api(lambda: [(Repo(name=name, path=root), None) for name in ['allowed', 'outside']]))
    return TestClient(app), selected


def test_browse_page_read_lines_and_literal_search(source_api):
    client, _ = source_api
    params = {'agent_id': 'custom', 'revision': 7}
    rows = client.get('/repos/allowed/files', params={**params, 'prefix': 'src/'}).json()
    assert rows == {'files': ['src/main.py'], 'total': 1, 'next_offset': None}
    first = client.get('/repos/allowed/files', params={**params, 'limit': 1}).json()
    second = client.get('/repos/allowed/files', params={**params, 'limit': 1, 'offset': first['next_offset']}).json()
    assert first['files'] != second['files'] and first['total'] == second['total']
    result = client.get('/repos/allowed/file', params={**params, 'path': 'src/main.py', 'start_line': 2, 'line_count': 1})
    assert result.status_code == 200 and result.json()['text'] == '2: Find.Me' and result.json()['total_lines'] == 3
    search = client.get('/repos/allowed/search', params={**params, 'query': 'find.me'}).json()
    assert [(m['path'], m['line']) for m in search['matches']] == [('README.md', 2), ('src/main.py', 2)]
    assert not search['truncated']
    assert client.get('/repos/allowed/search', params={**params, 'query': 'find.*'}).json()['matches'] == []


def test_local_code_project_spans_mapped_folders_without_exposing_other_code(tmp_path, monkeypatch):
    for folder in ('backend','shared','unselected'):
        (tmp_path/folder).mkdir()
        (tmp_path/folder/'main.py').write_text(f'SOURCE {folder}\n')
    (tmp_path/'backend'/'escape').symlink_to(tmp_path/'unselected', target_is_directory=True)
    project = Repo(name='payments', path=tmp_path, folder_paths=('backend','shared'))
    monkeypatch.setattr(source_tools, 'load_profile', lambda _: {'revision':1,'repositories_enabled':True,'all_repositories':False,'repository_ids':['payments']})
    app = FastAPI(); app.include_router(source_tools.build_api(lambda: [(project,'Payments')]))
    client = TestClient(app)
    params = {'agent_id':'code','revision':1}
    assert client.get('/repos/payments/files', params=params).json()['files'] == ['backend/main.py','shared/main.py']
    assert 'SOURCE shared' in client.get('/repos/payments/file', params={**params,'path':'shared/main.py'}).json()['text']
    assert {m['path'] for m in client.get('/repos/payments/search', params={**params,'query':'SOURCE'}).json()['matches']} == {'backend/main.py','shared/main.py'}
    for path in ('unselected/main.py','backend/escape/main.py','../outside'):
        assert client.get('/repos/payments/file', params={**params,'path':path}).status_code == 404


@pytest.mark.parametrize('path', ['../private', '/etc/passwd', '.git/config', 'untracked', 'outside-link', 'inside-link', 'git-link', 'binary', 'large'])
def test_unsafe_untracked_binary_and_large_files_are_not_read(source_api, path):
    client, _ = source_api
    result = client.get('/repos/allowed/file', params={'agent_id': 'custom', 'revision': 7, 'path': path})
    assert result.status_code in (400, 403, 404, 413)
    assert 'PRIVATE' not in result.text and 'LOCAL-ONLY' not in result.text


@pytest.mark.parametrize('operation,extra', [('files', {}), ('file', {'path': 'README.md'}), ('search', {'query': 'Overview'})])
def test_every_source_operation_enforces_profile_and_revision(source_api, monkeypatch, operation, extra):
    client, selected = source_api
    params = {'agent_id': 'custom', 'revision': 7, **extra}
    assert client.get(f'/repos/outside/{operation}', params=params).status_code == 403
    assert client.get(f'/repos/allowed/{operation}', params={**params, 'revision': 6}).status_code == 403
    selected['repositories_enabled'] = False
    assert client.get(f'/repos/allowed/{operation}', params=params).status_code == 403
    def disabled(agent_id):
        raise ModelUnavailable('Agent disabled')
    monkeypatch.setattr(source_tools, 'load_profile', disabled)
    assert client.get(f'/repos/allowed/{operation}', params=params).status_code == 409
