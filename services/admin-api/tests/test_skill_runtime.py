"""Skills at run time: a run pins its skills' folders at start (the skill entries carry `folder`); load_skill returns
the pinned SKILL.md body and the skill's files at their absolute paths; the skill folders are read-only roots of the
file tools and of the shell, and only the run's own skills are reachable."""

import asyncio
import json

import httpx
import pytest

from knowledge_admin.capabilities import inventory
from knowledge_admin.capabilities.files import check_list_files
from knowledge_admin.knowledge_agents.config import get_agent_config
from knowledge_admin.sessions.debug import view
from knowledge_admin.sessions.run_access import agent_access
from knowledge_admin.skills.store import absolute
from knowledge_admin.tools.catalog import CATALOG
from test_agents import agent_mounts, of, saved_agent  # noqa: F401  (fixtures)
from test_repositories import HEADERS

NOTE = 'Read files with read_file; run scripts with shell_run; both take these paths.'


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
    workspace = (tmp_path / 'workspace').resolve()
    workspace.mkdir()
    access = {'resources': [item for item in access['resources'] if item['type'] != 'code_project']}
    context = {
        'workspace': workspace,
        'credentials': {},
        'config': {**config, 'access': access, 'project_id': str(project)},
    }
    return client, skill, context


def call(tool: str, context: dict, **arguments) -> str:
    spec = CATALOG[tool]
    return asyncio.run(spec.run(context, spec.arguments.model_validate(arguments)))


def test_a_runs_skill_entry_carries_its_pinned_folder(pinned):
    _, skill, context = pinned
    [entry] = of(context['config'], 'skill')
    assert entry['folder'] == skill['folder']


def test_load_skill_returns_the_body_the_files_at_absolute_paths_and_the_note(pinned):
    _, skill, context = pinned
    folder = absolute(skill['folder'])
    text = call('load_skill', context, name='inspect')
    body = (folder / 'SKILL.md').read_text()
    assert text.startswith('Check the exit code.')
    assert f'- {folder}/SKILL.md ({len(body.encode())} bytes)' in text
    assert f'- {folder}/scripts/check.sh (13 bytes)' in text
    assert text.endswith(NOTE)


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
    text = call('load_skill', context, name='inspect')
    assert text.startswith('Check the exit code.') and str(absolute(skill['folder'])) in text
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


def test_read_file_cannot_reach_a_skill_outside_the_runs_access(pinned):
    """Review Focus 4: another skill's folder is outside the run's roots."""
    client, _, context = pinned
    other = client.post('/api/skills', headers=HEADERS, json={'name': 'other', 'description': 'Another skill.'})
    assert other.status_code == 201, other.text
    path = str(absolute(other.json()['folder']) / 'SKILL.md')
    with pytest.raises(ValueError, match='outside the agent’s selected folders'):
        call('read_file', context, path=path)


def test_the_file_tools_and_the_shell_need_a_code_project_or_a_skill():
    skill = {'type': 'skill', 'id': 'k1', 'name': 'inspect', 'folder': 'skills/k1/v1'}
    from knowledge_admin.capabilities.shell.client import check_shell_run

    for check in (check_list_files, check_shell_run):
        assert check({'resources': [skill]}) is None
        assert check({'resources': []}) == 'This run has no code projects or skills.'


def test_the_shell_mounts_the_skill_folders_at_the_listed_paths(pinned, monkeypatch):
    """Review Focus 4: shell_run gets the skill folder as a read-only path, the same path load_skill lists (the worker
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
    assert folder in call('load_skill', context, name='inspect')


def test_debug_lists_the_skills_with_their_folder(pinned, store):
    _, skill, context = pinned
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        shown = view(conn, store, project, context['config'], prompt_id=None, prompt_text='')
    assert shown['skills'] == [{'name': 'inspect', 'folder': skill['folder']}]
