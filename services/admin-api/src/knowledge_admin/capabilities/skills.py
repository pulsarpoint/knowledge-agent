"""Skills on demand: the run's instructions list its skills by name, description and the absolute path of their
SKILL.md (section); the run reads that file with read_file (its folder is a file tool root: capabilities/files.py), and
the rest of the folder with list_files and read_file; scripts run with shell_run. Every run gets every enabled skill of
the project: run start pins them in config['skills'] (run_skills), each with its folder, so an update or a switch
during the run does not change what it reads. Skills are not part of the run's access object."""

import json
import os.path

from knowledge_admin.capabilities.files import skill_folder
from knowledge_admin.skills.store import absolute

ENABLED = """SELECT id::text AS id,name,description,folder FROM ka.agent_skills
    WHERE project_id=%s AND enabled ORDER BY name,id"""
HEADER = """## Skills
A skill is a set of instructions to follow. Below is the list of skills that can be used. Each entry has a name, a description and the path of its SKILL.md.
### Available skills"""
HOW_TO = """### How to use skills
- Trigger rules: If the user names a skill OR the task clearly matches a skill's description shown above, you must use that skill for that turn. Multiple mentions mean use them all. Do not carry skills across turns unless re-mentioned.
- When the task matches a skill, read its SKILL.md with read_file before acting, then follow it; its other files are in the same folder (list_files, read_file), and its scripts run with shell_run.
- Missing/blocked: If a named skill isn't in the list, say so briefly and continue with the best fallback."""


def run_skills(conn, project) -> list[dict]:
    """What a run pins as config['skills'] at start: every enabled skill of the project, in name order, as {id, name,
    description, folder}; a skill whose folder is missing on disk is left out (files.skill_folder)."""
    return [dict(row) for row in conn.execute(ENABLED, (project,)) if skill_folder(row) is not None]


def skill_md(skill: dict) -> str:
    """The absolute path of a pinned skill's SKILL.md: the path read_file and shell_run take."""
    return str(absolute(skill['folder']) / 'SKILL.md')


def section(config: dict) -> str:
    """The skills section of the run's instructions: each of config['skills'] by name, description and SKILL.md path;
    '' when it has none or read_file is switched off for the run (config['disabled_tools'])."""
    skills = config.get('skills') or []
    if not skills or 'read_file' in config.get('disabled_tools', ()):
        return ''
    lines = [
        f'- {item["name"]}: {" ".join((item.get("description") or "").split())} — {skill_md(item)}' for item in skills
    ]
    return '\n'.join([HEADER, *lines, HOW_TO])


def skills_used(skills: list[dict], tool_calls: list[dict]) -> list[str]:
    """The names of the skills (config['skills'] order) whose SKILL.md a run read: a read_file call
    ({name, arguments: the JSON text}) with that path, spelled any equivalent way (normalised). Other files of the folder
    do not count."""
    read = set()
    for call in tool_calls:
        if call.get('name') != 'read_file':
            continue
        try:
            path = json.loads(call.get('arguments') or '{}').get('path')
        except (ValueError, AttributeError):
            continue
        if isinstance(path, str):
            read.add(os.path.normpath(path))
    return [item['name'] for item in skills if os.path.normpath(skill_md(item)) in read]
