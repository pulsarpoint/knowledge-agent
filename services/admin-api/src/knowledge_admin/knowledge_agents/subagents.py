"""run_subagent: a native agent hands a separate part of its work to a helper (a subagent) that runs in parallel with
the tools and resources it is given and without the conversation. Subagents run only in native agent sessions (Test
and Generate documents), whose runner puts the turn's Subagents into the run context.

The checks and the subagent's configuration are pure: a deep copy of the parent's effective configuration with only
the given tools on and the resource lists narrowed, the built-in prompt, no instructions and no skills.

Running (Subagents): one per native turn, in parallel up to AGENT_MAX_SUBAGENTS, each recorded in the turn's record
with parent_call_id and counted in the session's totals; Stop cancels them with the turn. Expert calls (ask_<name>,
Subagents.ask) run the same way and share the slots and the per-turn limit, but with the expert's own saved
configuration and credentials (experts.load)."""
import asyncio
import copy
import logging
import os
from collections.abc import Awaitable, Callable, Collection, Mapping
from pathlib import Path
from typing import Any

from agents.run_config import CallModelData, ModelInputData

from knowledge_admin.capabilities.context import TOOL_CALL_ID
from knowledge_admin.knowledge_agents import experts, native_sessions
from knowledge_admin.knowledge_agents.config import credential_references
from knowledge_admin.knowledge_agents.native_sessions import SessionEvents, failure
from knowledge_admin.knowledge_agents.run_resources import KINDS
from knowledge_admin.knowledge_agents.session_worker import clean_text
from knowledge_admin.tools.catalog import CATALOG, SUBAGENT, item, tools_of
from knowledge_admin.tools.sdk import NOT_OFFERED

logger = logging.getLogger(__name__)

MAX_TASK = 20_000
MAX_PER_TURN = 16
PROMPT = (Path(__file__).parent / 'prompts' / 'subagent.md').read_text(encoding='utf-8').strip()
UNAVAILABLE = 'Subagents run only in agent sessions.'
EMPTY_TASK = 'Give the subagent a task.'
LONG_TASK = 'A subagent task is at most 20,000 characters.'
NO_TOOLS = 'Give the subagent at least one tool.'
NO_SPAWN = 'Tool run_subagent cannot be given to a subagent.'
NOT_FOR_SUBAGENTS = 'Tool {name} may not be given to a subagent.'
NOT_GIVEN = '{label} {id} is not one of your resources.'
LIMIT = 'This turn already started 16 helpers.'  # subagents and expert calls together
FAILED = 'Subagent failed: {message}'


def check_task(task: str) -> None:
    if not task.strip():
        raise ValueError(EMPTY_TASK)
    if len(task) > MAX_TASK:
        raise ValueError(LONG_TASK)


def _listed(config: Mapping[str, Any], capability: str) -> dict[str, dict[str, Any]]:
    """The parent's current resources of one kind, by id; none for a kind it does not use."""
    if not config[capability]['enabled']:
        return {}
    items = {'code': lambda: config['code']['projects'], 'documents': lambda: config['documents'].get('collections', []),
             'ssh': lambda: config['ssh']['servers'], 'services': lambda: config['services']['items']}[capability]()
    return {str(entry['id']): entry for entry in items if 'id' in entry}


def _given_tools(config: Mapping[str, Any], tools: list[str], offered: Collection[str],
                 mcp_tools: Mapping[str, str]) -> tuple[set[str], dict[str, set[str]]]:
    """The given catalog tools, and the given MCP tools by server id. Each name must be offered to this run (MCP
    tools by the name the model sees), allowed for subagents, and not run_subagent."""
    if not tools:
        raise ValueError(NO_TOOLS)
    rows = {entry['tool']: entry for entry in tools_of(config)['items']}
    catalog: set[str] = set()
    mcp: dict[str, set[str]] = {}
    expert_tools = {entry['tool'] for entry in config.get('experts', [])}
    for name in dict.fromkeys(tools):
        if name == SUBAGENT:
            raise ValueError(NO_SPAWN)
        if name in expert_tools:
            raise ValueError(NOT_FOR_SUBAGENTS.format(name=name))
        if name in offered:
            row_name = name
        elif name in mcp_tools:
            row_name = mcp_tools[name]
        else:
            raise ValueError(NOT_OFFERED.format(name=name))
        if not rows.get(row_name, {}).get('subagent_allowed', True):
            raise ValueError(NOT_FOR_SUBAGENTS.format(name=name))
        if row_name.startswith('mcp:'):
            _, server, tool = row_name.split(':', 2)
            mcp.setdefault(server, set()).add(tool)
        else:
            catalog.add(row_name)
    return catalog, mcp


