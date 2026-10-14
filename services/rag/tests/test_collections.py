"""Collection folders: confinement, glob matching, skips and freshness."""

import os
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

from rag.collections import (
    DataRoot,
    FolderMissing,
    glob_regex,
    indexable,
    matches,
    overlaps,
    relative_path,
    source_path,
    visible,
)

FOLDERS = ('code', 'code-index', 'docs', 'generated-documents', 'jira')


@pytest.fixture
def data_root(tmp_path):
    base = tmp_path / 'data'
    roots = {name: base / name for name in FOLDERS}
    for root in roots.values():
        root.mkdir(parents=True)
    roots['data'] = base
    return DataRoot(base, '/srv/data', 'jira'), roots


@pytest.mark.parametrize(
    'value',
    ['jira', 'code/payments/src', 'generated-documents', 'docs', 'exports/tools', 'any-folder/v1.2', 'rag-data/demo'],
)
def test_valid_source_paths(value):
    assert source_path(value) == value


@pytest.mark.parametrize(
    'value', ['', 'jira/../code', 'jira/.hidden', '/jira', 'jira//x', 'code/a b', '.hidden', '..', 'code/', 'a' * 501]
)
def test_invalid_source_paths(value):
    with pytest.raises(HTTPException):
        source_path(value)


@pytest.mark.parametrize('value', ['../x', 'a/../b', '/abs', 'a\\b', 'a//b', '', 'a/\x00'])
def test_invalid_relative_paths(value):
    with pytest.raises(HTTPException):
        relative_path(value)


def test_glob_matching():
    assert matches('README.md', ['**/*.md'], [])
    assert matches('a/b/c.md', ['**/*.md'], [])
    assert not matches('a/b/c.py', ['**/*.md'], [])
    assert matches('src/app.py', ['src/**/*.py'], [])
    assert not matches('svc/x/v3/README.md', ['**/*.md'], ['**/v[0-9]*/**'])
    assert matches('svc/x/README.md', ['**/*.md'], ['**/v[0-9]*/**'])
    assert not matches('tests/test_a.py', ['**/*.py'], ['tests/**'])
    assert matches('a.txt', ['*.txt'], []) and not matches('d/a.txt', ['*.txt'], [])


def test_walk_filters_skips_and_reports_newest_change(data_root):
    data_root, roots = data_root
    repo = roots['code'] / 'payments'
    (repo / 'src').mkdir(parents=True)
    (repo / '.git').mkdir()
    (repo / 'node_modules').mkdir()
    (repo / 'src' / 'app.py').write_text('print(1)')
    (repo / 'README.md').write_text('# Payments')
    (repo / '.git' / 'config.md').write_text('x')
    (repo / 'node_modules' / 'x.md').write_text('x')
    (repo / '.env.md').write_text('SECRET=1')
    os.symlink(repo / 'README.md', repo / 'link.md')
    os.mkfifo(repo / 'pipe.md')
    old = time.time() - 3600
    os.utime(repo / 'README.md', (old, old))
    walk = data_root.walk('code/payments', ['**/*.md', '**/*.py'], [])
    assert [f['path'] for f in walk.files] == ['README.md', 'src/app.py']
    assert walk.skipped == {'links': 1, 'special': 1, 'unreadable': 0, 'invalid_names': 0}
    assert walk.newest_mtime >= (repo / 'src' / 'app.py').stat().st_mtime
    assert data_root.container_path('code/payments') == roots['code'] / 'payments'
    assert data_root.host_path('code/payments') == '/srv/data/code/payments'
    assert not data_root.writable('code/payments') and data_root.writable('jira') and not data_root.writable('jira2')


def test_deleting_a_file_moves_folder_change_time_forward(data_root):
    data_root, roots = data_root
    folder = roots['jira'] / 'jira'
    folder.mkdir()
    (folder / 'A.md').write_text('a')
    (folder / 'B.md').write_text('b')
    old = time.time() - 3600
    for path in (folder, folder / 'A.md', folder / 'B.md'):
        os.utime(path, (old, old))
    before = data_root.walk('jira/jira', ['**/*.md'], []).newest_mtime
    (folder / 'B.md').unlink()
    assert data_root.walk('jira/jira', ['**/*.md'], []).newest_mtime > before


def test_walk_limit_and_missing_folder(data_root):
    data_root, roots = data_root
    folder = roots['jira'] / 'big'
    folder.mkdir()
    for i in range(5):
        (folder / f'{i}.md').write_text('x')
    with pytest.raises(HTTPException, match='more than 3 files'):
        data_root.walk('jira/big', ['**/*.md'], [], limit=3)
    walk = data_root.walk('jira/big', ['**/*.md'], [], limit=3, truncate=True)
    assert walk.truncated and len(walk.files) == 3
    with pytest.raises(FolderMissing):
        data_root.walk('jira/absent', ['**/*.md'], [])


