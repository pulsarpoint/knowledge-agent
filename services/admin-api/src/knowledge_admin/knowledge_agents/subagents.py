"""run_subagent: a native agent hands a separate part of its work to a helper (a subagent) that runs in parallel with
the tools and resources it is given and without the conversation. Subagents run only in native agent sessions (Test
and Generate documents), whose runner puts the turn's Subagents into the run context.

The checks and the subagent's configuration are pure: a deep copy of the parent's effective configuration with its
access object narrowed, the built-in prompt, no instructions, no skills and no experts. A subagent gets every tool but
run_subagent (its run has no Subagents host) and every tool of the MCP servers in its access; what a tool may do is
decided by its check against the subagent's access (the parent's, narrowed, content editor off).

Running (Subagents): one per native turn, in parallel up to AGENT_MAX_SUBAGENTS, each recorded in the turn's record
with parent_call_id and counted in the session's totals; Stop cancels them with the turn. Expert calls (ask_<name>,
Subagents.ask) run the same way and share the slots and the per-turn limit, but with the expert's own saved
configuration and credentials (experts.load)."""

import asyncio
import copy
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from agents.run_config import CallModelData, ModelInputData

from knowledge_admin import access as access_rules
from knowledge_admin.capabilities.context import TOOL_CALL_ID
from knowledge_admin.capabilities.subagents import check_run_subagent
from knowledge_admin.knowledge_agents import experts, native_sessions
from knowledge_admin.knowledge_agents.config import credential_references
from knowledge_admin.knowledge_agents.native_sessions import SessionEvents, failure
from knowledge_admin.sessions.run_access import picks
from knowledge_admin.knowledge_agents.session_worker import clean_text

logger = logging.getLogger(__name__)

MAX_TASK = 20_000
MAX_PER_TURN = 16
PROMPT = (Path(__file__).parent / 'prompts' / 'subagent.md').read_text(encoding='utf-8').strip()
UNAVAILABLE = 'Subagents run only in agent sessions.'
EMPTY_TASK = 'Give the subagent a task.'
LONG_TASK = 'A subagent task is at most 20,000 characters.'
LIMIT = 'This turn already started 16 helpers.'  # subagents and expert calls together
FAILED = 'Subagent failed: {message}'


def check_task(task: str) -> None:
    if not task.strip():
        raise ValueError(EMPTY_TASK)
    if len(task) > MAX_TASK:
        raise ValueError(LONG_TASK)


def subagent_config(config: Mapping[str, Any], resources: Mapping[str, list[str]] | None) -> dict[str, Any]:
    """The configuration a subagent runs with: the parent's with its access narrowed (subagent_access: what the
    access entries carry is all the tools reach). resources None: the parent's resources; otherwise exactly those."""
    sub = copy.deepcopy(dict(config))
    sub.pop('record', None)
    sub['experts'] = []  # a subagent never calls experts
    sub['access'] = subagent_access(config['access'], resources)
    sub['name'] = f'{config["name"]} · subagent'
    sub['system_prompt'] = {'content': PROMPT}
    sub['instructions'] = ''
    return sub


def subagent_access(parent: Mapping[str, Any], resources: Mapping[str, list[str]] | None) -> dict[str, Any]:
    """The parent's access narrowed (access.narrow, never widened) to what the subagent is given: the given resources
    (None: all of the parent's but its documents and skills, which a subagent never gets) and the parent's MCP servers;
    content editor off (a subagent never changes content; it has no inventory write tools either)."""
    if resources is None:
        picked = [entry for entry in parent['resources'] if entry['type'] not in ('document', 'skill', 'mcp_server')]
    else:
        picked = picks(resources)
    picked += [entry for entry in parent['resources'] if entry['type'] == 'mcp_server']
    return access_rules.narrow(dict(parent), picked, ['content_editor'])


def referenced_credentials(config: Mapping[str, Any], credentials: Mapping[str, str]) -> dict[str, str]:
    """The parent turn's already decrypted credentials that a subagent's configuration references; nothing else."""
    wanted = {str(reference['id']) for reference in credential_references(config) if reference is not None}
    return {key: value for key, value in credentials.items() if str(key) in wanted}