def check_given(config: Mapping[str, Any], resources: Mapping[str, list[str]]) -> dict[str, list[str]]:
    """The given resource ids of every kind (each once, in the caller's order), each one of the caller's current
    resources; otherwise a ValueError naming it (NOT_GIVEN). Checked before a subagent narrows to them and before an
    expert is given them."""
    given: dict[str, list[str]] = {}
    for kind in KINDS:
        listed = _listed(config, kind.capability)
        ids = list(dict.fromkeys(str(entry) for entry in resources.get(kind.key) or []))
        for entry in ids:
            if entry not in listed:
                raise ValueError(NOT_GIVEN.format(label=kind.label, id=entry))
        given[kind.key] = ids
    return given


def _narrow(sub: dict[str, Any], resources: Mapping[str, list[str]]) -> None:
    """Narrow each resource list to `resources`, as run_resources.resolve narrows a per-run agent's: every id must be
    in the parent's current list; an empty list gives nothing of that kind, never everything.

    Kept separate from run_resources.resolve on purpose: resolve checks a per-run agent's request against the
    database (allowed and still available), while a subagent may only narrow its parent's already resolved lists,
    without reading the database. Keep the two rules in step."""
    given = check_given(sub, resources)
    chosen = {kind.key: [_listed(sub, kind.capability)[entry] for entry in given[kind.key]] for kind in KINDS}
    code_ids = set(given['code_project_ids'])
    sub['code'] = {**sub['code'], 'all_projects': False, 'projects': chosen['code_project_ids'],
                   'graphs': [graph for graph in sub['code'].get('graphs', []) if str(graph.get('id')) in code_ids]}
    documents = {key: value for key, value in sub['documents'].items() if key not in ('folders', 'all_folders')}
    sub['documents'] = {**documents, 'all_collections': False, 'collections': chosen['collection_ids']}
    sub['ssh'] = {**sub['ssh'], 'servers': chosen['server_ids']}
    sub['services'] = {**sub['services'], 'items': chosen['service_ids']}
    sub['resources'], sub['run_resources'] = 'per_run', given


