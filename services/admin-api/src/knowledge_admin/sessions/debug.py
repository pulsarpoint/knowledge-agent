"""The Debug tab: what the model of a run receives, built from a configuration by the runner's own code
(agents.runner.instructions_and_tools, which create_agent uses), so it cannot drift from what runs. GET
/api/sessions/{id}/debug shows a run's pinned configuration; POST /api/sessions/preview the one a start would pin.
Every text shown is redacted with the configuration's secrets, as run records are."""

from typing import Any
from uuid import UUID

from knowledge_admin import access as access_rules
from knowledge_admin.agents.runner import instructions_and_tools, known_secrets
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.sessions.activity import redact


def instructions_and_tool_list(config: dict[str, Any], secrets: list[str]) -> dict[str, Any]:
    """{instructions, tools: [{name, description}]} as a session turn gives them to the model (a session turn offers
    subagents and experts; never the run's off tools), redacted with `secrets` (known_secrets). MCP tools are listed by their servers once a run
    connects; the servers are in the access object."""
    prompt, tools, experts, _ = instructions_and_tools(
        config, subagents=True, output_schema=config.get('output_schema')
    )
    return {
        'instructions': redact(prompt, secrets),
        'tools': [{'name': tool.name, 'description': redact(tool.description, secrets)} for tool in [*tools, *experts]],
    }


def prompt_origin(conn, project: UUID, prompt_id: UUID | str | None, text: str) -> dict[str, Any] | None:
    """The library prompt a run's prompt came from: {id, name, version}, the version whose text the run used (None when
    the text was edited before the start)."""
    if prompt_id is None:
        return None
    row = conn.execute(
        'SELECT id,name FROM ka.documentation_prompts WHERE id=%s AND project_id=%s', (prompt_id, project)
    ).fetchone()
    if row is None:
        return None
    versions = conn.execute(
        'SELECT version,content FROM ka.documentation_prompt_versions WHERE prompt_id=%s ORDER BY version DESC',
        (row['id'],),
    ).fetchall()
    version = next((item['version'] for item in versions if item['content'].strip() == text.strip()), None)
    return {'id': str(row['id']), 'name': row['name'], 'version': version}


def view(
    conn,
    store: RepositoryStore,
    project: UUID,
    config: dict[str, Any],
    *,
    prompt_id: UUID | str | None,
    prompt_text: str,
) -> dict[str, Any]:
    """The Debug tab of a configuration. prompt_id, prompt_text: the run's (or the start request's)."""
    secrets = known_secrets(store, config)
    model = config['model']
    return {
        **instructions_and_tool_list(config, secrets),
        'prompt': prompt_origin(conn, project, prompt_id, prompt_text),
        'agent_instructions': redact(config['instructions'], secrets),
        'skills': [{'name': item['name']} for item in access_rules.listing(config['access'], 'skill')],
        # what the run may use: resources with their source (never their connections: access.listing)
        'access': {'resources': access_rules.listing(config['access'])},
        'model': {'id': str(model['id']), 'name': model['name']} if model else None,
        'notes': config.get('start_notes', []),  # what the start left out, and why
        'output_schema': config.get('output_schema'),  # the pinned {id, name, version, schema}; None: a normal run
    }
