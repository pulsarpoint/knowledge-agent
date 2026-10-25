"""Every run sees every enabled skill: run start pins config['skills'] (all enabled skills of the project, in name order,
whose folder is on disk); load_skill, the file tools, the shell, Debug and folder cleanup read it; subagents and experts
inherit it. Skills are not part of the access model: old stored skill entries are ignored, and 0141 strips them."""

import asyncio
import json
import shutil
from pathlib import Path
from uuid import UUID, uuid4

from access_helpers import give
from knowledge_admin.agents.runner import get_prompt
from knowledge_admin.capabilities.files import check_list_files
from knowledge_admin.capabilities.shell.client import check_shell_run
from knowledge_admin.capabilities.skills import check_load_skill
from knowledge_admin.knowledge_agents import experts
from knowledge_admin.knowledge_agents.config import get_agent_config
from knowledge_admin.knowledge_agents.subagents import subagent_config
from knowledge_admin.sessions.text import agent_system_prompt
from knowledge_admin.skills.store import absolute, pinned_folders
from knowledge_admin.tools.catalog import CATALOG
from psycopg.types.json import Jsonb
from session_rows import a_model
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_expert_tools import model_of
from test_generations import sessions  # noqa: F401  (fixture)
from test_profiles import update
from test_repositories import HEADERS
from test_test_sessions import test_start

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def add_skill(client, name: str, *, enabled: bool = True) -> dict:
    created = client.post('/api/skills', headers=HEADERS, json={'name': name, 'description': f'{name} things'})
    assert created.status_code == 201, created.text
    skill = created.json()
    if not enabled:
        switched = client.patch(
            f'/api/skills/{skill["id"]}/status', headers=HEADERS, json={'enabled': False, 'revision': skill['revision']}
        )
        assert switched.status_code == 200, switched.text
    return skill


def started_config(store, client) -> dict:
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET model_id=%s,enabled=true WHERE id='operations'", (model_of_client(client),))
    response = client.post('/api/sessions', headers=HEADERS, json=test_start(client, 'operations', 'Hi'))
    assert response.status_code == 201, response.text
    with store.connect() as conn:
        return conn.execute(
            'SELECT agent_config FROM ka.agent_sessions WHERE id=%s', (UUID(response.json()['id']),)
        ).fetchone()['agent_config']


def model_of_client(client) -> str:
    return model_of((client,))


def load(config: dict, name: str, workspace=None) -> str:
    spec = CATALOG['load_skill']
    context = {'config': config, 'workspace': workspace, 'credentials': {}}
    return asyncio.run(spec.run(context, spec.arguments.model_validate({'name': name})))


def test_a_saved_agents_run_lists_every_enabled_skill_in_name_order(store, saved_agent):  # noqa: F811
    client, inspect = saved_agent[0], saved_agent[3]
    zeta = add_skill(client, 'zeta')
    add_skill(client, 'muted', enabled=False)
    alpha = add_skill(client, 'alpha')
    with store.connect() as conn:
        give(conn, 'operations', [])  # no resources at all: the skills come anyway
    config = started_config(store, client)
    assert config['skills'] == [
        {'id': item['id'], 'name': item['name'], 'description': item['description'], 'folder': item['folder']}
        for item in (alpha, inspect, zeta)
    ]
    assert not [item for item in config['access']['resources'] if item['type'] == 'skill']
    assert '## Files' in load(config, 'zeta')
    later = add_skill(client, 'later')  # enabled after the run started: not in that run
    assert later['name'] not in [item['name'] for item in config['skills']]
    assert load(config, 'later') == 'No skill named later in this run.'
    assert load(config, 'muted') == 'No skill named muted in this run.'
    for prompt in (get_prompt(config), agent_system_prompt(config)):
        assert '- alpha: alpha things\n- inspect: Inspect service health\n- zeta: zeta things\n' in prompt
        assert 'muted' not in prompt


def test_a_skill_whose_folder_is_gone_is_left_out_at_start(store, saved_agent):  # noqa: F811
    client = saved_agent[0]
    gone = add_skill(client, 'gone')
    shutil.rmtree(absolute(gone['folder']))
    config = started_config(store, client)
    assert [item['name'] for item in config['skills']] == ['inspect']


def test_an_ad_hoc_run_gets_every_enabled_skill(sessions):  # noqa: F811
    from test_ad_hoc_agents import build

    add_skill(sessions.client, 'beta')
    add_skill(sessions.client, 'off', enabled=False)
    config = build(sessions)
    assert [item['name'] for item in config['skills']] == ['beta']


def test_load_skill_off_means_no_skills_section():
    skills = [{'id': 'k1', 'name': 'inspect', 'description': 'Inspect.', 'folder': 'skills/k1/v1'}]
    config = {
        'system_prompt': {'content': 'You answer.'},
        'instructions': '',
        'access': {'resources': []},
        'skills': skills,
    }
    assert '## Skills' in get_prompt(config) and '## Skills' in agent_system_prompt(config)
    off = {**config, 'disabled_tools': ['load_skill']}
    for prompt in (get_prompt(off), agent_system_prompt(off)):
        assert '## Skills' not in prompt and 'load_skill' not in prompt


