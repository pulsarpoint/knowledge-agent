"""Skill import: a zip (the skill at its root, in its only folder, or several skills), or a git repository (URL, ref,
path; shallow, the commit recorded). A zip with '../evil' or a symlink is refused (Review Focus 2)."""

import io
import os
import stat
import subprocess
import zipfile
from pathlib import Path

import pytest

from knowledge_admin.skills import imports, rules


def skill_md(name: str, description: str = 'Use it.') -> bytes:
    return f'---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n'.encode()


def a_zip(entries: dict[str, bytes], *, executable=(), symlinks=()) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for path, content in entries.items():
            info = zipfile.ZipInfo(path)
            mode = 0o755 if path in executable else 0o644
            if path in symlinks:
                mode = stat.S_IFLNK | 0o777
            elif not path.endswith('/'):
                mode |= stat.S_IFREG
            info.external_attr = mode << 16
            archive.writestr(info, content)
    return buffer.getvalue()


def test_a_zip_with_the_skill_at_its_root():
    data = a_zip(
        {'SKILL.md': skill_md('pdf'), 'scripts/': b'', 'scripts/fill.py': b'x'}, executable={'scripts/fill.py'}
    )
    [found] = imports.skills_in_zip(data)
    assert found['name'] == 'pdf' and found['description'] == 'Use it.'
    assert found['files'] == {'SKILL.md': (skill_md('pdf'), False), 'scripts/fill.py': (b'x', True)}


def test_a_zip_with_the_skill_in_its_only_folder_ignores_mac_leftovers():
    data = a_zip(
        {'pdf/SKILL.md': skill_md('pdf'), 'pdf/a.txt': b'a', '__MACOSX/pdf/._a.txt': b'', 'pdf/.DS_Store': b''}
    )
    [found] = imports.skills_in_zip(data)
    assert set(found['files']) == {'SKILL.md', 'a.txt'}


def test_several_skills_in_one_zip():
    data = a_zip(
        {
            'README.md': b'not a skill',
            'pdf/SKILL.md': skill_md('pdf'),
            'docx/SKILL.md': skill_md('docx'),
            'docx/ref.md': b'r',
            'notes/x.md': b'no SKILL.md here',
        }
    )
    assert [(s['name'], sorted(s['files'])) for s in imports.skills_in_zip(data)] == [
        ('docx', ['SKILL.md', 'ref.md']),
        ('pdf', ['SKILL.md']),
    ]
    nested = a_zip({'all/pdf/SKILL.md': skill_md('pdf'), 'all/docx/SKILL.md': skill_md('docx')})
    assert [s['name'] for s in imports.skills_in_zip(nested)] == ['docx', 'pdf']


@pytest.mark.parametrize(
    'entries,symlinks',
    [
        ({'SKILL.md': skill_md('pdf'), '../evil': b'x'}, ()),
        ({'SKILL.md': skill_md('pdf'), 'a/../../evil': b'x'}, ()),
        ({'SKILL.md': skill_md('pdf'), '/etc/evil': b'x'}, ()),
        ({'SKILL.md': skill_md('pdf'), 'link': b'/etc/passwd'}, ('link',)),
    ],
)
def test_a_zip_with_an_escape_or_a_symlink_is_refused(entries, symlinks):
    with pytest.raises(imports.ImportRefused):
        imports.skills_in_zip(a_zip(entries, symlinks=symlinks))


@pytest.mark.parametrize(
    'entries,message',
    [
        ({'README.md': b'x'}, imports.NO_SKILL),
        ({'a/SKILL.md': skill_md('pdf'), 'b/SKILL.md': skill_md('pdf')}, 'The archive holds two skills named pdf.'),
        ({'SKILL.md': b'no frontmatter'}, rules.NO_FRONTMATTER),
        ({'SKILL.md': skill_md('pdf'), 'big.bin': b'x' * (5 * 1024 * 1024 + 1)}, rules.FILE_TOO_LARGE),
        ({'SKILL.md': skill_md('pdf'), **{f'f{i}': b'' for i in range(500)}}, rules.TOO_MANY_FILES),
    ],
)
def test_a_zip_that_breaks_a_rule_is_refused(entries, message):
    with pytest.raises(imports.ImportRefused) as error:
        imports.skills_in_zip(a_zip(entries))
    assert str(error.value) == message


