"""Skills at run time: a run pins every enabled skill with its folder at start (config['skills']); the skill folders
are read-only roots of the file tools (SKILL.md is read at the path the Skills section lists) and of the shell, and
only the run's own skills are reachable."""

import asyncio
import json

import httpx
import pytest

from knowledge_admin.agents.runner import get_prompt
from knowledge_admin.capabilities import inventory
from knowledge_admin.capabilities.files import check_list_files
from knowledge_admin.capabilities.skills import run_skills
from knowledge_admin.knowledge_agents.config import get_agent_config
from knowledge_admin.sessions.debug import view
from knowledge_admin.sessions.run_access import agent_access
from knowledge_admin.skills.store import absolute, pinned_folders
from knowledge_admin.tools.catalog import CATALOG
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_expert_tools import model_of
from test_repositories import HEADERS
from test_test_sessions import test_start


@pytest.fixture
def pinned(store, saved_agent, monkeypatch, tmp_path):  # noqa: F811
    """A run of the saved agent, as run start pins it, with a workspace; its skill has a script."""
    client, skill = saved_agent[0], saved_agent[3]
    saved = client.put(
        f'/api/skills/{skill["id"]}/files',
        headers=HEADERS,
        json={'revision': 1, 'changes': [{'path': 'scripts/check.sh', 'text': 'echo checked\n', 'executable': True}]},
    )
    assert saved.status_code == 200, saved.text
    skill = saved.json()['skill']
    monkeypatch.setattr(inventory, '_store', store)
    config = get_agent_config('operations', conninfo=store.conninfo)
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        access = agent_access(conn, project, config, None)
        skills = run_skills(conn, project)
    workspace = (tmp_path / 'workspace').resolve()
    workspace.mkdir()
    access = {'resources': [item for item in access['resources'] if item['type'] != 'code_project']}
    context = {
        'workspace': workspace,
        'credentials': {},
        'config': {**config, 'access': access, 'skills': skills, 'project_id': str(project)},
    }
    return client, skill, context


def call(tool: str, context: dict, **arguments) -> str:
    spec = CATALOG[tool]
    return asyncio.run(spec.run(context, spec.arguments.model_validate(arguments)))


def test_a_runs_skill_carries_its_pinned_folder(pinned):
    _, skill, context = pinned
    [entry] = context['config']['skills']
    assert entry['folder'] == skill['folder']


