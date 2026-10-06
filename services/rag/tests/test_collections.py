"""Collection folders: confinement, glob matching, skips and freshness."""
import os
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

from rag.collections import (AREAS, Areas, FolderMissing, glob_regex, indexable, matches, overlaps, relative_path,
                             source_path, visible)


@pytest.fixture
def areas(tmp_path):
    roots = {area: tmp_path / area for area in AREAS}
    for root in roots.values():
        root.mkdir()
    return Areas(roots, {area: f'/srv/{area}' for area in AREAS}), roots


@pytest.mark.parametrize('value', ['rag-data/jira', 'code/payments/src', 'generated-documents', 'docs'])
def test_valid_source_paths(value):
    assert source_path(value) == value


@pytest.mark.parametrize('value', ['', 'other/x', 'rag-data/../code', 'rag-data/.hidden', '/rag-data', 'rag-data//x', 'code/a b'])
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


def test_walk_filters_skips_and_reports_newest_change(areas):
    areas, roots = areas
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
    walk = areas.walk('code/payments', ['**/*.md', '**/*.py'], [])
    assert [f['path'] for f in walk.files] == ['README.md', 'src/app.py']
    assert walk.skipped == {'links': 1, 'special': 1, 'unreadable': 0, 'invalid_names': 0}
    assert walk.newest_mtime >= (repo / 'src' / 'app.py').stat().st_mtime
    assert areas.container_path('code/payments') == roots['code'] / 'payments'
    assert areas.host_path('code/payments') == '/srv/code/payments'
    assert not areas.writable('code/payments') and areas.writable('rag-data/jira')


def test_deleting_a_file_moves_folder_change_time_forward(areas):
    areas, roots = areas
    folder = roots['rag-data'] / 'jira'
    folder.mkdir()
    (folder / 'A.md').write_text('a')
    (folder / 'B.md').write_text('b')
    old = time.time() - 3600
    for path in (folder, folder / 'A.md', folder / 'B.md'):
        os.utime(path, (old, old))
    before = areas.walk('rag-data/jira', ['**/*.md'], []).newest_mtime
    (folder / 'B.md').unlink()
    assert areas.walk('rag-data/jira', ['**/*.md'], []).newest_mtime > before


def test_walk_limit_and_missing_folder(areas):
    areas, roots = areas
    folder = roots['rag-data'] / 'big'
    folder.mkdir()
    for i in range(5):
        (folder / f'{i}.md').write_text('x')
    with pytest.raises(HTTPException, match='more than 3 files'):
        areas.walk('rag-data/big', ['**/*.md'], [], limit=3)
    walk = areas.walk('rag-data/big', ['**/*.md'], [], limit=3, truncate=True)
    assert walk.truncated and len(walk.files) == 3
    with pytest.raises(FolderMissing):
        areas.walk('rag-data/absent', ['**/*.md'], [])


def test_read_write_confined_to_rag_data(areas):
    areas, roots = areas
    (roots['rag-data'] / 'notes').mkdir()
    areas.write('rag-data/notes', 'sub/a.md', b'# A')
    assert areas.read('rag-data/notes', 'sub/a.md', 100) == b'# A'
    with pytest.raises(HTTPException, match='size limit'):
        areas.read('rag-data/notes', 'sub/a.md', 1)
    (roots['code'] / 'repo').mkdir()
    with pytest.raises(HTTPException, match='read-only'):
        areas.write('code/repo', 'a.md', b'x')
    os.symlink(roots['code'], roots['rag-data'] / 'notes' / 'escape')
    with pytest.raises(OSError):
        areas.read('rag-data/notes', 'escape/repo', 100)


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
def test_walk_counts_unreadable_directories(areas):
    areas, roots = areas
    folder = roots['rag-data'] / 'p'
    (folder / 'locked').mkdir(parents=True)
    (folder / 'a.md').write_text('a')
    (folder / 'locked' / 'b.md').write_text('b')
    (folder / 'locked').chmod(0)
    try:
        walk = areas.walk('rag-data/p', ['**/*.md'], [])
    finally:
        (folder / 'locked').chmod(0o755)
    assert [f['path'] for f in walk.files] == ['a.md']
    assert walk.skipped['unreadable'] == 1