def test_read_write_confined_to_the_jira_folder(data_root):
    data_root, roots = data_root
    (roots['jira'] / 'notes').mkdir()
    data_root.write('jira/notes', 'sub/a.md', b'# A')
    assert data_root.read('jira/notes', 'sub/a.md', 100) == b'# A'
    with pytest.raises(HTTPException, match='size limit'):
        data_root.read('jira/notes', 'sub/a.md', 1)
    (roots['code'] / 'repo').mkdir()
    with pytest.raises(HTTPException, match='read-only') as error:
        data_root.write('code/repo', 'a.md', b'x')
    assert error.value.status_code == 403
    os.symlink(roots['code'], roots['jira'] / 'notes' / 'escape')
    with pytest.raises(OSError):
        data_root.read('jira/notes', 'escape/repo', 100)


def test_glob_classes_and_specials():
    assert matches('a.md', ['[!x].md'], []) and not matches('x.md', ['[!x].md'], [])
    assert matches('[].md', ['[].md'], []) and matches('[!].md', ['[!].md'], [])
    assert matches('a+b.md', ['a+b.md'], []) and not matches('aab.md', ['a+b.md'], [])
    assert matches('a/b/c', ['a/**'], []) and matches('a/b.md', ['a/**/b.md'], [])
    assert matches('a/x/y/b.md', ['a/**/b.md'], [])
    assert not matches('a/b', ['a[/]b'], []) and not matches('a/b', ['a[!x]b'], [])
    assert matches('a^b', ['a[\\^]b'], []) and matches('^', ['[^]'], [])
    assert matches('a\nb/c.md', ['**/c.md'], [])
    with pytest.raises(HTTPException) as error:
        glob_regex('[z-a]')
    assert error.value.status_code == 400


@pytest.mark.skipif(os.geteuid() == 0, reason='root ignores permissions')
def test_walk_counts_unreadable_directories(data_root):
    data_root, roots = data_root
    folder = roots['jira'] / 'p'
    (folder / 'locked').mkdir(parents=True)
    (folder / 'a.md').write_text('a')
    (folder / 'locked' / 'b.md').write_text('b')
    (folder / 'locked').chmod(0)
    try:
        walk = data_root.walk('jira/p', ['**/*.md'], [])
    finally:
        (folder / 'locked').chmod(0o755)
    assert [f['path'] for f in walk.files] == ['a.md']
    assert walk.skipped['unreadable'] == 1


def test_walk_skips_non_utf8_names(data_root):
    data_root, roots = data_root
    folder = roots['jira'] / 'p'
    folder.mkdir()
    (folder / 'a.md').write_text('a')
    try:
        with open(os.path.join(os.fsencode(folder), b'bad\xff.md'), 'w') as stream:
            stream.write('x')
    except OSError:
        pytest.skip('filesystem rejects non-UTF-8 names')
    walk = data_root.walk('jira/p', ['**/*.md'], [])
    assert [f['path'] for f in walk.files] == ['a.md']
    assert walk.skipped['invalid_names'] == 1


def test_walk_rejects_symlinked_collection_folder(data_root):
    data_root, roots = data_root
    os.symlink(roots['code'], roots['jira'] / 'link')
    with pytest.raises(HTTPException) as error:
        data_root.walk('jira/link', ['**/*.md'], [])
    assert error.value.status_code == 409


def test_walk_skips_surrogate_names_from_listing(data_root, monkeypatch):
    data_root, roots = data_root
    folder = roots['jira'] / 'p'
    folder.mkdir()
    (folder / 'a.md').write_text('a')
    real = os.listdir
    monkeypatch.setattr(os, 'listdir', lambda fd: [*real(fd), 'bad\udcff.md'])
    walk = data_root.walk('jira/p', ['**/*.md'], [])
    assert [f['path'] for f in walk.files] == ['a.md']
    assert walk.skipped['invalid_names'] == 1


DOCUMENTS_EXCLUDE = ['*/v[0-9]*/**', 'services/*/*/v[0-9]*/**']


