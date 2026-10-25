"""Skill folders on disk: an update writes a whole new folder, then the row switches to it; the last three are kept;
cleanup never deletes a folder a running run pinned (Review Focus 5)."""

import os
import stat
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from knowledge_admin.skills import store as folders
from session_rows import a_model, add_test_session

SKILL_MD = b'---\nname: pdf-forms\ndescription: Fill PDF forms.\n---\n\n# PDF forms\n'


@pytest.fixture
def root(skills_folder):
    return folders.skills_root()


def files(**extra) -> dict:
    return {'SKILL.md': (SKILL_MD, False), **extra}


def test_the_root_comes_from_skills_root_inside_the_data_folder(monkeypatch):
    monkeypatch.delenv('SKILLS_ROOT', raising=False)
    with pytest.raises(ValueError, match='not configured'):
        folders.skills_root()
    monkeypatch.setenv('SKILLS_ROOT', '/elsewhere/skills')
    with pytest.raises(ValueError, match='inside DATA_ROOT'):
        folders.skills_root()


def test_write_folder_writes_every_file_keeps_executable_bits_and_records_the_source(root, skills_folder):
    skill = str(uuid4())
    source = {'kind': 'upload', 'file': 'pdf.zip'}
    version = folders.write_folder(
        root, skill, files(**{'scripts/fill.py': (b'print(1)', True), 'assets/a.bin': (b'\x00\x01', False)}), source
    )
    folder = root / skill / version
    assert folders.relative(root, skill, version) == f'skills/{skill}/{version}'
    assert (skills_folder / folders.relative(root, skill, version)) == folder
    assert (folder / 'SKILL.md').read_bytes() == SKILL_MD
    assert os.stat(folder / 'scripts/fill.py').st_mode & stat.S_IXUSR
    assert not os.stat(folder / 'SKILL.md').st_mode & stat.S_IXUSR
    assert folders.read_folder(root, skill, version)['scripts/fill.py'] == (b'print(1)', True)
    assert folders.list_files(folder) == [
        {'path': 'SKILL.md', 'size': len(SKILL_MD), 'executable': False},
        {'path': 'assets/a.bin', 'size': 2, 'executable': False},
        {'path': 'scripts/fill.py', 'size': 8, 'executable': True},
    ]
    [kept] = folders.kept_folders(root, skill)
    assert kept['version'] == version and kept['source'] == source and kept['created_at']
    assert not [p for p in (root / skill).iterdir() if p.name.startswith('.')]  # no staging left


def test_versions_are_timestamps_or_the_commit_and_never_overwrite(root):
    skill = str(uuid4())
    first = folders.write_folder(root, skill, files(), {'kind': 'editor'})
    second = folders.write_folder(root, skill, files(**{'b.txt': (b'b', False)}), {'kind': 'editor'})
    assert len(first) == len('20261025T101010Z') and first != second
    assert folders.read_folder(root, skill, first) == files()
    commit = folders.write_folder(root, skill, files(), {'kind': 'git', 'commit': 'a' * 40}, commit='a' * 40)
    assert commit == 'a' * 12
    assert [kept['version'] for kept in folders.kept_folders(root, skill)] == [commit, second, first]


def a_run_pinning(conn, project, folder: str, *, status: str = 'working'):
    actor = conn.execute('SELECT id FROM ka.principals LIMIT 1').fetchone()['id']
    agent = f'agent-{uuid4().hex[:6]}'
    conn.execute("INSERT INTO ka.agents(id,name,system_prompt) VALUES (%s,%s,'Be brief.')", (agent, agent))
    skill = {'id': str(uuid4()), 'name': 'inspect', 'description': 'Inspect.', 'folder': folder}
    config = {'id': agent, 'access': {'resources': []}, 'skills': [skill]}
    return add_test_session(conn, project, actor, agent, a_model(conn, actor), config=config, status=status)


