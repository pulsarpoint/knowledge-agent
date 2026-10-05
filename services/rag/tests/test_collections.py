"""Collection folders: confinement, glob matching, skips and freshness."""
import os
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

from rag.collections import AREAS, Areas, FolderMissing, glob_regex, matches, relative_path, source_path


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


def test_read_write_delete_confined_to_rag_data(areas):
    areas, roots = areas
    (roots['rag-data'] / 'notes').mkdir()
    areas.write('rag-data/notes', 'sub/a.md', b'# A')
    assert areas.read('rag-data/notes', 'sub/a.md', 100) == b'# A'
    with pytest.raises(HTTPException, match='size limit'):
        areas.read('rag-data/notes', 'sub/a.md', 1)
    areas.delete('rag-data/notes', 'sub/a.md')
    assert not (roots['rag-data'] / 'notes' / 'sub' / 'a.md').exists()
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