def test_walk_skips_non_utf8_names(areas):
    areas, roots = areas
    folder = roots['rag-data'] / 'p'
    folder.mkdir()
    (folder / 'a.md').write_text('a')
    try:
        with open(os.path.join(os.fsencode(folder), b'bad\xff.md'), 'w') as stream:
            stream.write('x')
    except OSError:
        pytest.skip('filesystem rejects non-UTF-8 names')
    walk = areas.walk('rag-data/p', ['**/*.md'], [])
    assert [f['path'] for f in walk.files] == ['a.md']
    assert walk.skipped['invalid_names'] == 1


def test_walk_rejects_symlinked_collection_folder(areas):
    areas, roots = areas
    os.symlink(roots['code'], roots['rag-data'] / 'link')
    with pytest.raises(HTTPException) as error:
        areas.walk('rag-data/link', ['**/*.md'], [])
    assert error.value.status_code == 409


def test_walk_skips_surrogate_names_from_listing(areas, monkeypatch):
    areas, roots = areas
    folder = roots['rag-data'] / 'p'
    folder.mkdir()
    (folder / 'a.md').write_text('a')
    real = os.listdir
    monkeypatch.setattr(os, 'listdir', lambda fd: [*real(fd), 'bad\udcff.md'])
    walk = areas.walk('rag-data/p', ['**/*.md'], [])
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
    for path in ('.env', '.env.local', 'sub/.env.production', '.git/config', 'a/.git/HEAD', 'node_modules/x/README.md',
                 '.github/workflows/ci.md', '.commit-update-test/README.md', 'a/.hidden.md'):
        assert not indexable(path, include, []), path
        assert not visible(path), path
    assert not indexable('app.py', ['**/*.md'], [])
    assert not indexable('v1/a.md', ['**/*.md'], ['v1/**'])


def test_walk_skips_hidden_directories_and_files(areas):
    areas, roots = areas
    folder = roots['generated-documents']
    (folder / '.documentation-update-abc' / 'payments').mkdir(parents=True)
    (folder / '.documentation-update-abc' / 'payments' / 'README.md').write_text('# scratch')
    (folder / 'payments').mkdir()
    (folder / 'payments' / 'README.md').write_text('# Payments')
    (folder / 'payments' / '.draft.md').write_text('# hidden')
    walk = areas.walk('generated-documents', ['**/*.md'], [])
    assert [f['path'] for f in walk.files] == ['payments/README.md']


def test_walk_caps_visited_entries(areas):
    areas, roots = areas
    folder = roots['code'] / 'repo'
    (folder / 'src').mkdir(parents=True)
    for i in range(10):
        (folder / 'src' / f'{i}.py').write_text('x')
    (folder / 'README.md').write_text('# R')
    # No file matches the include patterns, yet every entry counts towards the cap.
    walk = areas.walk('code/repo', ['**/*.rst'], [], truncate=True, max_entries=5)
    assert walk.truncated and walk.files == []
    with pytest.raises(HTTPException, match='more than 5 entries'):
        areas.walk('code/repo', ['**/*.rst'], [], max_entries=5)
    assert not areas.walk('code/repo', ['**/*.rst'], [], truncate=True, max_entries=100).truncated


def test_present_and_ensure_folder(areas):
    areas, roots = areas
    assert not areas.present('rag-data/new')
    areas.ensure_folder('rag-data/new/nested')
    assert (roots['rag-data'] / 'new' / 'nested').is_dir() and areas.present('rag-data/new/nested')
    assert (roots['rag-data'] / 'new' / 'nested').stat().st_mode & 0o777 == 0o750 & ~_umask()
    areas.ensure_folder('rag-data/new/nested')  # idempotent
    areas.ensure_folder('code/absent')  # read-only areas are never created
    assert not (roots['code'] / 'absent').exists() and not areas.present('code/absent')
    os.symlink(roots['code'], roots['rag-data'] / 'link')
    with pytest.raises(OSError):
        areas.ensure_folder('rag-data/link/x')
    assert not (roots['code'] / 'x').exists()
    assert not areas.present('rag-data/link')


def test_first_write_creates_the_collection_folder(areas):
    areas, roots = areas
    areas.write('rag-data/jira', 'DEMO-1.md', b'# DEMO-1')
    assert (roots['rag-data'] / 'jira' / 'DEMO-1.md').read_bytes() == b'# DEMO-1'


@pytest.mark.parametrize('path', ['.env', 'sub/.env.local', '.git/config', 'node_modules/a.md', '.hidden/a.md'])
def test_write_refuses_hidden_and_skipped_paths(areas, path):
    areas, roots = areas
    with pytest.raises(HTTPException) as error:
        areas.write('rag-data/notes', path, b'x')
    assert error.value.status_code == 400