def test_load_skill_and_the_folder_tools_check_the_runs_skills():
    skills = [{'id': 'k1', 'name': 'inspect', 'description': '', 'folder': 'skills/k1/v1'}]
    empty = {'resources': []}
    assert check_load_skill(empty, name='inspect', skills=skills) is None
    assert check_load_skill(empty, name='other', skills=skills) == 'No skill named other in this run.'
    assert check_load_skill(empty, name='inspect') == 'No skill named inspect in this run.'
    for check in (check_list_files, check_shell_run):
        assert check(empty, skills=skills) is None
        assert check(empty) == 'This run has no code projects or skills.'
        # an old access entry of type skill gives nothing
        assert check({'resources': [{'type': 'skill', 'id': 'k1', 'name': 'inspect'}]}) == (
            'This run has no code projects or skills.'
        )


def test_subagents_and_experts_inherit_the_callers_skills():
    skills = [{'id': 'k1', 'name': 'inspect', 'description': '', 'folder': 'skills/k1/v1'}]
    parent = {
        'name': 'Lead',
        'system_prompt': {'content': 'Lead.'},
        'instructions': 'x',
        'access': {'resources': []},
        'skills': skills,
        'experts': [],
    }
    assert subagent_config(parent, None)['skills'] == skills
    expert = {'name': 'Payments', 'access': {'resources': []}, 'skills': [], 'disabled_tools': []}
    assert experts.inherit(expert, parent)['skills'] == skills


def test_the_file_tools_reach_every_listed_skill_folder(store, saved_agent, tmp_path):  # noqa: F811
    client = saved_agent[0]
    other = add_skill(client, 'other')
    config = started_config(store, client)
    workspace = (tmp_path / 'workspace').resolve()
    workspace.mkdir()
    context = {
        'workspace': workspace,
        'credentials': {},
        'config': {**config, 'access': {'resources': []}},
    }
    spec = CATALOG['list_files']
    roots = json.loads(asyncio.run(spec.run(context, spec.arguments.model_validate({}))))['entries']
    folders = [str(absolute(item['folder'])) for item in config['skills']]
    assert [item['path'] for item in roots] == [str(workspace), *folders]
    assert str(absolute(other['folder'])) in folders
    spec = CATALOG['read_file']
    text = asyncio.run(
        spec.run(context, spec.arguments.model_validate({'path': str(absolute(other['folder']) / 'SKILL.md')}))
    )
    assert 'other things' in text


def test_cleanup_keeps_the_folders_of_running_runs_skills(store, saved_agent):  # noqa: F811
    client, inspect = saved_agent[0], saved_agent[3]
    other = add_skill(client, 'other')
    started_config(store, client)
    with store.connect() as conn:
        assert pinned_folders(conn) == {inspect['folder'], other['folder']}
        conn.execute("UPDATE ka.agent_sessions SET status='closed',closed_at=now()")
        assert pinned_folders(conn) == set()


def test_debug_shows_the_runs_skills(store, saved_agent):  # noqa: F811
    from knowledge_admin.sessions.debug import view

    client, inspect = saved_agent[0], saved_agent[3]
    config = started_config(store, client)
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        shown = view(conn, store, project, config, prompt_id=None, prompt_text='')
    assert shown['skills'] == [{'name': 'inspect', 'folder': inspect['folder']}]
    assert not [item for item in shown['access']['resources'] if item['type'] == 'skill']


def test_an_old_access_object_with_skill_entries_loads(store, saved_agent):  # noqa: F811
    client, inspect = saved_agent[0], saved_agent[3]
    with store.connect() as conn:
        give(conn, 'operations', [('skill', inspect['id'])])
    config = get_agent_config('operations', conninfo=store.conninfo)
    assert config['access']['resources'] == []
    agent = client.get('/api/agents/operations', headers=HEADERS).json()
    assert agent['access']['resources'] == []
    refused = update(client, agent, access={'resources': [{'type': 'skill', 'id': inspect['id']}]})
    assert refused.status_code == 400, refused.text


def test_migration_0141_strips_skill_entries(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        actor = conn.execute('SELECT id FROM ka.principals LIMIT 1').fetchone()['id']
        given = [
            {'type': 'skill', 'id': str(uuid4())},
            {'type': 'service', 'id': str(uuid4())},
            {'type': 'skill', 'id': str(uuid4())},
        ]
        agent = f'agent-{uuid4().hex[:6]}'
        conn.execute(
            'INSERT INTO ka.agents(id,name,system_prompt,access) VALUES (%s,%s,%s,%s)',
            (agent, 'Agent', 'Be brief.', Jsonb({'resources': given})),
        )
        session = uuid4()
        conn.execute(
            """INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
            context_snapshot,purpose,agent_config,access) VALUES (%s,%s,%s,%s,%s,'Run','{}','{}','test',%s,%s)""",
            (
                session,
                project,
                agent,
                actor,
                a_model(conn, actor),
                Jsonb({'name': 'Agent', 'access': {'resources': given}}),
                Jsonb({'resources': given}),
            ),
        )
        conn.execute((MIGRATIONS / '0141_skills_for_all.up.sql').read_text(), prepare=False)
        kept = [given[1]]
        assert conn.execute('SELECT access FROM ka.agents WHERE id=%s', (agent,)).fetchone()['access'] == {
            'resources': kept
        }
        row = conn.execute('SELECT access,agent_config FROM ka.agent_sessions WHERE id=%s', (session,)).fetchone()
        assert row['access'] == {'resources': kept}
        assert row['agent_config'] == {'name': 'Agent', 'access': {'resources': kept}}
        conn.execute((MIGRATIONS / '0141_skills_for_all.down.sql').read_text(), prepare=False)
        assert conn.execute('SELECT access FROM ka.agents WHERE id=%s', (agent,)).fetchone()['access'] == {
            'resources': kept
        }
