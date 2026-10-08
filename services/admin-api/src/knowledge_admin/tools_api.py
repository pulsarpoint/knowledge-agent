"""An agent's tools: the catalog, the Tools tab (GET/PUT) and the rules that keep a selection consistent."""
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator

from knowledge_admin.access import request_actor
from knowledge_admin.tools.catalog import (CATALOG, NEED_REASONS, NEED_TABS, capability_defaults, catalog_listing,
                                           implied_tools, needs_met)

STALE = 'The tools changed since you opened them. Reload.'
GROUNDED = 'Grounded answers need report_not_covered.'
UNKNOWN = 'Unknown tool: {tool}.'
MCP_NOT_SELECTED = 'MCP tool {tool} belongs to a server this agent has not selected.'
CLAUDE_CODE = 'Claude Code agents use their Claude Code tools. Switch them on the General tab.'
MCP_REASON = 'Needs MCP'
# A capability's own tools (by need): switching it on while none of them is on switches its defaults on.
PRIMARY = {'code': ('code', 'code_graph'), 'documents': ('documents', 'grounded'), 'ssh': ('ssh',)}

AGENT = '''SELECT id,runtime,revision,claude_code_tools,repositories_enabled,documents_enabled,documents_grounded,
    ssh_enabled,mcp_enabled FROM ka.agents WHERE id=%s'''
# As the agent configuration selects them: the application project's servers, linked in the same project.
SELECTED_MCP = '''SELECT m.id::text AS id,m.name,m.enabled,m.tools FROM ka.mcp_servers m
    WHERE m.project_id=%s AND EXISTS (
    SELECT 1 FROM ka.agent_config_mcp_servers s JOIN ka.agent_configs c ON c.id=s.config_id
    WHERE s.mcp_server_id=m.id AND c.agent=%s AND c.project_id=m.project_id) ORDER BY lower(m.name),m.id'''
ON = "SELECT tool FROM ka.agent_tools WHERE agent_id=%s AND mode='on'"
SWITCH_ON = '''INSERT INTO ka.agent_tools(agent_id,tool,mode) VALUES (%s,%s,'on')
    ON CONFLICT (agent_id,tool) DO UPDATE SET mode='on',updated_at=now()'''


class ToolChoice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tool: str = Field(min_length=1, max_length=300)
    mode: Literal['on', 'off']  # per run comes with a later step
    usage: str | None = Field(default=None, max_length=8000)
    subagent_allowed: bool | None = None  # accepted and ignored: step 1 always saves true


class ToolsInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=1, strict=True)
    tools: list[ToolChoice] = Field(max_length=500)

    @field_validator('tools')
    @classmethod
    def once(cls, value: list[ToolChoice]) -> list[ToolChoice]:
        if len({choice.tool for choice in value}) != len(value):
            raise ValueError('List each tool once.')
        return value


def flags(row: dict[str, Any]) -> dict[str, Any]:
    """The capability switches of an agent row (or a proposal) in config shape, for the catalog rules."""
    return {'runtime': row['runtime'], 'code': {'enabled': row['repositories_enabled']},
            'documents': {'enabled': row['documents_enabled'], 'grounded': row['documents_grounded']},
            'ssh': {'enabled': row['ssh_enabled']}}


def _uuid(value: str) -> UUID | None:
    """An agent ID as an audit resource_id (a uuid column); seeded agents with text IDs have none."""
    try:
        return UUID(value)
    except ValueError:
        return None


def _usage(text: str | None) -> str | None:
    return text if text is not None and text.strip() else None  # blank: the built-in text


def agent_tools(conn, agent_id: str, project) -> dict[str, Any]:
    agent = conn.execute(AGENT, (agent_id,)).fetchone()
    if agent is None:
        raise HTTPException(404, 'Agent not found.')
    if agent['runtime'] != 'openai':
        return {'runtime': agent['runtime'], 'revision': agent['revision'],
                'claude_code_tools': list(agent['claude_code_tools']), 'tools': []}
    saved = {row['tool']: row for row in conn.execute(
        'SELECT tool,mode,usage,subagent_allowed FROM ka.agent_tools WHERE agent_id=%s', (agent_id,))}
    current = flags(agent)
    tools = []
    for spec in CATALOG.values():
        row = saved.get(spec.name)
        available = needs_met(spec.needs, current, None)
        tools.append({'tool': spec.name, 'label': spec.label, 'summary': spec.summary, 'group': spec.group,
                      'mode': row['mode'] if row else 'off', 'usage': row['usage'] if row else None,
                      'default_usage': spec.usage, 'subagent_allowed': row['subagent_allowed'] if row else True,
                      'available': available, 'reason': None if available else NEED_REASONS[spec.needs],
                      'tab': NEED_TABS.get(spec.needs)})
    for server in conn.execute(SELECTED_MCP, (project, agent_id)):
        available = agent['mcp_enabled'] and server['enabled']
        for found in server['tools']:
            name = f"mcp:{server['id']}:{found['name']}"
            row = saved.get(name)
            tools.append({'tool': name, 'label': f"{server['name']} / {found['name']}", 'summary': '', 'group': 'mcp',
                          'mode': row['mode'] if row else 'on', 'usage': row['usage'] if row else None,
                          'default_usage': found.get('description', ''),
                          'subagent_allowed': row['subagent_allowed'] if row else True,
                          'available': available, 'reason': None if available else MCP_REASON, 'tab': 'mcp'})
    return {'runtime': 'openai', 'revision': agent['revision'], 'tools': tools}


