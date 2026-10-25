"""Skills on demand: the run's instructions list its skills by name and description (section); load_skill returns a
listed skill's instructions (the body of its SKILL.md) and its files at their absolute paths. Every run gets every
enabled skill of the project: run start pins them in config['skills'] (run_skills), each with its folder, so an update
or a switch during the run does not change what it reads. Skills are not part of the run's access object."""

import asyncio
from typing import Annotated

from pydantic import Field

from knowledge_admin.capabilities.context import RunContext
from knowledge_admin.capabilities.files import skill_folder
from knowledge_admin.skills.rules import parse_skill_md
from knowledge_admin.skills.store import list_files
from knowledge_admin.tools.registry import tool

UNAVAILABLE = 'Skills are unavailable right now.'
NO_SKILL = 'No skill named {name} in this run.'
ENABLED = """SELECT id::text AS id,name,description,folder FROM ka.agent_skills
    WHERE project_id=%s AND enabled ORDER BY name,id"""
FILES_UNAVAILABLE = "This skill's files are unavailable."
NOTE = 'Read files with read_file; run scripts with shell_run; both take these paths.'
HEADER = """## Skills
A skill is a set of instructions to follow. Below is the list of skills that can be used. Each entry has a name and a description; call `load_skill` with the name to get its full instructions when you use it.
### Available skills"""
HOW_TO = """### How to use skills
- Trigger rules: If the user names a skill OR the task clearly matches a skill's description shown above, you must use that skill for that turn. Multiple mentions mean use them all. Do not carry skills across turns unless re-mentioned.
- Call `load_skill` once per skill, before following it.
- Missing/blocked: If a named skill isn't in the list, say so briefly and continue with the best fallback."""


def run_skills(conn, project) -> list[dict]:
    """What a run pins as config['skills'] at start: every enabled skill of the project, in name order, as {id, name,
    description, folder}; a skill whose folder is missing on disk is left out (files.skill_folder)."""
    return [dict(row) for row in conn.execute(ENABLED, (project,)) if skill_folder(row) is not None]


def section(config: dict) -> str:
    """The skills section of the run's instructions: each of config['skills'] by name and description; '' when it has
    none or load_skill is switched off for the run (config['disabled_tools'])."""
    skills = config.get('skills') or []
    if not skills or 'load_skill' in config.get('disabled_tools', ()):
        return ''
    lines = [f'- {item["name"]}: {" ".join((item.get("description") or "").split())}' for item in skills]
    return '\n'.join([HEADER, *lines, HOW_TO])


def _skill(skills, name: str) -> dict | None:
    return next((item for item in skills if item['name'] == name), None)


def check_load_skill(access: dict, name: str, skills=(), **_) -> str | None:
    """None when a skill of that name is in the run's skills (config['skills']), else the refusal text."""
    return None if _skill(skills, name) is not None else NO_SKILL.format(name=name)


def _instructions(entry: dict) -> str:
    """The pinned folder's SKILL.md body, its files at their absolute paths (as the file tools and the shell take them)
    with their sizes, and NOTE; FILES_UNAVAILABLE when the folder is missing (files.skill_folder), UNAVAILABLE when it is
    unreadable or SKILL.md is broken."""
    root = skill_folder(entry)
    if root is None:
        return FILES_UNAVAILABLE
    try:
        body = parse_skill_md((root / 'SKILL.md').read_text(encoding='utf-8'))['body']
        files = list_files(root)
    except (OSError, ValueError):
        return UNAVAILABLE
    lines = [f'- {root / item["path"]} ({item["size"]} bytes)' for item in files]
    return '\n'.join([body.rstrip(), '', '## Files', *lines, '', NOTE])


@tool
async def load_skill(
    context: RunContext,
    name: Annotated[str, Field(min_length=1, max_length=200, description='The skill name, as listed under Skills.')],
) -> str:
    """Get the full instructions of a skill listed under Skills in your instructions, and the list of its files at their absolute paths. Call it once per skill, before following the skill."""
    config = context['config']
    skills = config.get('skills') or []
    if refusal := check_load_skill(config['access'], name=name, skills=skills):
        return refusal
    return await asyncio.to_thread(_instructions, _skill(skills, name))
