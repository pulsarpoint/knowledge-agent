"""Skills on demand: the instructions list each skill the run pinned (config['skills']) by name and description (never
its text); load_skill returns a listed skill's instructions from the folder the run pinned and refuses any other (its
file list: test_skill_runtime)."""

import asyncio

import pytest
from knowledge_admin.agents.runner import get_prompt
from knowledge_admin.knowledge_agents.config import get_agent_config
from knowledge_admin.capabilities import inventory
from knowledge_admin.capabilities.skills import check_load_skill
from knowledge_admin.capabilities.skills import run_skills
from knowledge_admin.sessions.run_access import agent_access
from knowledge_admin.sessions.text import agent_system_prompt
from knowledge_admin.tools.catalog import CATALOG
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)

REFUSED = 'No skill named {name} in this run.'
SECTION = """## Skills
A skill is a set of instructions to follow. Below is the list of skills that can be used. Each entry has a name and a description; call `load_skill` with the name to get its full instructions when you use it.
### Available skills
- inspect: Inspect service health
### How to use skills
- Trigger rules: If the user names a skill OR the task clearly matches a skill's description shown above, you must use that skill for that turn. Multiple mentions mean use them all. Do not carry skills across turns unless re-mentioned.
- Call `load_skill` once per skill, before following it.
- Missing/blocked: If a named skill isn't in the list, say so briefly and continue with the best fallback."""


def skill(id_: str, name: str) -> dict:
    return {'id': id_, 'name': name, 'description': '', 'folder': f'skills/{id_}/v1'}


def bare_config(*skills: dict) -> dict:
    return {
        'system_prompt': {'content': 'You answer.'},
        'instructions': 'Be brief.',
        'access': {'resources': []},
        'skills': list(skills),
    }


@pytest.fixture
def run(store, saved_agent, monkeypatch):  # noqa: F811
    """A run of the saved agent: its configuration with the run's access object, as run start pins it."""
    monkeypatch.setattr(inventory, '_store', store)
    config = get_agent_config('operations', conninfo=store.conninfo)
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        access = agent_access(conn, project, config, None)
        skills = run_skills(conn, project)
    return {**config, 'access': access, 'skills': skills, 'project_id': str(project)}, saved_agent[3]


def load(config: dict, name: str) -> str:
    spec = CATALOG['load_skill']
    return asyncio.run(spec.run({'config': config}, spec.arguments.model_validate({'name': name})))


def test_the_instructions_list_the_skills_by_name_and_description_only(run):
    config, _ = run
    for prompt in (get_prompt(config), agent_system_prompt(config)):
        assert SECTION in prompt
        assert 'Check the exit code.' not in prompt
        assert '# Skills\n\n[' not in prompt


def test_a_run_without_skills_has_no_section():
    for prompt in (get_prompt(bare_config()), agent_system_prompt(bare_config() | {'skills': []})):
        assert '## Skills' not in prompt and 'load_skill' not in prompt


def test_load_skill_returns_a_listed_skills_instructions(run):
    config, _ = run
    assert load(config, 'inspect').startswith('Check the exit code.\n\n## Files\n')


def test_load_skill_refuses_an_unlisted_skill(run):
    config, _ = run
    assert load(config, 'other') == REFUSED.format(name='other')
    assert load({**config, 'skills': []}, 'inspect') == REFUSED.format(name='inspect')


def test_a_skill_switched_off_during_a_run_stays_pinned(run, store):
    config, found = run
    with store.connect() as conn:
        conn.execute('UPDATE ka.agent_skills SET enabled=false WHERE id=%s', (found['id'],))
    assert load(config, 'inspect').startswith('Check the exit code.')


def test_check_load_skill_matches_on_the_skill_name():
    skills = bare_config(skill('k1', 'inspect'))['skills']
    assert check_load_skill({'resources': []}, name='inspect', skills=skills) is None
    assert check_load_skill({'resources': []}, name='k1', skills=skills) == REFUSED.format(name='k1')
    assert check_load_skill({'resources': []}, name='inspect') == REFUSED.format(name='inspect')


def test_a_missing_or_broken_folder_is_a_refusal(run, skills_folder):
    config, _ = run
    [entry] = config['skills']
    gone = {**config, 'skills': [{**entry, 'folder': 'skills/gone/v1'}]}
    assert load(gone, 'inspect') == "This skill's files are unavailable."
    unpinned = {**config, 'skills': [{k: v for k, v in entry.items() if k != 'folder'}]}
    assert load(unpinned, 'inspect') == "This skill's files are unavailable."
    (skills_folder / entry['folder'] / 'SKILL.md').write_text('no frontmatter')
    assert load(config, 'inspect') == 'Skills are unavailable right now.'
