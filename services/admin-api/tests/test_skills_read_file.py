"""Skills are read with read_file: the Skills section lists each skill with the path of its SKILL.md (and is left out
when read_file is off), load_skill is gone, and the skills a run used are the SKILL.md files it read."""

import asyncio
import json
from uuid import UUID

from knowledge_admin.agents.runner import get_prompt
from knowledge_admin.capabilities.skills import skills_used
from knowledge_admin.knowledge_agents.config import get_agent_config
from knowledge_admin.sessions.text import agent_system_prompt
from knowledge_admin.skills.store import absolute
from knowledge_admin.tools import selection
from knowledge_admin.tools.catalog import CATALOG
from psycopg.types.json import Jsonb
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_repositories import HEADERS
from test_skills_for_all import model_of_client
from test_test_sessions import test_start

HOW_TO = (
    '- When the task matches a skill, read its SKILL.md with read_file before acting, then follow it; its other files '
    'are in the same folder (list_files, read_file), and its scripts run with shell_run.'
)
TRIGGER = (
    "- Trigger rules: If the user names a skill OR the task clearly matches a skill's description shown above, you "
    'must use that skill for that turn. Multiple mentions mean use them all. Do not carry skills across turns unless '
    're-mentioned.'
)


def entry(name: str) -> dict:
    return {'id': name, 'name': name, 'description': f'{name}  things\n', 'folder': f'skills/{name}/v1'}


def config(*skills: dict, **more) -> dict:
    return {
        'system_prompt': {'content': 'You answer.'},
        'instructions': '',
        'access': {'resources': []},
        'skills': list(skills),
        **more,
    }


def path_of(skill: dict) -> str:
    return str(absolute(skill['folder']) / 'SKILL.md')


def read(skill: dict, name: str = 'read_file') -> dict:
    return {'name': name, 'arguments': json.dumps({'path': path_of(skill)})}


def test_the_section_lists_each_skill_with_the_path_of_its_skill_md():
    alpha, beta = entry('alpha'), entry('beta')
    for prompt in (get_prompt(config(alpha, beta)), agent_system_prompt(config(alpha, beta))):
        assert f'- alpha: alpha things — {path_of(alpha)}\n- beta: beta things — {path_of(beta)}\n' in prompt
        assert HOW_TO in prompt and TRIGGER in prompt
        assert 'load_skill' not in prompt


def test_the_section_is_left_out_when_read_file_is_off():
    off = config(entry('alpha'), disabled_tools=['read_file'])
    for prompt in (get_prompt(off), agent_system_prompt(off)):
        assert '## Skills' not in prompt


def test_load_skill_is_gone():
    assert 'load_skill' not in CATALOG
    assert selection.known(['load_skill', 'shell_run']) == ['shell_run']


def test_an_agent_that_stored_load_skill_as_off_still_loads(store, saved_agent):  # noqa: F811
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET disabled_tools=%s WHERE id='operations'", (['load_skill', 'shell_run'],))
    assert get_agent_config('operations', conninfo=store.conninfo)['disabled_tools'] == ['shell_run']


def test_skills_used_are_the_skill_md_files_read_with_read_file():
    alpha, beta, gamma = entry('alpha'), entry('beta'), entry('gamma')
    skills = [alpha, beta, gamma]
    other_file = {'name': 'read_file', 'arguments': json.dumps({'path': str(absolute(beta['folder']) / 'notes.md')})}
    calls = [read(gamma), read(alpha), read(alpha), other_file, read(beta, 'shell_run'), {'name': 'read_file'}]
    assert skills_used(skills, calls) == ['alpha', 'gamma']
    assert skills_used(skills, []) == [] and skills_used([], calls) == []
    assert skills_used(skills, [{'name': 'read_file', 'arguments': 'not json'}]) == []


def test_an_agent_reads_skill_md_at_the_listed_path(store, saved_agent, tmp_path):  # noqa: F811
    skill = saved_agent[3]
    context = {
        'workspace': tmp_path,
        'credentials': {},
        'config': {'access': {'resources': []}, 'skills': [{**skill, 'description': 'd'}]},
    }
    spec = CATALOG['read_file']
    text = asyncio.run(spec.run(context, spec.arguments.model_validate({'path': path_of(skill)})))
    assert 'Check the exit code.' in json.loads(text)['content']


def test_debug_and_the_session_detail_show_the_skills_used(store, saved_agent):  # noqa: F811
    client, skill = saved_agent[0], saved_agent[3]
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET model_id=%s,enabled=true WHERE id='operations'", (model_of_client(client),))
    response = client.post('/api/sessions', headers=HEADERS, json=test_start(client, 'operations', 'Hi'))
    assert response.status_code == 201, response.text
    session = response.json()['id']
    for url in (f'/api/sessions/{session}/debug', f'/api/sessions/{session}'):
        assert client.get(url, headers=HEADERS).json()['skills_used'] == []
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute('UPDATE ka.agent_sessions SET turns=1 WHERE id=%s', (UUID(session),))
        conn.execute(
            """INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
            VALUES (%s,%s,900,'tool_call','operations',%s)""",
            (
                project,
                f'cc:{UUID(session)}:1',
                Jsonb({'name': 'read_file', 'arguments': json.dumps({'path': path_of(skill)})}),
            ),
        )
    for url in (f'/api/sessions/{session}/debug', f'/api/sessions/{session}'):
        assert client.get(url, headers=HEADERS).json()['skills_used'] == ['inspect']
