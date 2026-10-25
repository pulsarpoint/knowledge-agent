"""Skill rules are pure: SKILL.md frontmatter, paths, limits, changed files and which folders cleanup deletes."""

import pytest

from knowledge_admin.skills import rules

SKILL_MD = """---
name: pdf-forms
description: >
  Use when filling PDF forms.
license: MIT
metadata:
  version: 1
---

# PDF forms

Run scripts/fill.py.
"""


def test_parse_skill_md_reads_the_frontmatter_and_keeps_the_body():
    assert rules.parse_skill_md(SKILL_MD) == {
        'name': 'pdf-forms',
        'description': 'Use when filling PDF forms.',
        'body': '# PDF forms\n\nRun scripts/fill.py.\n',
    }
    crlf = SKILL_MD.replace('\n', '\r\n')
    assert rules.parse_skill_md(crlf)['name'] == 'pdf-forms'


@pytest.mark.parametrize(
    'text,message',
    [
        ('# No frontmatter\n', rules.NO_FRONTMATTER),
        ('---\nname: a\n', rules.NO_FRONTMATTER),
        ('---\n[unclosed\n---\nbody', rules.BAD_FRONTMATTER),
        ('---\n- a list\n---\nbody', rules.BAD_FRONTMATTER),
        ('---\ndescription: d\n---\nbody', rules.NAME_RULE),
        ('---\nname: Bad Name\ndescription: d\n---\nbody', rules.NAME_RULE),
        ('---\nname: ' + 'a' * 65 + '\ndescription: d\n---\nbody', rules.NAME_RULE),
        ('---\nname: ok\n---\nbody', rules.DESCRIPTION_RULE),
        ('---\nname: ok\ndescription: "  "\n---\nbody', rules.DESCRIPTION_RULE),
        ('---\nname: ok\ndescription: ' + 'd' * 1025 + '\n---\nbody', rules.DESCRIPTION_RULE),
        ('---\nname: ok\ndescription: 12\n---\nbody', rules.DESCRIPTION_RULE),
    ],
)
def test_parse_skill_md_refuses_a_bad_frontmatter(text, message):
    with pytest.raises(ValueError) as error:
        rules.parse_skill_md(text)
    assert str(error.value) == message


def entry(path, type_='file'):
    return {'path': path, 'type': type_}


@pytest.mark.parametrize(
    'entries',
    [
        [entry('../evil')],
        [entry('a/../../evil')],
        [entry('/etc/passwd')],
        [entry('a\\b')],
        [entry('a/./b')],
        [entry('')],
        [entry('a//b')],
        [entry('a\x00b')],
        [entry('link', 'symlink')],
        [entry('device', 'other')],
        [entry('SKILL.md'), entry('SKILL.md')],
    ],
)
def test_check_paths_refuses_escapes_symlinks_and_duplicates(entries):
    assert rules.check_paths(entries) is not None


def test_check_paths_allows_relative_files_and_folders():
    assert rules.check_paths([entry('SKILL.md'), entry('scripts', 'folder'), entry('scripts/fill.py')]) is None
    assert rules.check_paths([entry('scripts/', 'folder')]) is None  # a zip names folders with a trailing slash


def test_check_limits():
    mb = 1024 * 1024
    assert rules.check_limits([1, 2, 3]) is None
    assert rules.check_limits([5 * mb, 5 * mb]) is None
    assert rules.check_limits([5 * mb + 1]) == rules.FILE_TOO_LARGE
    assert rules.check_limits([4 * mb, 4 * mb, 3 * mb]) == rules.FOLDER_TOO_LARGE
    assert rules.check_limits([1] * 500) is None
    assert rules.check_limits([1] * 501) == rules.TOO_MANY_FILES


def test_changed_files():
    old = {'SKILL.md': b'a', 'gone.txt': b'x', 'same.txt': b's'}
    new = {'SKILL.md': b'b', 'same.txt': b's', 'new.txt': b'n'}
    assert rules.changed_files(old, new) == [
        {'path': 'SKILL.md', 'change': 'modified'},
        {'path': 'gone.txt', 'change': 'removed'},
        {'path': 'new.txt', 'change': 'added'},
    ]


def test_folders_to_delete_keeps_the_last_three_the_current_and_the_pinned():
    newest_first = ['v5', 'v4', 'v3', 'v2', 'v1']
    assert rules.folders_to_delete(newest_first, current='v5', pinned=set()) == ['v2', 'v1']
    assert rules.folders_to_delete(newest_first, current='v5', pinned={'v1'}) == ['v2']  # Review Focus 5
    assert rules.folders_to_delete(newest_first, current='v1', pinned=set()) == ['v2']  # restored to an old one
    assert rules.folders_to_delete(['v2', 'v1'], current='v2', pinned=set()) == []
    assert rules.folders_to_delete(['v2', 'v1'], current=None, pinned={'v1'}) == ['v2']  # the skill is deleted