def test_not_a_zip():
    with pytest.raises(imports.ImportRefused, match='not a zip'):
        imports.skills_in_zip(b'plain text')


def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}
    return subprocess.run(
        ['git', '-c', 'user.name=T', '-c', 'user.email=t@test', *args],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A local repository with a marketplace layout: skills/pdf-forms on main, a change on the branch next."""
    monkeypatch.setattr(imports, 'ALLOW_LOCAL', True)
    path = tmp_path / 'repo'
    (path / 'skills/pdf-forms/scripts').mkdir(parents=True)
    (path / 'skills/pdf-forms/SKILL.md').write_bytes(skill_md('pdf-forms'))
    (path / 'skills/pdf-forms/scripts/fill.sh').write_text('echo fill\n')
    (path / 'skills/pdf-forms/scripts/fill.sh').chmod(0o755)
    (path / 'README.md').write_text('market')
    git(path, 'init', '-q', '-b', 'main')
    git(path, 'add', '.')
    git(path, 'commit', '-q', '-m', 'first')
    git(path, 'checkout', '-q', '-b', 'next')
    (path / 'skills/pdf-forms/ref.md').write_text('more')
    git(path, 'add', '.')
    git(path, 'commit', '-q', '-m', 'second')
    git(path, 'checkout', '-q', 'main')
    return path


def test_git_reads_the_folder_at_a_ref_and_path_and_records_the_commit(repo):
    found, commit = imports.from_git(str(repo), '', 'skills/pdf-forms')
    assert commit == git(repo, 'rev-parse', 'main')
    assert found['name'] == 'pdf-forms'
    assert found['files'] == {'SKILL.md': (skill_md('pdf-forms'), False), 'scripts/fill.sh': (b'echo fill\n', True)}
    on_next, next_commit = imports.from_git(str(repo), 'next', 'skills/pdf-forms')
    assert next_commit == git(repo, 'rev-parse', 'next') and 'ref.md' in on_next['files']


def test_git_at_the_repository_root_leaves_out_dot_git(repo):
    (repo / 'SKILL.md').write_bytes(skill_md('market'))
    git(repo, 'add', '.')
    git(repo, 'commit', '-q', '-m', 'root skill')
    found, _ = imports.from_git(str(repo), 'main', '')
    assert '.git' not in {path.split('/')[0] for path in found['files']} and 'README.md' in found['files']


def test_git_refuses_a_symlink_a_missing_path_and_a_bad_ref(repo):
    os.symlink('/etc/passwd', repo / 'skills/pdf-forms/link')
    git(repo, 'add', '.')
    git(repo, 'commit', '-q', '-m', 'link')
    with pytest.raises(imports.ImportRefused, match='symbolic link'):
        imports.from_git(str(repo), 'main', 'skills/pdf-forms')
    with pytest.raises(imports.ImportRefused, match='no folder'):
        imports.from_git(str(repo), 'main', 'skills/missing')
    with pytest.raises(imports.ImportRefused, match='relative path'):
        imports.from_git(str(repo), 'main', '../outside')
    with pytest.raises(imports.ImportRefused, match='Git could not fetch'):
        imports.from_git(str(repo), 'no-such-branch', 'skills/pdf-forms')


def test_git_urls_are_https_only_unless_local_is_allowed(monkeypatch):
    monkeypatch.setattr(imports, 'ALLOW_LOCAL', False)
    with pytest.raises(imports.ImportRefused, match='(?i)https'):
        imports.from_git('/tmp/repo', '', '')
    with pytest.raises(imports.ImportRefused, match='(?i)https'):
        imports.from_git('file:///tmp/repo', '', '')


MB = 1024 * 1024


def a_deflated_zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path, content in entries.items():
            archive.writestr(path, content)
    return buffer.getvalue()


@pytest.fixture
def no_reading(monkeypatch):
    """Refusals that must come from the declared sizes, before any entry is read."""

    original = zipfile.ZipFile.open

    def open_for_writing_only(self, name, mode='r', *args, **kwargs):
        if mode == 'r':
            raise AssertionError('an entry was read')
        return original(self, name, mode, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, 'open', open_for_writing_only)


def test_a_large_compressible_entry_is_refused_before_reading(no_reading):
    data = a_deflated_zip({'SKILL.md': skill_md('pdf'), 'zeros.bin': b'\0' * (6 * MB)})
    assert len(data) < MB
    with pytest.raises(imports.ImportRefused) as error:
        imports.skills_in_zip(data)
    assert str(error.value) == rules.FILE_TOO_LARGE


def test_a_skill_over_10_mb_in_declared_sizes_is_refused_before_reading(no_reading):
    data = a_deflated_zip({'a/SKILL.md': skill_md('a'), **{f'a/f{i}': b'\0' * (4 * MB) for i in range(3)}})
    with pytest.raises(imports.ImportRefused) as error:
        imports.skills_in_zip(data)
    assert str(error.value) == rules.FOLDER_TOO_LARGE


def test_an_archive_of_several_skills_is_capped_at_50_mb_before_reading(no_reading):
    entries = {}
    for skill in 'abcdef':
        entries[f'{skill}/SKILL.md'] = skill_md(skill)
        entries.update({f'{skill}/f{i}': b'\0' * (4 * MB + MB // 2) for i in range(2)})  # 9 MB a skill, 54 MB in all
    with pytest.raises(imports.ImportRefused) as error:
        imports.skills_in_zip(a_deflated_zip(entries))
    assert str(error.value) == imports.ARCHIVE_TOO_LARGE


def test_a_forged_small_size_cannot_read_past_it():
    data = bytearray(a_deflated_zip({'SKILL.md': skill_md('pdf'), 'zeros.bin': b'\0' * (6 * MB)}))
    real = (6 * MB).to_bytes(4, 'little')
    forged = (100).to_bytes(4, 'little')
    central = data.index(b'PK\x01\x02', data.index(b'PK\x01\x02') + 4)  # zeros.bin's central directory entry
    assert data[central + 24 : central + 28] == real
    data[central + 24 : central + 28] = forged
    local = data.index(b'PK\x03\x04', 4)
    data[local + 22 : local + 26] = forged
    with pytest.raises(imports.ImportRefused):
        imports.skills_in_zip(bytes(data))


def test_a_damaged_or_encrypted_archive_is_refused():
    data = bytearray(a_zip({'SKILL.md': skill_md('pdf'), 'a.txt': b'hello world'}))
    data[data.index(b'hello world')] ^= 0xFF  # a CRC mismatch
    with pytest.raises(imports.ImportRefused, match='cannot be read'):
        imports.skills_in_zip(bytes(data))


@pytest.mark.parametrize(
    'entries',
    [
        {'SKILL.md': skill_md('pdf'), 'README.md': b'a', 'Readme.md': b'b'},
        {'SKILL.md': skill_md('pdf'), 'a': b'1', 'a/b': b'2'},
    ],
)
def test_case_and_file_folder_collisions_are_refused(entries):
    with pytest.raises(imports.ImportRefused):
        imports.skills_in_zip(a_zip(entries))


@pytest.mark.parametrize('url', ['ext::sh -c touch% /tmp/x', '-uhttps://example.test/a', 'http://example.test/a.git'])
def test_git_refuses_other_transports_and_options(url, monkeypatch):
    monkeypatch.setattr(imports, 'ALLOW_LOCAL', False)
    with pytest.raises(imports.ImportRefused):
        imports.from_git(url, '', '')


def test_entries_without_file_type_bits_are_files():
    """Python's writestr and Windows tools leave the type bits empty."""
    [found] = imports.skills_in_zip(a_deflated_zip({'SKILL.md': skill_md('pdf'), 'a.txt': b'a'}))
    assert set(found['files']) == {'SKILL.md', 'a.txt'}


def test_git_refuses_a_path_through_a_symlink_out_of_the_repository(repo, tmp_path):
    outside = tmp_path / 'outside'
    (outside / 'x').mkdir(parents=True)
    (outside / 'x/SKILL.md').write_bytes(skill_md('outside'))
    os.symlink(outside, repo / 'skills/out')
    git(repo, 'add', '.')
    git(repo, 'commit', '-q', '-m', 'out')
    with pytest.raises(imports.ImportRefused, match='no folder'):
        imports.from_git(str(repo), 'main', 'skills/out/x')
