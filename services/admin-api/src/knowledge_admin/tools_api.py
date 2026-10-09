"""An agent's tools: the catalog, the Tools tab (GET/PUT) and the rules that keep a selection consistent."""
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator

from knowledge_admin.access import request_actor
from knowledge_admin.knowledge_agents.experts import ROW_PREFIX
from knowledge_admin.tools.catalog import (CATALOG, SUBAGENT, NEED_REASONS, NEED_TABS, capability_tools,
                                           catalog_listing, enabled_capability_tools, needs_met)

STALE = 'The tools changed since you opened them. Reload.'
GROUNDED = 'Grounded answers need report_not_covered.'
UNKNOWN = 'Unknown tool: {tool}.'
UNKNOWN_EXPERT = 'Unknown expert: {tool}.'
MCP_NOT_SELECTED = 'MCP tool {tool} belongs to a server this agent has not selected.'
MCP_REASON = 'Needs MCP'
SUBAGENTS_OFF = 'Turn on May start subagents in General first.'
SUBAGENT_SWITCH = 'run_subagent comes with May start subagents.'
# May start subagents is this row: always on, never given to a subagent (0078's subagents_do_not_spawn).
SUBAGENT_ROW = '''INSERT INTO ka.agent_tools(agent_id,tool,mode,usage,subagent_allowed) VALUES (%s,'run_subagent','on',%s,false)
    ON CONFLICT (agent_id,tool) DO UPDATE SET mode='on',subagent_allowed=false,updated_at=now()'''
INSERT_TOOL = 'INSERT INTO ka.agent_tools(agent_id,tool,mode,usage,subagent_allowed) VALUES (%s,%s,%s,%s,%s)'

AGENT = '''SELECT id,revision,repositories_enabled,documents_enabled,documents_grounded,
    ssh_enabled,mcp_enabled,expert FROM ka.agents WHERE id=%s'''
# Every other expert, including those this agent switched off (the Tools tab shows the switch).
EXPERTS = '''SELECT id,name,when_to_use,resources FROM ka.agents WHERE expert AND id<>%s ORDER BY lower(name),id'''
# As the agent configuration selects them: the application project's servers, linked in the same project.
SELECTED_MCP = '''SELECT m.id::text AS id,m.name,m.enabled,m.tools FROM ka.mcp_servers m
    WHERE m.project_id=%s AND EXISTS (
    SELECT 1 FROM ka.agent_config_mcp_servers s JOIN ka.agent_configs c ON c.id=s.config_id
    WHERE s.mcp_server_id=m.id AND c.agent=%s AND c.project_id=m.project_id) ORDER BY lower(m.name),m.id'''
SWITCH_ON = '''INSERT INTO ka.agent_tools(agent_id,tool,mode) VALUES (%s,%s,'on')
    ON CONFLICT (agent_id,tool) DO UPDATE SET mode='on',updated_at=now()'''