def test_an_update_during_a_run_does_not_change_what_it_reads(pinned):
    """Review Focus 1: the run keeps reading its pinned folder."""
    client, skill, context = pinned
    updated = client.put(
        f'/api/skills/{skill["id"]}/files',
        headers=HEADERS,
        json={
            'revision': skill['revision'],
            'changes': [
                {'path': 'SKILL.md', 'text': '---\nname: inspect\ndescription: Inspect service health\n---\n\nNew.\n'},
                {'path': 'scripts/check.sh', 'text': 'echo changed\n', 'executable': True},
            ],
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()['skill']['folder'] != skill['folder']
    text = json.loads(call('read_file', context, path=str(absolute(skill['folder']) / 'SKILL.md')))['content']
    assert 'Check the exit code.' in text
    script = str(absolute(skill['folder']) / 'scripts/check.sh')
    assert json.loads(call('read_file', context, path=script))['content'] == 'echo checked\n'


def test_skill_folders_are_roots_of_the_file_tools(pinned):
    _, skill, context = pinned
    folder = absolute(skill['folder'])
    roots = [item['path'] for item in json.loads(call('list_files', context))['entries']]
    assert roots == [str(context['workspace']), str(folder)]
    listed = json.loads(call('list_files', context, path=str(folder)))['entries']
    assert [item['path'] for item in listed] == [str(folder / 'SKILL.md'), str(folder / 'scripts')]
    found = json.loads(call('search_files', context, path=str(folder), query='echo'))['matches']
    assert found == [f'{folder}/scripts/check.sh:1:echo checked']


def test_read_file_cannot_reach_a_skill_the_run_did_not_pin(pinned):
    """Review Focus 4: a skill created after the run started is outside the run's roots."""
    client, _, context = pinned
    other = client.post('/api/skills', headers=HEADERS, json={'name': 'other', 'description': 'Another skill.'})
    assert other.status_code == 201, other.text
    path = str(absolute(other.json()['folder']) / 'SKILL.md')
    with pytest.raises(ValueError, match='outside the agent’s selected folders'):
        call('read_file', context, path=path)


def test_the_file_tools_and_the_shell_need_a_code_project_or_a_skill():
    skill = {'id': 'k1', 'name': 'inspect', 'description': '', 'folder': 'skills/k1/v1'}
    from knowledge_admin.capabilities.shell.client import check_shell_run

    for check in (check_list_files, check_shell_run):
        assert check({'resources': []}, skills=[skill]) is None
        assert check({'resources': []}) == 'This run has no code projects or skills.'


def test_the_shell_mounts_the_skill_folders_at_the_listed_paths(pinned, monkeypatch):
    """Review Focus 4: shell_run gets the skill folder as a read-only path, the same path the Skills section lists (the worker
    ro-binds every read path; test_shell_worker runs it in the container)."""
    _, skill, context = pinned
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={'exit_code': 0, 'stdout': 'checked\n', 'stderr': '', 'timed_out': False})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setenv('AGENT_SHELL_URL', 'http://shell.test')
    folder = str(absolute(skill['folder']))
    call('shell_run', context, command=f'{folder}/scripts/check.sh')
    assert sent['read_paths'] == [folder] and sent['workspace'] == str(context['workspace'])
    assert folder in get_prompt(context['config'])


def test_debug_lists_the_skills_with_their_folder(pinned, store):
    _, skill, context = pinned
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        shown = view(conn, store, project, context['config'], prompt_id=None, prompt_text='')
    assert shown['skills'] == [{'name': 'inspect', 'folder': skill['folder']}]


def test_a_missing_skill_folder_leaves_out_only_that_skill(pinned, tmp_path, monkeypatch):
    """A pinned skill folder that is gone (or not mounted) is left out of the roots and the shell mounts, and its
    nothing of it is read; the other folders and skills keep working. A missing code folder still fails the file
    tools."""
    _, skill, context = pinned
    folder = absolute(skill['folder'])
    resources = context['config']['access']['resources']
    skills = context['config']['skills']
    [entry] = skills
    gone = {**entry, 'id': 'k2', 'name': 'gone', 'folder': 'skills/gone/v1'}
    escaping = {**entry, 'id': 'k3', 'name': 'escaping', 'folder': '../outside'}
    skills += [gone, escaping]
    roots = [item['path'] for item in json.loads(call('list_files', context))['entries']]
    assert roots == [str(context['workspace']), str(folder)]
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={'exit_code': 0, 'stdout': '', 'stderr': '', 'timed_out': False})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setenv('AGENT_SHELL_URL', 'http://shell.test')
    call('shell_run', context, command='ls')
    assert sent['read_paths'] == [str(folder)]
    code = {'type': 'code_project', 'id': 'c1', 'name': 'Code', 'folder_paths': [str(tmp_path / 'missing-code')]}
    resources.append(code)
    with pytest.raises(ValueError, match='Configured folder is unavailable'):
        call('list_files', context)


def test_a_started_session_pins_the_folder_cleanup_keeps(store, saved_agent):  # noqa: F811
    """The entry a real session start stores is the shape store.pinned_folders reads."""
    client, skill = saved_agent[0], saved_agent[3]
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET model_id=%s,enabled=true WHERE id='operations'", (model_of(saved_agent),))
    response = client.post('/api/sessions', headers=HEADERS, json=test_start(client, 'operations', 'Hi'))
    assert response.status_code == 201, response.text
    with store.connect() as conn:
        assert pinned_folders(conn) == {skill['folder']}