async def run_subagent(context: Any, task: str, resources: dict[str, list[str]] | None) -> str:
    """The catalog tool: the subagent's final answer, or a ValueError the adapter returns as a tool error."""
    host = context.get('subagents')
    if host is None:
        raise ValueError(UNAVAILABLE)
    return await host.run(context, task, resources, TOOL_CALL_ID.get() or '')


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

    def __init__(
        self,
        parent: SessionEvents,
        parent_call_id: str,
        *,
        secrets: list[str],
        record_usage: Callable[[dict[str, Any]], None] | None,
        host: 'Subagents | None' = None,
        agent_id: str | None = None,
    ):
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
    (knowledge_admin.agents.runner.agent) as its own task, with the parent's scratch workspace, no history, no turn or time
    limit, SubagentEvents hooks and `model_filter`. A subagent gets only the parent turn's credentials its
    configuration references; an expert gets its own configuration and credentials from `load_expert(entry, given
    resources, whether the calling run is a content editor)` (experts.load, run in a thread), whose secrets are added to the turn's `secrets` so the turn's record,
    its answer and the tool result redact them too. An expert whose configuration has run_subagent gets a nested
    Subagents of its own (`nested`): its own slots (a slot the expert holds never blocks its helpers), the turn's
    per-turn count (`root`), its events in the turn's record under the expert's agent id, and no experts. Cancelling
    the turn (Stop) cancels the tool call and with it the run; close() (the end of the turn) cancels any still running,
    the experts' subagents too, and empties their credentials."""

    def __init__(
        self,
        parent: SessionEvents,
        *,
        secrets: list[str],
        record_usage: Callable[[dict[str, Any]], None] | None,
        run_agent: Callable[..., Awaitable[Any]],
        limit: int | None = None,
        load_expert: Callable[
            [dict[str, Any], Mapping[str, list[str]] | None, bool], tuple[dict[str, Any], dict[str, str]]
        ]
        | None = None,
        root: 'Subagents | None' = None,
        agent_id: str | None = None,
    ):
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

    async def run(
        self,
        context: Mapping[str, Any],
        task: str,
        resources: Mapping[str, list[str]] | None,
        call_id: str,
    ) -> str:
        """One run_subagent call: the subagent's final answer (redacted), FAILED with the redacted reason when it fails,
        or a ValueError for a refused call (refused calls do not count towards MAX_PER_TURN)."""
        check_task(task)
        sub = subagent_config(context['config'], resources)
        if self.closed or self.parent.closed:
            raise asyncio.CancelledError
        if self.root.started >= MAX_PER_TURN:
            raise ValueError(LIMIT)
        self.root.started += 1  # no await since the check: calls arriving together cannot pass it twice
        credentials = referenced_credentials(sub, context['credentials'])
        self.issued.append(credentials)
        try:
            events = SubagentEvents(
                self.parent,
                call_id,
                secrets=self.secrets,
                record_usage=self.record_usage,
                host=self,
                agent_id=self.agent_id,
            )
            events.add('input', {'task': task, 'subagent': {'task': task}})
            return await self._drive(
                events, sub, task, credentials, context['workspace'], lambda message: FAILED.format(message=message)
            )
        finally:
            credentials.clear()

    async def ask(
        self,
        context: Mapping[str, Any],
        entry: Mapping[str, Any],
        task: str,
        resources: Mapping[str, list[str]] | None,
        call_id: str,
    ) -> str:
        """One expert call (ask_<name>): the expert's answer (redacted), experts.FAILED when its run fails, or a
        ValueError for a refused call (refused calls do not count towards MAX_PER_TURN). The given resources must be
        the caller's own; the expert runs its own saved configuration, its access narrowed to them (experts.load),
        and its own credentials. Shares the slots and the per-turn limit with run_subagent."""
        check_task(task)
        if resources is not None and (refusal := check_run_subagent(context['config']['access'], resources)):
            raise ValueError(refusal)
        if self.load_expert is None:
            raise ValueError(experts.UNAVAILABLE.format(name=entry['name']))
        if self.closed or self.parent.closed:
            raise asyncio.CancelledError
        if self.root.started >= MAX_PER_TURN:
            raise ValueError(LIMIT)
        self.root.started += 1  # before the load (an await): calls arriving together cannot pass the check twice
        editor = access_rules.is_content_editor(context['config']['access'])  # the expert's only if its caller's too
        load = asyncio.ensure_future(asyncio.to_thread(self.load_expert, dict(entry), resources, editor))
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
            events = SubagentEvents(
                self.parent,
                call_id,
                secrets=self.secrets,
                record_usage=self.record_usage,
                host=self,
                agent_id=str(entry['id']),
            )
            # The expert's own host for its subagents: recorded in the turn under the expert, closed with its call.
            nested = Subagents(
                events,
                secrets=self.secrets,
                record_usage=self.record_usage,
                run_agent=self.run_agent,
                limit=self.limit,
                root=self.root,
                agent_id=str(entry['id']),
            )
            self.nested.append(nested)
            events.add('input', {'task': task, 'expert': {'id': entry['id'], 'name': entry['name']}})
            return await self._drive(
                events,
                config,
                task,
                credentials,
                context['workspace'],
                lambda message: experts.FAILED.format(name=entry['name'], message=message),
                subagents=nested,
            )
        finally:
            if nested is not None:
                nested.forget()
            credentials.clear()

    async def _drive(
        self,
        events: SubagentEvents,
        config: dict[str, Any],
        task: str,
        credentials: dict[str, str],
        workspace: Any,
        failed: Callable[[str], str],
        subagents: 'Subagents | None' = None,
    ) -> str:
        """Run one started call in a slot: its answer (redacted), or `failed(redacted reason)` when its run fails.
        subagents: an expert's own host (a subagent starts no subagents)."""
        # The slot first, then the flush: calls take slots in the order they were made (a waiting call's input
        # event is written by the next flush of the turn).
        async with self.slots:
            if self.closed or self.parent.closed:  # the turn ended while this call waited for a slot
                raise asyncio.CancelledError
            await asyncio.to_thread(self.parent.recorder.flush)
            running = asyncio.create_task(
                self.run_agent(
                    config,
                    task,
                    credentials=credentials,
                    workspace=workspace,
                    hooks=events,
                    max_turns=None,
                    turn_timeout_seconds=None,
                    call_model_input_filter=self.model_filter,
                    stream=False,
                    subagents=subagents,
                )
            )
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
