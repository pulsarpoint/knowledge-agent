"""Skills on demand: the run's instructions list its skills by name and description (section); load_skill returns a
listed skill's instructions (the body of its SKILL.md) and its files at their absolute paths. The skills are the run's
access entries of type skill, each with the folder the run pinned at start: an update during the run does not change
what it reads."""

import asyncio
from typing import Annotated

from pydantic import Field

from knowledge_admin import access as access_rules
from knowledge_admin.capabilities.context import RunContext
from knowledge_admin.skills.rules import parse_skill_md
from knowledge_admin.skills.store import absolute, list_files
from knowledge_admin.tools.registry import tool

UNAVAILABLE = 'Skills are unavailable right now.'
NOTE = 'Read files with read_file; run scripts with shell_run; both take these paths.'
HEADER = """## Skills
A skill is a set of instructions to follow. Below is the list of skills that can be used. Each entry has a name and a description; call `load_skill` with the name to get its full instructions when you use it.
### Available skills"""
HOW_TO = """### How to use skills
- Trigger rules: If the user names a skill OR the task clearly matches a skill's description shown above, you must use that skill for that turn. Multiple mentions mean use them all. Do not carry skills across turns unless re-mentioned.
- Call `load_skill` once per skill, before following it.
- Missing/blocked: If a named skill isn't in the list, say so briefly and continue with the best fallback."""


def section(access: dict) -> str:
    """The skills section of the run's instructions: each skill by name and description; '' when it has none."""
    skills = access_rules.listing(access, 'skill')
    if not skills:
        return ''
    lines = [f'- {item["name"]}: {" ".join((item.get("description") or "").split())}' for item in skills]
    return '\n'.join([HEADER, *lines, HOW_TO])


def _skill(access: dict, name: str) -> dict | None:
    return next((item for item in access_rules.listing(access, 'skill') if item['name'] == name), None)


def check_load_skill(access: dict, name: str, **_) -> str | None:
    """None when a skill of that name is in the run's access, else the refusal text."""
    return None if _skill(access, name) is not None else access_rules.refuse_use('skill')


def _instructions(folder: str | None) -> str:
    """The pinned folder's SKILL.md body, its files at their absolute paths (as the file tools and the shell take them)
    with their sizes, and NOTE; UNAVAILABLE when the folder is unreadable or SKILL.md is broken."""
    if not folder:
        return UNAVAILABLE
    root = absolute(folder)
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
    access = context['config']['access']
    if refusal := check_load_skill(access, name=name):
        return refusal
    return await asyncio.to_thread(_instructions, _skill(access, name).get('folder'))