def test_documents_exclude_keeps_slugs_that_start_with_a_version():
    include = ['**/*.md']
    assert matches('v2-api-design/README.md', include, DOCUMENTS_EXCLUDE)
    assert matches('payments-runbook/README.md', include, DOCUMENTS_EXCLUDE)
    assert matches('services/x/overview/overview.md', include, DOCUMENTS_EXCLUDE)
    assert not matches('payments-runbook/v3/README.md', include, DOCUMENTS_EXCLUDE)
    assert not matches('v2-api-design/v1/README.md', include, DOCUMENTS_EXCLUDE)
    assert not matches('services/x/overview/v2/overview.md', include, DOCUMENTS_EXCLUDE)


def test_indexable_refuses_hidden_env_and_skipped_segments():
    include = ['**/*']
    assert indexable('docs/a.md', include, [])
    for path in (
        '.env',
        '.env.local',
        'sub/.env.production',
        '.git/config',
        'a/.git/HEAD',
        'node_modules/x/README.md',
        '.github/workflows/ci.md',
        '.commit-update-test/README.md',
        'a/.hidden.md',
    ):
        assert not indexable(path, include, []), path
        assert not visible(path), path
    assert not indexable('app.py', ['**/*.md'], [])
    assert not indexable('v1/a.md', ['**/*.md'], ['v1/**'])


def test_walk_skips_hidden_directories_and_files(data_root):
    data_root, roots = data_root
    folder = roots['generated-documents']
    (folder / '.documentation-update-abc' / 'payments').mkdir(parents=True)
    (folder / '.documentation-update-abc' / 'payments' / 'README.md').write_text('# scratch')
    (folder / 'payments').mkdir()
    (folder / 'payments' / 'README.md').write_text('# Payments')
    (folder / 'payments' / '.draft.md').write_text('# hidden')
    walk = data_root.walk('generated-documents', ['**/*.md'], [])
    assert [f['path'] for f in walk.files] == ['payments/README.md']


def test_walk_caps_visited_entries(data_root):
    data_root, roots = data_root
    folder = roots['code'] / 'repo'
    (folder / 'src').mkdir(parents=True)
    for i in range(10):
        (folder / 'src' / f'{i}.py').write_text('x')
    (folder / 'README.md').write_text('# R')
    # No file matches the include patterns, yet every entry counts towards the cap.
    walk = data_root.walk('code/repo', ['**/*.rst'], [], truncate=True, max_entries=5)
    assert walk.truncated and walk.files == []
    with pytest.raises(HTTPException, match='more than 5 entries'):
        data_root.walk('code/repo', ['**/*.rst'], [], max_entries=5)
    assert not data_root.walk('code/repo', ['**/*.rst'], [], truncate=True, max_entries=100).truncated


def test_present_never_creates_folders(data_root):
    data_root, roots = data_root
    assert not data_root.present('jira/new') and not (roots['jira'] / 'new').exists()
    (roots['jira'] / 'new').mkdir()
    assert data_root.present('jira/new')
    os.symlink(roots['code'], roots['jira'] / 'link')
    assert not data_root.present('jira/link')


def test_first_write_creates_subfolders_only_below_the_jira_folder(data_root):
    data_root, roots = data_root
    data_root.write('jira', '2026/DEMO-1.md', b'# DEMO-1')
    assert (roots['jira'] / '2026' / 'DEMO-1.md').read_bytes() == b'# DEMO-1'
    with pytest.raises(HTTPException) as error:
        data_root.write('jira/absent', 'a.md', b'x')
    assert error.value.status_code == 409 and not (roots['jira'] / 'absent').exists()


def test_top_level_listing_shows_visible_folders_of_the_data_root(data_root):
    data_root, roots = data_root
    (roots['data'] / 'exports').mkdir()
    (roots['data'] / '.local-archive').mkdir()
    (roots['data'] / 'notes.md').write_text('x')
    os.symlink(roots['code'], roots['data'] / 'linked')
    assert data_root.subfolders(None) == (
        ['code', 'code-index', 'docs', 'exports', 'generated-documents', 'jira'],
        False,
    )


def test_reads_go_through_the_one_data_root_and_never_follow_links(data_root, tmp_path):
    data_root, roots = data_root
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    (elsewhere / 'a.md').write_text('outside-secret')
    os.symlink(elsewhere, roots['data'] / 'outside')
    assert not data_root.present('outside')
    with pytest.raises(HTTPException) as error:
        data_root.walk('outside', ['**/*.md'], [])
    assert error.value.status_code == 409
    with pytest.raises(OSError):
        data_root.read('outside', 'a.md', 100)
    for bad in ('../elsewhere', '.local-archive', 'code/.git'):
        with pytest.raises(HTTPException):
            data_root.container_path(bad)
    with pytest.raises(HTTPException) as unmounted:
        DataRoot(None).read('code', 'a.md', 100)
    assert unmounted.value.status_code == 503