def switch_on_defaults(conn, agent_id: str, old: dict[str, Any], new: dict[str, Any]) -> None:
    """Profile save: a capability switched on while none of its own tools is on gets its default tools; grounded
    answers always get report_not_covered. Switching a capability off keeps every row."""
    if new['runtime'] != 'openai':
        return
    on = {row['tool'] for row in conn.execute(ON, (agent_id,))}
    wanted: set[str] = set()
    for capability in ('code', 'documents', 'ssh'):
        if new[capability]['enabled'] and not old[capability]['enabled']:
            own = {spec.name for spec in CATALOG.values() if spec.needs in PRIMARY[capability]}
            if not on & own:
                wanted.update(capability_defaults(capability, new))
    if new['documents']['enabled'] and new['documents']['grounded']:
        wanted.add('report_not_covered')
    for name in sorted(wanted - on):
        conn.execute(SWITCH_ON, (agent_id, name))


def ensure_native_tools(conn, agent_id: str, current: dict[str, Any]) -> None:
    """An agent that becomes native without any tool rows gets the tools its capabilities give (the backfill)."""
    if conn.execute('SELECT 1 FROM ka.agent_tools WHERE agent_id=%s LIMIT 1', (agent_id,)).fetchone():
        return
    for entry in implied_tools(current)['items']:
        if entry['mode'] == 'on':
            conn.execute(SWITCH_ON, (agent_id, entry['tool']))


def build_api(app_name, store):
    router = APIRouter()

    def database():
        if store is None:
            raise HTTPException(503, 'Agent storage is not configured.')
        return store

    @router.get('/tools')
    def catalog():
        return {'tools': catalog_listing()}

    @router.get('/agents/{agent_id}/tools')
    def tools(agent_id: str, response: Response):
        response.headers['Cache-Control'] = 'no-store'
        with database().connect() as conn:
            return agent_tools(conn, agent_id, store.project(conn, app_name))

    @router.put('/agents/{agent_id}/tools')
    def save(agent_id: str, value: ToolsInput, request: Request):
        with database().connect() as conn:
            agent = conn.execute(AGENT + ' FOR UPDATE', (agent_id,)).fetchone()
            if agent is None:
                raise HTTPException(404, 'Agent not found.')
            if agent['runtime'] != 'openai':
                raise HTTPException(400, CLAUDE_CODE)
            if agent['revision'] != value.expected_revision:
                raise HTTPException(409, STALE)
            servers = {row['id']: {found['name'] for found in row['tools']} for row in conn.execute(SELECTED_MCP, (store.project(conn, app_name), agent_id))}
            for choice in value.tools:
                if choice.tool in CATALOG:
                    continue
                kind, _, rest = choice.tool.partition(':')
                server, _, name = rest.partition(':')
                if kind != 'mcp' or not name:
                    raise HTTPException(400, UNKNOWN.format(tool=choice.tool))
                if name not in servers.get(server, set()):
                    raise HTTPException(400, MCP_NOT_SELECTED.format(tool=choice.tool))
            chosen = {choice.tool: choice for choice in value.tools}
            report = chosen.get('report_not_covered')
            if agent['documents_enabled'] and agent['documents_grounded'] and (report is None or report.mode != 'on'):
                raise HTTPException(400, GROUNDED)
            conn.execute('DELETE FROM ka.agent_tools WHERE agent_id=%s', (agent_id,))
            for choice in value.tools:
                usage = _usage(choice.usage)
                if choice.tool.startswith('mcp:') and choice.mode == 'on' and usage is None:
                    continue  # an MCP tool's default: no row
                conn.execute('INSERT INTO ka.agent_tools(agent_id,tool,mode,usage) VALUES (%s,%s,%s,%s)',
                             (agent_id, choice.tool, choice.mode, usage))
            actor = store.actor(conn, *request_actor(request))
            conn.execute('UPDATE ka.agents SET revision=revision+1,updated_by=%s WHERE id=%s', (actor, agent_id))
            conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
                VALUES (%s,%s,'agent.tools.updated','agent',%s,%s)''', (store.project(conn, app_name), actor,
                _uuid(agent_id), Jsonb({'agent_id': agent_id,
                                        'on': sorted(choice.tool for choice in value.tools if choice.mode == 'on')})))
            return agent_tools(conn, agent_id, store.project(conn, app_name))

    return router