class ToolChoice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tool: str = Field(min_length=1, max_length=300)
    mode: Literal['on', 'off']  # per run comes with a later step
    usage: str | None = Field(default=None, max_length=8000)
    subagent_allowed: bool | None = None  # None: a subagent may use it


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
    return {'resources': row.get('resources', 'fixed'),
            'code': {'enabled': row['repositories_enabled']},
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


def subagents_on(conn, agent_id: str) -> bool:
    """May start subagents: whether the agent has the run_subagent row."""
    return conn.execute("SELECT 1 FROM ka.agent_tools WHERE agent_id=%s AND tool='run_subagent'",
                        (agent_id,)).fetchone() is not None


def switch_subagents(conn, agent_id: str, on: bool) -> None:
    """May start subagents on (the row, with the default usage text unless it has one) or off (the row is deleted)."""
    if on:
        conn.execute(SUBAGENT_ROW, (agent_id, None))
    else:
        conn.execute("DELETE FROM ka.agent_tools WHERE agent_id=%s AND tool='run_subagent'", (agent_id,))


def agent_tools(conn, agent_id: str, project) -> dict[str, Any]:
    agent = conn.execute(AGENT, (agent_id,)).fetchone()
    if agent is None:
        raise HTTPException(404, 'Agent not found.')
    saved = {row['tool']: row for row in conn.execute(
        'SELECT tool,mode,usage,subagent_allowed FROM ka.agent_tools WHERE agent_id=%s', (agent_id,))}
    current = flags(agent)
    tools = []
    for spec in CATALOG.values():
        row = saved.get(spec.name)
        if spec.name == SUBAGENT and row is None:
            continue  # listed only while May start subagents is on
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
    for found in conn.execute(EXPERTS, (agent_id,)):
        name = f'{ROW_PREFIX}{found["id"]}'
        row = saved.get(name)
        tools.append({'tool': name, 'label': found['name'], 'summary': found['when_to_use'], 'group': 'experts',
                      'mode': row['mode'] if row else 'on', 'usage': None, 'default_usage': '',
                      'subagent_allowed': False, 'available': True, 'reason': None, 'tab': None,
                      'resources': found['resources']})
    return {'revision': agent['revision'], 'expert': agent['expert'], 'tools': tools}


CAPABILITIES = ('code', 'documents', 'ssh')


def switch_on_defaults(conn, agent_id: str, old: dict[str, Any], new: dict[str, Any]) -> None:
    """Profile save. Fixed agents: a capability switched on gets all of its tools on (catalog.capability_tools),
    replacing earlier off rows (they had no effect while it was off). Per-run agents: when the agent becomes per run or
    a capability is switched on, every enabled capability's tools are switched on. Grounded answers always get
    report_not_covered. Switching a capability off, and a save that switches nothing on, keep every row; edited usage
    text is kept."""
    switched_on = [capability for capability in CAPABILITIES
                   if new[capability]['enabled'] and not old[capability]['enabled']]
    wanted: set[str] = set()
    if new['resources'] == 'per_run' and (old['resources'] != 'per_run' or switched_on):
        wanted.update(enabled_capability_tools(new))
    for capability in switched_on:
        wanted.update(capability_tools(capability, new))
    if new['documents']['enabled'] and new['documents']['grounded']:
        wanted.add('report_not_covered')
    for name in sorted(wanted):
        conn.execute(SWITCH_ON, (agent_id, name))


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
            if agent['revision'] != value.expected_revision:
                raise HTTPException(409, STALE)
            servers = {row['id']: {found['name'] for found in row['tools']} for row in conn.execute(SELECTED_MCP, (store.project(conn, app_name), agent_id))}
            experts = {row['id'] for row in conn.execute('SELECT id FROM ka.agents WHERE expert AND id<>%s', (agent_id,))}
            for choice in value.tools:
                if choice.tool in CATALOG:
                    continue
                if choice.tool.startswith(ROW_PREFIX):
                    if choice.tool.removeprefix(ROW_PREFIX) not in experts:
                        raise HTTPException(400, UNKNOWN_EXPERT.format(tool=choice.tool))
                    continue
                kind, _, rest = choice.tool.partition(':')
                server, _, name = rest.partition(':')
                if kind != 'mcp' or not name:
                    raise HTTPException(400, UNKNOWN.format(tool=choice.tool))
                if name not in servers.get(server, set()):
                    raise HTTPException(400, MCP_NOT_SELECTED.format(tool=choice.tool))
            chosen = {choice.tool: choice for choice in value.tools}
            switch = conn.execute("SELECT usage FROM ka.agent_tools WHERE agent_id=%s AND tool='run_subagent'",
                                  (agent_id,)).fetchone()
            if SUBAGENT in chosen and switch is None:
                raise HTTPException(400, SUBAGENTS_OFF)
            report = chosen.get('report_not_covered')
            if agent['documents_enabled'] and agent['documents_grounded'] and (report is None or report.mode != 'on'):
                raise HTTPException(400, GROUNDED)
            # Choices about agents that are not experts now survive the replace (they apply again if one returns).
            kept = [row['tool'] for row in conn.execute(
                "SELECT tool FROM ka.agent_tools WHERE agent_id=%s AND tool LIKE 'expert:%%'", (agent_id,))
                if row['tool'].removeprefix(ROW_PREFIX) not in experts]
            conn.execute('DELETE FROM ka.agent_tools WHERE agent_id=%s', (agent_id,))
            for tool in kept:
                conn.execute(INSERT_TOOL, (agent_id, tool, 'off', None, False))
            for choice in value.tools:
                if choice.tool.startswith(ROW_PREFIX):  # on: no row
                    if choice.mode == 'off':
                        conn.execute(INSERT_TOOL, (agent_id, choice.tool, 'off', None, False))
                    continue
                usage = _usage(choice.usage)
                if choice.tool == SUBAGENT:  # on while the switch is on, whatever the choice says
                    conn.execute(SUBAGENT_ROW, (agent_id, usage))
                    continue
                allowed = choice.subagent_allowed is not False
                if choice.tool.startswith('mcp:') and choice.mode == 'on' and usage is None and allowed:
                    continue  # an MCP tool's default: no row
                conn.execute(INSERT_TOOL, (agent_id, choice.tool, choice.mode, usage, allowed))
            if switch is not None and SUBAGENT not in chosen:
                conn.execute(SUBAGENT_ROW, (agent_id, switch['usage']))  # only the switch removes it
            actor = store.actor(conn, *request_actor(request))
            conn.execute('UPDATE ka.agents SET revision=revision+1,updated_by=%s WHERE id=%s', (actor, agent_id))
            conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
                VALUES (%s,%s,'agent.tools.updated','agent',%s,%s)''', (store.project(conn, app_name), actor,
                _uuid(agent_id), Jsonb({'agent_id': agent_id,
                                        'on': sorted(choice.tool for choice in value.tools if choice.mode == 'on')})))
            return agent_tools(conn, agent_id, store.project(conn, app_name))

    return router