@pytest.mark.parametrize('path', ['.env', 'sub/.env.local', '.git/config', 'node_modules/a.md', '.hidden/a.md'])
def test_write_refuses_hidden_and_skipped_paths(data_root, path):
    data_root, roots = data_root
    with pytest.raises(HTTPException) as error:
        data_root.write('jira/notes', path, b'x')
    assert error.value.status_code == 400


def test_walk_paths_merges_full_paths_and_reports_missing(data_root):
    data_root, roots = data_root
    repo = roots['code'] / 'payments'
    (repo / 'src').mkdir(parents=True)
    (repo / 'tests').mkdir()
    (repo / 'src' / 'retry.py').write_text('x')
    (repo / 'tests' / 'test_retry.py').write_text('x')
    (roots['jira'] / 'jira').mkdir()
    (roots['jira'] / 'jira' / 'DEMO-1.md').write_text('# D')
    walk = data_root.walk_paths(
        ['code/payments', 'jira/jira', 'docs/absent'], ['**/*.py', '**/*.md'], ['tests/**'], truncate=True
    )
    assert [(f['path'], f['source']) for f in walk.files] == [
        ('code/payments/src/retry.py', 'code/payments'),
        ('jira/jira/DEMO-1.md', 'jira/jira'),
    ]
    assert walk.missing == ['docs/absent'] and not walk.truncated
    with pytest.raises(FolderMissing, match='Path docs/absent is missing') as error:
        data_root.walk_paths(['code/payments', 'docs/absent'], ['**/*.py'], [])
    assert error.value.status_code == 409 and error.value.source == 'docs/absent'


def test_walk_paths_limits_count_the_whole_collection(data_root):
    data_root, roots = data_root
    for name in ('a', 'b'):
        folder = roots['jira'] / name
        folder.mkdir()
        for i in range(3):
            (folder / f'{i}.md').write_text('x')
    with pytest.raises(HTTPException, match='more than 4 files'):
        data_root.walk_paths(['jira/a', 'jira/b'], ['**/*.md'], [], limit=4)
    walk = data_root.walk_paths(['jira/a', 'jira/b'], ['**/*.md'], [], limit=4, truncate=True)
    assert walk.truncated and len(walk.files) == 4
    with pytest.raises(HTTPException, match='more than 5 entries'):
        data_root.walk_paths(['jira/a', 'jira/b'], ['**/*.md'], [], max_entries=5)


def test_walk_paths_newest_change_spans_paths(data_root):
    data_root, roots = data_root
    old, new = time.time() - 7200, time.time() - 60
    for name, stamp in (('a', old), ('b', new)):
        folder = roots['jira'] / name
        folder.mkdir()
        (folder / 'x.md').write_text('x')
        for path in (folder / 'x.md', folder):
            os.utime(path, (stamp, stamp))
    walk = data_root.walk_paths(['jira/a', 'jira/b'], ['**/*.md'], [])
    assert walk.newest_mtime == pytest.approx(new)
    assert data_root.walk_paths([], ['**/*.md'], []).files == []


def test_overlapping_paths():
    assert overlaps('code/a', 'code/a') and overlaps('code/a', 'code/a/b') and overlaps('code', 'code/a')
    assert (
        not overlaps('code/a', 'code/ab') and not overlaps('code/a', 'jira/a') and not overlaps('code/a/b', 'code/a/c')
    )


def test_single_walk_keeps_relative_paths_and_names_its_source(data_root):
    data_root, roots = data_root
    (roots['jira'] / 'notes').mkdir()
    (roots['jira'] / 'notes' / 'a.md').write_text('# A')
    assert data_root.walk('jira/notes', ['**/*.md'], []).files[0] | {'size': 0, 'mtime': 0} == {
        'path': 'a.md',
        'source': 'jira/notes',
        'size': 0,
        'mtime': 0,
    }


def test_subfolders_hide_hidden_skipped_links_and_unaddable_names(data_root):
    data_root, roots = data_root
    for name in ('b', 'a', '.git', '.env.d', 'node_modules', 'two words'):
        (roots['code'] / name).mkdir()
    (roots['code'] / 'file.md').write_text('x')
    os.symlink(roots['code'] / 'a', roots['code'] / 'link')
    assert data_root.subfolders('code') == (['a', 'b'], False)
    assert data_root.subfolders('code', limit=1) == (['a'], True)
    with pytest.raises(HTTPException) as missing:
        data_root.subfolders('code/absent')
    assert missing.value.status_code == 404 and missing.value.detail == 'Folder does not exist'