def test_pinned_folders_are_named_by_runs_that_are_not_closed(store_db):
    conn, project = store_db
    a_run_pinning(conn, project, 'skills/s/v1')
    a_run_pinning(conn, project, 'skills/s/v2', status='closed')
    actor = conn.execute('SELECT id FROM ka.principals LIMIT 1').fetchone()['id']
    add_test_session(conn, project, actor, 'nobody', a_model(conn, actor), config={'skills': 'odd'})
    old = {'access': {'resources': [{'type': 'skill', 'id': 'k1', 'folder': 'skills/s/v3'}]}}  # ignored
    add_test_session(conn, project, actor, 'nobody', a_model(conn, actor), config=old)
    assert folders.pinned_folders(conn) == {'skills/s/v1'}


@pytest.fixture
def store_db(store):
    with store.connect() as conn:
        conn.execute("INSERT INTO ka.agents(id,name,system_prompt) VALUES ('nobody','N','N')")
        yield conn, store.project(conn, 'shop')


def test_cleanup_keeps_the_last_three_and_never_a_pinned_folder(root, store_db):
    conn, project = store_db
    skill = str(uuid4())
    versions = [folders.write_folder(root, skill, files(), {'kind': 'editor'}) for _ in range(5)]
    a_run_pinning(conn, project, folders.relative(root, skill, versions[0]))  # Review Focus 5
    deleted = folders.cleanup(root, {skill: versions[-1]}, folders.pinned_folders(conn))
    assert deleted == [folders.relative(root, skill, versions[1])]
    assert [kept['version'] for kept in folders.kept_folders(root, skill)] == [*reversed(versions[2:]), versions[0]]
    assert not (root / skill / f'{versions[1]}.json').exists()


def test_cleanup_removes_a_deleted_skills_folders_unless_pinned(root):
    gone, pinned_skill = str(uuid4()), str(uuid4())
    folders.write_folder(root, gone, files(), {'kind': 'editor'})
    kept = folders.write_folder(root, pinned_skill, files(), {'kind': 'editor'})
    folders.cleanup(root, {}, {folders.relative(root, pinned_skill, kept)}, all_skills=True)
    assert not (root / gone).exists()
    assert (root / pinned_skill / kept / 'SKILL.md').exists()
    folders.cleanup(root, {}, set(), all_skills=True)
    assert not (root / pinned_skill).exists()


def test_cleanup_on_update_touches_only_the_skills_named(root):
    being_created, deleted = str(uuid4()), str(uuid4())
    folders.write_folder(root, being_created, files(), {'kind': 'editor'})  # its row is not inserted yet
    folders.write_folder(root, deleted, files(), {'kind': 'editor'})
    folders.cleanup(root, {deleted: None}, set())
    assert (root / being_created).exists() and not (root / deleted).exists()


def test_a_skill_row_switches_folder_in_one_update(root, store_db):
    conn, project = store_db
    actor = conn.execute('SELECT id FROM ka.principals LIMIT 1').fetchone()['id']
    skill = uuid4()
    first = folders.write_folder(root, str(skill), files(), {'kind': 'editor'})
    conn.execute(
        """INSERT INTO ka.agent_skills(id,project_id,name,description,source,folder,created_by,updated_by)
        VALUES (%s,%s,'pdf-forms','Fill.',%s,%s,%s,%s)""",
        (skill, project, Jsonb({'kind': 'editor'}), folders.relative(root, str(skill), first), actor, actor),
    )
    second = folders.write_folder(root, str(skill), files(**{'b.txt': (b'b', False)}), {'kind': 'editor'})
    row = folders.switch(
        conn,
        project,
        skill,
        folder=folders.relative(root, str(skill), second),
        name='pdf-forms',
        description='Fill more.',
        source={'kind': 'editor'},
        actor=actor,
    )
    assert row['folder'] == f'skills/{skill}/{second}' and row['revision'] == 2 and row['description'] == 'Fill more.'
    assert (root / str(skill) / first / 'SKILL.md').exists()  # a run that began with it keeps reading it


def test_startup_cleanup_runs_over_every_skill_folder(root, store):
    gone = str(uuid4())
    folders.write_folder(root, gone, files(), {'kind': 'editor'})
    from test_api import _client

    with _client(root.parent, repository_store=store):  # the lifespan runs on enter
        pass
    assert not (root / gone).exists()