def _umask():
    mask = os.umask(0)
    os.umask(mask)
    return mask


def test_walk_paths_merges_full_paths_and_reports_missing(areas):
    areas, roots = areas
    repo = roots['code'] / 'payments'
    (repo / 'src').mkdir(parents=True)
    (repo / 'tests').mkdir()
    (repo / 'src' / 'retry.py').write_text('x')
    (repo / 'tests' / 'test_retry.py').write_text('x')
    (roots['rag-data'] / 'jira').mkdir()
    (roots['rag-data'] / 'jira' / 'DEMO-1.md').write_text('# D')
    walk = areas.walk_paths(['code/payments', 'rag-data/jira', 'docs/absent'], ['**/*.py', '**/*.md'], ['tests/**'], truncate=True)
    assert [(f['path'], f['source']) for f in walk.files] == [
        ('code/payments/src/retry.py', 'code/payments'), ('rag-data/jira/DEMO-1.md', 'rag-data/jira')]
    assert walk.missing == ['docs/absent'] and not walk.truncated
    with pytest.raises(FolderMissing, match='Path docs/absent is missing') as error:
        areas.walk_paths(['code/payments', 'docs/absent'], ['**/*.py'], [])
    assert error.value.status_code == 409 and error.value.source == 'docs/absent'


def test_walk_paths_limits_count_the_whole_collection(areas):
    areas, roots = areas
    for name in ('a', 'b'):
        folder = roots['rag-data'] / name
        folder.mkdir()
        for i in range(3):
            (folder / f'{i}.md').write_text('x')
    with pytest.raises(HTTPException, match='more than 4 files'):
        areas.walk_paths(['rag-data/a', 'rag-data/b'], ['**/*.md'], [], limit=4)
    walk = areas.walk_paths(['rag-data/a', 'rag-data/b'], ['**/*.md'], [], limit=4, truncate=True)
    assert walk.truncated and len(walk.files) == 4
    with pytest.raises(HTTPException, match='more than 5 entries'):
        areas.walk_paths(['rag-data/a', 'rag-data/b'], ['**/*.md'], [], max_entries=5)


def test_walk_paths_newest_change_spans_paths(areas):
    areas, roots = areas
    old, new = time.time() - 7200, time.time() - 60
    for name, stamp in (('a', old), ('b', new)):
        folder = roots['rag-data'] / name
        folder.mkdir()
        (folder / 'x.md').write_text('x')
        for path in (folder / 'x.md', folder):
            os.utime(path, (stamp, stamp))
    walk = areas.walk_paths(['rag-data/a', 'rag-data/b'], ['**/*.md'], [])
    assert walk.newest_mtime == pytest.approx(new)
    assert areas.walk_paths([], ['**/*.md'], []).files == []


def test_overlapping_paths():
    assert overlaps('code/a', 'code/a') and overlaps('code/a', 'code/a/b') and overlaps('code', 'code/a')
    assert not overlaps('code/a', 'code/ab') and not overlaps('code/a', 'rag-data/a') and not overlaps('code/a/b', 'code/a/c')


def test_single_walk_keeps_relative_paths_and_names_its_source(areas):
    areas, roots = areas
    (roots['rag-data'] / 'notes').mkdir()
    (roots['rag-data'] / 'notes' / 'a.md').write_text('# A')
    assert areas.walk('rag-data/notes', ['**/*.md'], []).files[0] | {'size': 0, 'mtime': 0} == {
        'path': 'a.md', 'source': 'rag-data/notes', 'size': 0, 'mtime': 0}


def test_subfolders_hide_hidden_skipped_links_and_unaddable_names(areas):
    areas, roots = areas
    for name in ('b', 'a', '.git', '.env.d', 'node_modules', 'two words'):
        (roots['code'] / name).mkdir()
    (roots['code'] / 'file.md').write_text('x')
    os.symlink(roots['code'] / 'a', roots['code'] / 'link')
    assert areas.subfolders('code') == (['a', 'b'], False)
    assert areas.subfolders('code', limit=1) == (['a'], True)
    with pytest.raises(HTTPException) as missing:
        areas.subfolders('code/absent')
    assert missing.value.status_code == 404 and missing.value.detail == 'Folder does not exist'
