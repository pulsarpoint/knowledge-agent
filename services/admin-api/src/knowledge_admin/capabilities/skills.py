"""Skills on demand: the run's instructions list its skills by name and description (section); load_skill returns a
listed skill's instructions from ka.agent_skills. The skills are the run's access entries of type skill."""

import asyncio
from typing import Annotated, Any

import psycopg
from pydantic import Field

from knowledge_admin import access as access_rules
from knowledge_admin.capabilities.context import RunContext
from knowledge_admin.capabilities.inventory import UNAVAILABLE as STORE_UNAVAILABLE
from knowledge_admin.capabilities.inventory import connect, project_of
from knowledge_admin.tools.registry import tool

UNAVAILABLE = 'Skills are unavailable right now.'
INSTRUCTIONS = """SELECT instructions FROM ka.agent_skills WHERE project_id=%s AND id=%s AND enabled"""
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
    lines = [f'- {item["name"]}: {" ".join(item["description"].split())}' for item in skills]
    return '\n'.join([HEADER, *lines, HOW_TO])


def _skill(access: dict, name: str) -> dict | None:
    return next((item for item in access_rules.listing(access, 'skill') if item['name'] == name), None)


def check_load_skill(access: dict, name: str, **_) -> str | None:
    """None when a skill of that name is in the run's access, else the refusal text."""
    return None if _skill(access, name) is not None else access_rules.refuse_use('skill')


def _instructions(config: dict[str, Any], skill_id: str) -> str:
    """The skill's instructions; a refusal when it is disabled or gone, or the database is unavailable (as the memory
    tools refuse)."""
    try:
        with connect() as conn:
            row = conn.execute(INSTRUCTIONS, (project_of(conn, config), skill_id)).fetchone()
    except ValueError as error:
        if str(error) != STORE_UNAVAILABLE:  # only 'the store is not configured in this process' is a refusal
            raise
        return UNAVAILABLE
    except psycopg.OperationalError:  # the database is down or unreachable
        return UNAVAILABLE
    return row['instructions'] if row is not None else access_rules.refuse_use('skill')


@tool
async def load_skill(
    context: RunContext,
    name: Annotated[str, Field(min_length=1, max_length=200, description='The skill name, as listed under Skills.')],
) -> str:
    """Get the full instructions of a skill listed under Skills in your instructions. Call it once per skill, before following the skill."""
    access = context['config']['access']
    if refusal := check_load_skill(access, name=name):
        return refusal
    return await asyncio.to_thread(_instructions, context['config'], _skill(access, name)['id'])