def _only(sub: dict[str, Any], catalog: set[str], mcp: dict[str, set[str]], mcp_tools: Mapping[str, str]) -> None:
    """Only the given tools on: catalog tools by name; MCP servers kept only for given MCP tools, every other tool of
    those servers off (from the snapshot's tool lists, the run's live names and the parent's own mcp:<server>:* rows,
    so a tool switched off for the parent stays off for the subagent). The kept servers offer only the tools switched
    on (an allowlist: a tool a server adds later stays out). Without ssh_run, only the SSH servers that host a kept MCP
    server stay (agent() requires their keys), so a subagent gets no other SSH private key."""
    rows = {entry['tool']: entry for entry in tools_of(sub)['items']}
    items = [item(name, 'on' if name in catalog else 'off', rows.get(name, {}).get('usage')) for name in CATALOG]

    def keep(servers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [server for server in servers if str(server['id']) in mcp]
    sub['mcp'] = {**sub['mcp'], 'enabled': bool(mcp) and sub['mcp']['enabled'], 'servers': keep(sub['mcp']['servers']),
                  'allowlist': True}
    for server in sub['ssh']['servers']:
        server['mcp_servers'] = keep(server.get('mcp_servers', []))
    if 'ssh_run' not in catalog:
        hosts = [server for server in sub['ssh']['servers'] if server['mcp_servers'] and sub['mcp']['enabled']]
        sub['ssh'] = {**sub['ssh'], 'servers': hosts}
        if 'run_resources' in sub:
            sub['run_resources'] = {**sub['run_resources'], 'server_ids': [str(server['id']) for server in hosts]}
    kept = [*sub['mcp']['servers'], *(found for server in sub['ssh']['servers'] for found in server['mcp_servers'])]
    for server_id, given in sorted(mcp.items()):
        listed = {tool['name'] for server in kept if str(server['id']) == server_id for tool in server.get('tools', [])}
        listed |= {name.split(':', 2)[2] for name in [*mcp_tools.values(), *rows] if name.startswith(f'mcp:{server_id}:')}
        for tool in sorted(listed):
            name = f'mcp:{server_id}:{tool}'
            items.append(item(name, 'on' if tool in given else 'off', rows.get(name, {}).get('usage'), group='mcp'))
    sub['tools'] = {'items': items}


def subagent_config(config: Mapping[str, Any], tools: list[str], resources: Mapping[str, list[str]] | None, *,
                    offered: Collection[str], mcp_tools: Mapping[str, str]) -> dict[str, Any]:
    """The configuration a subagent runs with, or a ValueError naming the tool or resource that cannot be given.
    `offered`: the catalog tools of the parent's run (context['tools']); `mcp_tools`: its MCP tools as the model sees
    them (context['mcp_tools']). resources None: the parent's lists and mode; otherwise a per-run run of exactly those."""
    catalog, mcp = _given_tools(config, tools, offered, mcp_tools)
    sub = copy.deepcopy(dict(config))
    sub.pop('record', None)
    sub.pop('revision_run', None)
    sub.pop('document_access', None)  # the document tools are never given to a subagent
    sub['experts'] = []  # a subagent never calls experts
    if resources is not None:
        _narrow(sub, resources)
    _only(sub, catalog, mcp, mcp_tools)
    sub['name'] = f"{config['name']} · subagent"
    sub['system_prompt'] = {'content': PROMPT}
    sub['instructions'] = ''
    sub['skills'] = {'enabled': False, 'items': []}
    return sub


def referenced_credentials(config: Mapping[str, Any], credentials: Mapping[str, str]) -> dict[str, str]:
    """The parent turn's already decrypted credentials that a subagent's configuration references; nothing else."""
    wanted = {str(reference['id']) for reference in credential_references(config) if reference is not None}
    return {key: value for key, value in credentials.items() if str(key) in wanted}


async def run_subagent(context: Any, task: str, tools: list[str], resources: dict[str, list[str]] | None) -> str:
    """The catalog tool: the subagent's final answer, or a ValueError the adapter returns as a tool error."""
    host = context.get('subagents')
    if host is None:
        raise ValueError(UNAVAILABLE)
    return await host.run(context, task, tools, resources, TOOL_CALL_ID.get() or '')


def _abandon(task: asyncio.Task) -> None:
    """A subagent's task nobody awaits any more: cancelled, kept referenced until it ends, and its outcome retrieved
    then (native_sessions._retrieve), so an exception it raises later is never logged with an unredacted traceback."""
    task.cancel()
    native_sessions._leave(task)


def _discard_loaded(load: asyncio.Future) -> None:
    """An expert's load that finished after its call was cancelled: the credentials it decrypted are emptied (and its
    error, if any, retrieved, so it is never logged)."""
    if not load.cancelled() and load.exception() is None:
        load.result()[1].clear()


class SubagentEvents(SessionEvents):
    """A subagent's or an expert's SDK lifecycle as events of its parent's turn record: the kinds SessionEvents writes,
    each with parent_call_id (the run_subagent or ask_<name> call) and, for an expert, the expert's agent_id. It is
    closed exactly while the parent's events are (Stop, Close, a lost lease, the end of the turn) or its Subagents are
    (close(), which runs before the parent's events close), so a subagent that outlives its turn, or ignores its
    cancellation while close() waits, starts no tool call and records nothing more."""

    def __init__(self, parent: SessionEvents, parent_call_id: str, *, secrets: list[str],
                 record_usage: Callable[[dict[str, Any]], None] | None, host: 'Subagents | None' = None,
                 agent_id: str | None = None):
        super().__init__(parent.recorder, secrets=secrets, first=True, record_usage=record_usage)
        self.parent, self.parent_call_id, self.host, self.agent_id = parent, parent_call_id, host, agent_id

    @property
    def closed(self) -> bool:
        return self.parent.closed or (self.host is not None and self.host.closed)

    @closed.setter
    def closed(self, value: bool) -> None:
        pass  # follows the parent's events

    def add(self, kind: str, payload: dict[str, Any]) -> None:
        self.recorder.add(kind, {**payload, 'parent_call_id': self.parent_call_id}, agent_id=self.agent_id)


class Subagents:
    """The subagents and expert calls of one native session turn (NativeRunner.run_turn makes one; agent() puts it in
    the run context). At most MAX_PER_TURN of them together are started; `limit` run at once (AGENT_MAX_SUBAGENTS,
    default 4, at least 1) and later calls wait for a slot. Each runs through `run_agent`
    (knowledge_admin.agents.agent) as its own task, with the parent's scratch workspace, no history, no turn or time
    limit, SubagentEvents hooks and `model_filter`. A subagent gets only the parent turn's credentials its
    configuration references; an expert gets its own configuration and credentials from `load_expert(entry, given
    resources)` (experts.load, run in a thread), whose secrets are added to the turn's `secrets` so the turn's record,
    its answer and the tool result redact them too. An expert whose configuration has run_subagent gets a nested
    Subagents of its own (`nested`): its own slots (a slot the expert holds never blocks its helpers), the turn's
    per-turn count (`root`), its events in the turn's record under the expert's agent id, and no experts. Cancelling
    the turn (Stop) cancels the tool call and with it the run; close() (the end of the turn) cancels any still running,
    the experts' subagents too, and empties their credentials."""

    def __init__(self, parent: SessionEvents, *, secrets: list[str],
                 record_usage: Callable[[dict[str, Any]], None] | None, run_agent: Callable[..., Awaitable[Any]],
                 limit: int | None = None,
                 load_expert: Callable[[dict[str, Any], Mapping[str, list[str]] | None],
                                       tuple[dict[str, Any], dict[str, str]]] | None = None,
                 root: 'Subagents | None' = None, agent_id: str | None = None):
        self.parent, self.secrets, self.record_usage, self.run_agent = parent, secrets, record_usage, run_agent
        self.load_expert = load_expert
        self.root = root or self  # the turn's host: it counts every start of the turn (MAX_PER_TURN)
        self.agent_id = agent_id  # an expert's helpers: their events belong to the expert
        self.nested: list[Subagents] = []  # the experts' own hosts (closed with this one)
        self.limit = limit or max(1, int(os.environ.get('AGENT_MAX_SUBAGENTS', '4')))
        self.slots = asyncio.Semaphore(self.limit)
        self.started = 0
        self.closed = False
        self.running: set[asyncio.Task] = set()
        self.issued: list[dict[str, str]] = []  # credential maps handed to subagents and experts, emptied by close()

    async def model_filter(self, data: CallModelData) -> ModelInputData:
        """Like the parent's comments filter for a closed turn: no new model request once the turn has ended (one
        already sent cannot be recalled). No comments are delivered to subagents."""
        if self.closed or self.parent.closed:
            raise asyncio.CancelledError
        return data.model_data

    async def run(self, context: Mapping[str, Any], task: str, tools: list[str],
                  resources: Mapping[str, list[str]] | None, call_id: str) -> str:
        """One run_subagent call: the subagent's final answer (redacted), FAILED with the redacted reason when it fails,
        or a ValueError for a refused call (refused calls do not count towards MAX_PER_TURN)."""
        check_task(task)
        sub = subagent_config(context['config'], tools, resources, offered=context['tools'],
                              mcp_tools=context.get('mcp_tools', {}))
        if self.closed or self.parent.closed:
            raise asyncio.CancelledError
        if self.root.started >= MAX_PER_TURN:
            raise ValueError(LIMIT)
        self.root.started += 1  # no await since the check: calls arriving together cannot pass it twice
        credentials = referenced_credentials(sub, context['credentials'])
        self.issued.append(credentials)
        try:
            events = SubagentEvents(self.parent, call_id, secrets=self.secrets, record_usage=self.record_usage,
                                    host=self, agent_id=self.agent_id)
            events.add('input', {'task': task, 'subagent': {'task': task}})
            return await self._drive(events, sub, task, credentials, context['workspace'],
                                     lambda message: FAILED.format(message=message))
        finally:
            credentials.clear()

    async def ask(self, context: Mapping[str, Any], entry: Mapping[str, Any], task: str,
                  resources: Mapping[str, list[str]] | None, call_id: str) -> str:
        """One expert call (ask_<name>): the expert's answer (redacted), experts.FAILED when its run fails, or a
        ValueError for a refused call (refused calls do not count towards MAX_PER_TURN). The given resources must be
        the caller's own; the expert runs its own saved configuration (resolved with them when it is per run) and
        its own credentials. Shares the slots and the per-turn limit with run_subagent."""
        check_task(task)
        given = check_given(context['config'], resources) if resources is not None else None
        if self.load_expert is None:
            raise ValueError(experts.UNAVAILABLE.format(name=entry['name']))
        if self.closed or self.parent.closed:
            raise asyncio.CancelledError
        if self.root.started >= MAX_PER_TURN:
            raise ValueError(LIMIT)
        self.root.started += 1  # before the load (an await): calls arriving together cannot pass the check twice
        load = asyncio.ensure_future(asyncio.to_thread(self.load_expert, dict(entry), given))
        try:
            config, credentials = await asyncio.shield(load)
        except asyncio.CancelledError:
            self.root.started -= 1  # a refused call does not count
            load.add_done_callback(_discard_loaded)  # Stop: a load still running empties what it returns
            raise
        except ValueError:
            self.root.started -= 1  # a refused call does not count
            raise
        except Exception as exc:  # the database or a key: the call fails like a failed run
            return experts.FAILED.format(name=entry['name'], message=failure(exc, self.secrets))
        self.issued.append(credentials)
        nested: Subagents | None = None
        try:
            if self.closed or self.parent.closed:  # the turn ended while the expert loaded
                raise asyncio.CancelledError
            # The turn's own list, extended in place: the turn's recorder, its events, its answer and its history
            # redact the expert's secrets too.
            self.secrets[:] = list(dict.fromkeys([*self.secrets, *(value for value in credentials.values() if value)]))
            events = SubagentEvents(self.parent, call_id, secrets=self.secrets, record_usage=self.record_usage,
                                    host=self, agent_id=str(entry['id']))
            # The expert's own host for its subagents: recorded in the turn under the expert, closed with its call.
            nested = Subagents(events, secrets=self.secrets, record_usage=self.record_usage, run_agent=self.run_agent,
                               limit=self.limit, root=self.root, agent_id=str(entry['id']))
            self.nested.append(nested)
            events.add('input', {'task': task, 'expert': {'id': entry['id'], 'name': entry['name']}})
            return await self._drive(events, config, task, credentials, context['workspace'],
                                     lambda message: experts.FAILED.format(name=entry['name'], message=message),
                                     subagents=nested)
        finally:
            if nested is not None:
                nested.forget()
            credentials.clear()

    async def _drive(self, events: SubagentEvents, config: dict[str, Any], task: str, credentials: dict[str, str],
                     workspace: Any, failed: Callable[[str], str], subagents: 'Subagents | None' = None) -> str:
        """Run one started call in a slot: its answer (redacted), or `failed(redacted reason)` when its run fails.
        subagents: an expert's own host (a subagent starts no subagents)."""
        # The slot first, then the flush: calls take slots in the order they were made (a waiting call's input
        # event is written by the next flush of the turn).
        async with self.slots:
            if self.closed or self.parent.closed:  # the turn ended while this call waited for a slot
                raise asyncio.CancelledError
            await asyncio.to_thread(self.parent.recorder.flush)
            running = asyncio.create_task(self.run_agent(
                config, task, credentials=credentials, workspace=workspace, hooks=events, max_turns=None,
                turn_timeout_seconds=None, call_model_input_filter=self.model_filter, stream=False,
                subagents=subagents))
            self.running.add(running)
            running.add_done_callback(self.running.discard)
            try:
                # Not `await running`: a cancelled call (Stop) returns at once even when the run ignores its
                # cancellation; close() then waits for it at most STOP_GRACE_SECONDS.
                await asyncio.wait({running})
            except asyncio.CancelledError:
                _abandon(running)
                raise
            try:
                result = running.result()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = failure(exc, self.secrets)
                events.add('error', {'type': type(exc).__name__, 'message': message})
                await asyncio.to_thread(self.parent.recorder.flush)
                return failed(message)
        return clean_text(events.draft(result.final_output), self.secrets)

    async def close(self, deadline: float | None = None) -> None:
        """The turn ends (Stop, Close, a lost lease or its answer): no more subagents start, running ones are cancelled
        and waited for at most STOP_GRACE_SECONDS, and never past `deadline` (loop time: the end of the grace that
        started at Stop, so Stop takes about STOP_GRACE_SECONDS in all); one still running then is left to end by
        itself, recording nothing. Every credential map handed to a subagent is emptied before the wait: a subagent that ignores
        its cancellation starts no tool or model call meanwhile (SubagentEvents and model_filter follow `closed`)
        and has no credentials for a new connection."""
        self.closed = True
        running = set(self.running).union(*(nested.running for nested in self.nested))
        for task in running:
            task.cancel()
        for credentials in self.issued:
            credentials.clear()
        for nested in self.nested:
            nested.forget()
        if running:
            grace = native_sessions.STOP_GRACE_SECONDS
            if deadline is not None:
                grace = min(grace, max(0.0, deadline - asyncio.get_running_loop().time()))
            if grace > 0:
                await asyncio.wait(running, timeout=grace)
            if any(not task.done() for task in running):
                logger.warning('A subagent did not end within the grace period of its turn; it is left to end')
            for task in running:
                _abandon(task)
        self.forget()

    def forget(self) -> None:
        """At once, without waiting: no more subagents start, running ones are cancelled and every credential map
        handed to a subagent is emptied (also run when the turn itself is cancelled), the experts' subagents too."""
        self.closed = True
        for task in list(self.running):
            task.cancel()
        for credentials in self.issued:
            credentials.clear()
        for nested in self.nested:
            nested.forget()
