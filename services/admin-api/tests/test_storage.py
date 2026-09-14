import pytest

from test_api import _client

HEADERS = {'X-Forwarded-Groups': 'docs-admins'}


@pytest.fixture
def storage(tmp_path, monkeypatch):
    root = tmp_path / 'code'
    root.mkdir()
    (root / 'billing').mkdir()
    (root / 'billing' / 'README.md').write_text('# Billing\n', encoding='utf-8')
    monkeypatch.setenv('CODE_ROOT', str(root))
    monkeypatch.setenv('CODE_PATH', '../.local/code')
    monkeypatch.setenv('RAG_SOURCE_ROOT', str(tmp_path / 'missing-rag'))
    monkeypatch.setenv('RG_DATA', '/host/rag')
    monkeypatch.delenv('DOCUMENT_OUTPUT_ROOT', raising=False)
    monkeypatch.delenv('GENERATED_DOCS_PATH', raising=False)
    monkeypatch.delenv('BOOTSTRAP_DOCS_ROOT', raising=False)
    monkeypatch.delenv('DOCS_PATH', raising=False)
    monkeypatch.setenv('CREDENTIAL_ENCRYPTION_KEY', 'must-not-appear')
    return _client(tmp_path), root


def test_storage_overview_reports_configured_paths_without_inventing_host_resolution(storage):
    client, root = storage
    response = client.get('/api/storage', headers=HEADERS)
    assert response.status_code == 200
    locations = {value['id']: value for value in response.json()['locations']}
    assert locations['code']['container_path'] == str(root)
    assert locations['code']['host_path'] == '../.local/code'
    assert locations['code']['host_path_relative'] is True
    assert locations['code']['status'] == 'available'
    assert locations['rag']['status'] == 'unavailable'
    assert locations['rag']['host_path_relative'] is False
    assert locations['documents']['status'] == 'unconfigured'
    assert locations['documents']['host_path'] is None
    assert 'must-not-appear' not in response.text


@pytest.mark.parametrize('path', ['/api/storage', '/api/storage/code/entries', '/api/storage/code/file?path=billing/README.md'])
def test_storage_requires_admin_before_reading_paths(storage, path):
    client, _ = storage
    assert client.get(path).status_code == 403


def test_browse_nested_folders_and_preview_text_without_writing(storage):
    client, root = storage
    original = (root / 'billing' / 'README.md').read_bytes()
    folders = client.get('/api/storage/code/entries', headers=HEADERS).json()
    assert folders['total'] == 1 and folders['entries'][0]['kind'] == 'folder'
    entries = client.get('/api/storage/code/entries', params={'path': 'billing'}, headers=HEADERS).json()
    assert entries['entries'][0]['path'] == 'billing/README.md'
    assert entries['entries'][0]['size'] == len(original)
    response = client.get('/api/storage/code/file', params={'path': 'billing/README.md'}, headers=HEADERS)
    assert response.json()['text'] == original.decode()
    assert (root / 'billing' / 'README.md').read_bytes() == original
    assert client.put('/api/storage/code/file', headers=HEADERS).status_code == 405


@pytest.mark.parametrize('path', ['../outside', '/etc/passwd', 'billing/../billing', 'billing//README.md', 'billing\\README.md', 'billing/\x00'])
def test_storage_rejects_escape_and_invalid_paths(storage, path):
    client, _ = storage
    assert client.get('/api/storage/code/file', params={'path': path}, headers=HEADERS).status_code in (400, 403)


def test_storage_does_not_follow_links_or_open_special_files(storage, tmp_path):
    client, root = storage
    secret = tmp_path / 'outside.txt'
    secret.write_text('outside content')
    (root / 'linked').symlink_to(secret)
    (root / 'linked-folder').symlink_to(tmp_path, target_is_directory=True)
    assert client.get('/api/storage/code/file', params={'path': 'linked'}, headers=HEADERS).status_code == 403
    assert client.get('/api/storage/code/file', params={'path': 'linked-folder/outside.txt'}, headers=HEADERS).status_code == 403
    response = client.get('/api/storage/code/entries', headers=HEADERS).json()
    assert next(e for e in response['entries'] if e['name'] == 'linked')['kind'] == 'symlink'
    assert client.get('/api/storage/rag/entries', headers=HEADERS).status_code == 503
    assert client.get('/api/storage/arbitrary/entries', headers=HEADERS).status_code == 400


def test_storage_paginates_directories_and_bounds_file_previews(storage):
    client, root = storage
    for i in range(205):
        (root / f'file-{i:03}.txt').write_text('sample')
    first = client.get('/api/storage/code/entries', headers=HEADERS).json()
    second = client.get('/api/storage/code/entries?offset=200', headers=HEADERS).json()
    assert first['total'] == 206 and len(first['entries']) == 200 and first['next_offset'] == 200
    assert len(second['entries']) == 6 and second['next_offset'] is None
    assert not {e['path'] for e in first['entries']} & {e['path'] for e in second['entries']}
    (root / 'large.txt').write_bytes(b'x' * 512001)
    (root / 'binary.dat').write_bytes(b'\x00\xff')
    assert client.get('/api/storage/code/file?path=large.txt', headers=HEADERS).status_code == 413
    assert client.get('/api/storage/code/file?path=binary.dat', headers=HEADERS).status_code == 415
