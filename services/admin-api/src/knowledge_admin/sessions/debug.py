"""The Debug tab: what the model of a run receives, built from a configuration by the runner's own code
(agents.runner.instructions_and_tools, which create_agent uses), so it cannot drift from what runs. GET
/api/sessions/{id}/debug shows a run's pinned configuration; POST /api/sessions/preview the one a start would pin.
Every text shown is redacted with the configuration's secrets, as run records are."""

from typing import Any
from uuid import UUID

from knowledge_admin.capabilities import mcp
from knowledge_admin.agents.runner import instructions_and_tools, known_secrets, ssh_servers
from knowledge_admin.rag.collections import document_collections
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.sessions.activity import redact

WIZARD_TAG = 'proposals'  # a run started from the Propose wizard: its prompt was written there


def instructions_and_tool_list(config: dict[str, Any], secrets: list[str]) -> dict[str, Any]:
    """{instructions, tools: [{name, description}]} as a session turn gives them to the model (a session turn offers
    subagents and experts), redacted with `secrets` (known_secrets). MCP tools are listed by their servers once a run
    connects; the servers are in resource_names."""
    prompt, tools, experts, _ = instructions_and_tools(config, ssh_servers(config), subagents=True)
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


def resource_names(config: dict[str, Any]) -> dict[str, list[str]]:
    """What the run is pinned to, by name."""
    documents = config['documents']['enabled']
    return {
        'code_projects': [item['name'] for item in config['code']['projects']] if config['code']['enabled'] else [],
        'document_collections': [item['name'] for item in document_collections(config)[1]] if documents else [],
        'servers': [server['name'] for server in ssh_servers(config)],
        'services': [item['name'] for item in config['services']['items']] if config['services']['enabled'] else [],
        'mcp_servers': [server['name'] for server in mcp.selected_servers(config, ssh_servers(config))],
        'documents': [entry['title'] for entry in config.get('document_access') or []],
    }


def view(
    conn,
    store: RepositoryStore,
    project: UUID,
    config: dict[str, Any],
    *,
    tags: list[str],
    prompt_id: UUID | str | None,
    prompt_text: str,
) -> dict[str, Any]:
    """The Debug tab of a configuration. tags, prompt_id, prompt_text: the run's (or the start request's)."""
    secrets = known_secrets(store, config)
    wizard = WIZARD_TAG in tags
    skills = [item for item in config['skills']['items'] if item['enabled']] if config['skills']['enabled'] else []
    model = config['model']
    return {
        **instructions_and_tool_list(config, secrets),
        'prompt': None if wizard else prompt_origin(conn, project, prompt_id, prompt_text),
        'prompt_source': 'wizard' if wizard else 'library',
        'agent_instructions': redact(config['instructions'], secrets),
        'skills': [{'name': item['name']} for item in skills],
        'resources': resource_names(config),
        'model': {'id': str(model['id']), 'name': model['name']} if model else None,
        'notes': config.get('start_notes', []),  # what the start left out, and why
    }
