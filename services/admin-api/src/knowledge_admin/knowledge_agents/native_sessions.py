"""Native agents in Generate documents sessions: a turn's SDK lifecycle as the session's run events, and
agent-session-worker, which runs native turns with the agent config pinned when the session started (built by
claude_code_api.create_session) through the native runtime (knowledge_admin.agents.agent) and the SDK Session HistorySession.
Turns have no time or model-call limit, like Claude Code turns: they end with the answer, Stop, Close or a lost lease.
Messages sent while a turn runs reach its next model call. Credentials are decrypted here, per turn, and redacted from
events, the draft, status messages and history."""
import asyncio
import base64
import json
import logging
import os
import signal
import socket
from collections.abc import Callable, Iterable
from contextlib import suppress
from typing import Any

from agents import RunHooks
from agents.run_config import CallModelData, ModelInputData
from cryptography.exceptions import InvalidTag

from knowledge_admin import agent_tracing, grounding, run_record
from knowledge_admin.agent_activity import tool_failed
from knowledge_admin.agents import agent, load_credentials
from knowledge_admin.knowledge_agents import session_worker as shared
from knowledge_admin.knowledge_agents.history import HistorySession
from knowledge_admin.knowledge_agents.session_worker import (END_STATUS, IDLE_RELEASE_SECONDS, MAX_PREVIEW_CHARS,
                                                             MAX_TOOL_OUTPUT, PREVIEW_LINES, STOPPED, capped, clean_text,
                                                             run_key)
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.run_record import RunRecorder

logger = logging.getLogger(__name__)

UNREADABLE = 'A configured credential could not be decrypted. Save it again in administration.'
COMMENT = 'Comment from the user while you work: '
STOP_GRACE_SECONDS = 30.0  # as claude_code_worker.INTERRUPT_GRACE_SECONDS
_abandoned: set[asyncio.Task] = set()  # turns that ignored cancellation, kept referenced until they end


def summary(name: str, arguments: Any) -> str:
    """One line for the session page, like Claude Code's Read/Grep/Glob/Bash summaries."""
    if not isinstance(arguments, dict):
        return ''

    def value(key: str) -> str:
        return str(arguments.get(key) or '')
    if name in ('read_file', 'list_files'):
        return value('path')
    if name == 'search_files':
        return f"{value('query')} in {value('path')}"
    if name == 'code_search':
        return f"{value('name')} in {value('path')}"
    if name == 'ast_grep':
        return f"{value('pattern') or value('rule')} in {value('path')}"
    if name == 'shell_run':
        return value('command')
    if name == 'ssh_run':
        return f"{value('server_id')}: {value('command')}"
    if name == 'search_documents':
        return value('query')
    if name == 'read_document':
        return f"{value('collection')}/{value('path')}"
    if name == 'code_graph':
        return f"{value('action')} {value('query') or value('node_id')}".strip()
    if name == 'run_subagent':
        return value('task')
    return ''


def _arguments(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


class SessionEvents(RunHooks):
    """A native turn's SDK lifecycle as the run events of a Claude Code turn: instructions (first turn), reasoning,
    message, tool_call with a summary, tool_result with a preview, and a usage event for each model call. Every hook
    flushes, so the session page follows the turn live; after a model call, `record_usage(usage)` (the runner) writes
    the events together with the call's tokens on the session, so its totals rise with every call. `usage` sums the
    turn's calls. The recorder redacts every event with `secrets`; tool output is also cleaned before it is capped, so
    no partial key survives the cut."""

    def __init__(self, recorder, *, secrets: list[str], first: bool,
                 record_usage: Callable[[dict[str, Any]], None] | None = None):
        self.recorder, self.secrets, self.first, self.record_usage = recorder, secrets, first, record_usage
        self.texts: list[str] = []
        self.closed = False  # the turn ended: a turn that outlived Stop records nothing more and starts no tool call
        self.usage = {'input_tokens': 0, 'output_tokens': 0, 'cached_tokens': 0, 'reasoning_tokens': 0,
                      'total_tokens': 0, 'cost_usd': 0.0}

    async def _flush(self) -> None:
        await asyncio.to_thread(self.recorder.flush)

    def add(self, kind: str, payload: dict[str, Any]) -> None:
        """One event of this turn (a subagent's SubagentEvents marks its own with the run_subagent call id)."""
        self.recorder.add(kind, payload)

    async def on_agent_start(self, context, agent) -> None:
        if self.closed or not self.first:
            return
        self.first = False
        text = agent.instructions if isinstance(agent.instructions, str) else '(generated at run time)'
        self.add('instructions', {'agent': agent.name, 'text': text})
        await self._flush()

    async def on_llm_end(self, context, agent, response) -> None:
        if self.closed:
            return
        for item in response.output:
            kind = getattr(item, 'type', None)
            if kind == 'reasoning':
                parts = [part.text for part in (getattr(item, 'summary', None) or [])]
                parts += [part.text for part in (getattr(item, 'content', None) or [])]
                if any(part.strip() for part in parts):
                    self.add('reasoning', {'text': '\n'.join(parts)})
            elif kind == 'message':
                text = ''.join(getattr(part, 'text', '') for part in item.content)
                if text.strip():
                    self.texts.append(text)
                    self.add('message', {'text': text})
            elif kind == 'function_call':
                arguments = _arguments(item.arguments)
                self.add('tool_call', {'name': item.name, 'call_id': item.call_id, 'arguments': arguments,
                                       'summary': summary(item.name, arguments)})
        usage = response.usage
        details = getattr(usage, 'input_tokens_details', None)
        output_details = getattr(usage, 'output_tokens_details', None)
        call = {'input_tokens': int(usage.input_tokens or 0), 'output_tokens': int(usage.output_tokens or 0),
                'cached_tokens': int(getattr(details, 'cached_tokens', 0) or 0),
                'reasoning_tokens': int(getattr(output_details, 'reasoning_tokens', 0) or 0),
                'total_tokens': int(usage.total_tokens or 0), 'cost_usd': 0.0}
        for key, value in call.items():
            self.usage[key] += value
        self.add('usage', call)
        if self.record_usage is None:
            await self._flush()
        else:
            await asyncio.to_thread(self.record_usage, call)

    async def on_tool_start(self, context, agent, tool) -> None:
        # The SDK calls this before the tool runs: a turn that outlived Stop starts no new tool call (shell_run,
        # ssh_run, MCP, file and code tools). A call already running at Stop cannot be recalled.
        if self.closed:
            raise asyncio.CancelledError

    async def on_tool_end(self, context, agent, tool, result) -> None:
        if self.closed:
            return
        output = clean_text(str(result), self.secrets)
        self.add('tool_result', {
            'name': tool.name, 'call_id': getattr(context, 'tool_call_id', None) or '',
            'output': capped(output, MAX_TOOL_OUTPUT),
            'preview': capped('\n'.join(output.splitlines()[:PREVIEW_LINES]), MAX_PREVIEW_CHARS),
            'is_error': tool_failed(output)})
        await self._flush()

    def draft(self, final_output: Any) -> str:
        """The final answer, or the last message when the answer is empty."""
        final = '' if final_output is None else str(final_output)
        return final if final.strip() else (self.texts[-1] if self.texts else '')


def failure(exc: BaseException, secrets: Iterable[str]) -> str:
    """A failed turn's message for people: one line, at most 500 characters, without credentials."""
    if isinstance(exc, InvalidTag):
        return UNREADABLE
    if isinstance(exc, ValueError):  # configuration problems: a missing folder, a disabled agent, model or server
        reason = ' '.join(clean_text(str(exc), secrets).split())[:500]
        if reason:
            return reason
    return f'Agent execution failed ({type(exc).__name__}). Check the model connection and agent resources.'


def grounding_of(result: Any, answer: str) -> dict[str, Any] | None:
    """The cited sources and coverage of a grounded turn, from its run context's grounding state (the documents tools
    keep it there), or None for an ungrounded turn."""
    context = getattr(getattr(result, 'context_wrapper', None), 'context', None)
    return grounding.result(answer, context.get('grounding') if isinstance(context, dict) else None)


def _retrieve(task: asyncio.Task) -> None:
    _abandoned.discard(task)
    if not task.cancelled():
        task.exception()  # retrieved: a stopped turn's error does not matter


async def _cancel(task: asyncio.Task, session_id) -> None:
    """Cancel a turn and wait until agent() has closed its model and MCP connections, at most STOP_GRACE_SECONDS: Stop
    always ends the turn. A turn still running then is left to end by itself; its history is dropped and finish_turn
    commits none for a stopped turn, so it cannot change the session."""
    task.cancel()
    await asyncio.wait({task}, timeout=STOP_GRACE_SECONDS)
    if task.done():
        _retrieve(task)
        return
    logger.warning('Agent session %s turn did not end within %ss of Stop; it is left to end', session_id,
                   STOP_GRACE_SECONDS)
    _abandoned.add(task)
    task.add_done_callback(_retrieve)


class Comments:
    """The call_model_input_filter of one native turn: before each model call, the messages queued since the last one
    are delivered to this turn (SessionRunner.queued_comments, with their input events in the same transaction) and
    reach the model as user messages, in place for the turn's later model calls too, and the turn's buffered history.
    The delivery runs to completion even when Stop cancels the turn, so `ids` always names what was delivered."""

    def __init__(self, runner: shared.SessionRunner, number: int, recorder: RunRecorder, history: HistorySession):
        self.runner, self.number, self.recorder, self.history = runner, number, recorder, history
        self.placed: list[tuple[int, dict]] = []  # (position in the SDK's input, comment item)
        self.ids: list[int] = []
        self.delivery: asyncio.Task | None = None
        self.closed = False  # the turn ended: nothing more is delivered to it

    def _record(self, conn, inputs: list[dict]) -> None:
        # Runs in a thread while the turn waits in this filter, so no hook adds events meanwhile; once the turn has
        # ended (closed, settle() done), a turn that outlived Stop no longer delivers or records anything.
        for item in inputs:
            self.recorder.add('input', {'task': item['text'], 'comment': True})
        self.recorder.flush_in(conn)
        self.ids.extend(item['id'] for item in inputs)

    async def __call__(self, data: CallModelData) -> ModelInputData:
        model = data.model_data
        if self.closed:  # a turn that outlived Stop starts no new model request (one already sent cannot be recalled)
            raise asyncio.CancelledError
        # _record flushes inside queued_comments' transaction without holding the recorder's lock until the commit
        # (add_usage does: recorder lock first, then store.connect(); keep that order if this ever takes the lock). It
        # cannot race a subagent's flush: this filter runs only after every tool call of the previous model response
        # has returned, and subagents run only inside run_subagent tool calls (Subagents.close() ends them with the turn).
        self.delivery = asyncio.ensure_future(asyncio.to_thread(self.runner.queued_comments, self.number, self._record))
        texts = await asyncio.shield(self.delivery)
        items = list(model.input)
        for text in texts:
            item = {'role': 'user', 'content': COMMENT + text}
            self.placed.append((len(items), item))
            await self.history.add_items([item])
        merged, start = [], 0
        for position, item in self.placed:  # the SDK's input only grows during a turn: earlier positions hold
            merged += items[start:position]
            merged.append(item)
            start = position
        model.input = merged + items[start:]
        return model

    async def settle(self) -> None:
        """Wait for a delivery that Stop interrupted."""
        if self.delivery is not None:
            with suppress(Exception):
                await self.delivery


class NativeRunner(shared.SessionRunner):
    """One native session in one asyncio task. Each turn loads the pinned config, decrypts its credentials, loads the
    history, runs the native runtime as a task (cancelled on Stop, Close or a lost lease, wherever it is), then records
    the outcome with the history commit in one transaction. Credentials are cleared after every turn."""

    def __init__(self, store: RepositoryStore, session: dict, run_agent=agent, *, run_subagent=None, poll: float = 1.0,
                 idle_release: float = IDLE_RELEASE_SECONDS, slot: int = 1):
        super().__init__(store, session, poll=poll, idle_release=idle_release, slot=slot)
        self.run_agent = run_agent
        self.run_subagent = run_subagent or run_agent  # how subagents run (tests pass a stand-in)
        self.stop_deadline: float | None = None  # loop time when the grace that started at this turn's Stop ends

    def load(self) -> tuple[dict, dict[str, str], HistorySession]:
        """The pinned config, its credentials (decrypted now, for this turn) and the saved history. load_credentials
        refuses a disabled agent, model, SSH server or MCP server."""
        with self.store.connect() as conn:
            config = conn.execute('SELECT agent_config FROM ka.agent_sessions_cc WHERE id=%s',
                                  (self.id,)).fetchone()['agent_config']
            history = HistorySession.load(conn, self.id)
        config.pop('record', None)  # the turn's events are recorded here, not by agent()
        return config, load_credentials(self.store, config), history

    def add_usage(self, recorder: RunRecorder, usage: dict[str, Any]) -> None:
        """One model call: its events and its tokens on the session, in one transaction, while this worker holds the
        session (as finish_turn), so the session page's totals rise during the turn. The recorder's lock is held until
        the commit, so no other flush (a parallel subagent's) commits later events first and a live view following
        event ids skips none."""
        with recorder._lock, self.store.connect() as conn:
            recorder.flush_in(conn)
            conn.execute('''UPDATE ka.agent_sessions_cc SET input_tokens=input_tokens+%(input)s,
                output_tokens=output_tokens+%(output)s, cached_tokens=cached_tokens+%(cached)s,
                reasoning_tokens=reasoning_tokens+%(reasoning)s, model_calls=model_calls+1,
                cost_usd=cost_usd+%(cost)s::numeric, last_activity_at=now() WHERE id=%(id)s AND worker_id=%(worker)s''',
                {'input': usage['input_tokens'], 'output': usage['output_tokens'], 'cached': usage['cached_tokens'],
                 'reasoning': usage['reasoning_tokens'], 'cost': usage['cost_usd'], 'id': self.id,
                 'worker': self.worker_id})

    async def run_turn(self, number: int, text: str) -> None:
        # This turn's own list: a turn that outlived Stop keeps redacting with it after the worker forgets it.
        secrets: list[str] = []
        self.secrets = secrets
        self.stop_deadline = None
        recorder = RunRecorder(run_key=run_key(self.id, number), project_id=str(self.session['project_id']),
                               agent_id=self.session['agent_id'], actor_id=str(self.session['actor_id']), secrets=secrets)
        kind = 'test_session' if self.session.get('purpose') == 'test' else 'native_session'
        recorder.add('start', {'kind': kind, 'title': self.session['title'], 'agent': self.session['agent_name'],
                               'turn': number})
        recorder.add('input', {'task': text})
        await asyncio.to_thread(recorder.flush)
        events = SessionEvents(recorder, secrets=secrets, first=number == 1,
                               record_usage=lambda usage: self.add_usage(recorder, usage))
        credentials: dict[str, str] = {}
        history: HistorySession | None = None
        comments: Comments | None = None
        subagents = None
        status, message, result = 'failed', '', None
        try:
            try:
                # Imported here: the subagent runtime builds on this module's SessionEvents.
                from knowledge_admin.knowledge_agents.subagents import Subagents
                subagents = Subagents(events, secrets=secrets, record_usage=events.record_usage,
                                      run_agent=self.run_subagent)
                config, credentials, history = await asyncio.to_thread(self.load)
                secrets[:] = list(dict.fromkeys(value for value in credentials.values() if value))
                comments = Comments(self, number, recorder, history)
                status, result = await self.drive(config, text, credentials, history, events, comments, subagents)
                message = STOPPED if status == 'stopped' else ''
            except Exception as exc:
                status, message = 'failed', failure(exc, secrets)
                recorder.add('error', {'type': type(exc).__name__, 'message': message})
                logger.warning('Agent session %s turn %s failed (%s)', self.id, number, type(exc).__name__)
            if subagents is not None:
                # None outlives the turn with credentials or records events after it; after Stop, close() waits only
                # for what is left of the turn's grace, so Stop takes about STOP_GRACE_SECONDS in all.
                await subagents.close(self.stop_deadline)
            events.closed = True
            credentials.clear()  # a turn that outlived Stop cannot use them for new connections (such as ssh_run)
            if comments is not None:
                comments.closed = True
                await comments.settle()
            answer = clean_text(events.draft(None if result is None else result.final_output), secrets)
            end = {'status': END_STATUS[status]}
            sources = None
            if status == 'waiting':
                try:
                    sources = grounding_of(result, answer)
                except Exception as exc:  # the turn still ends with its answer; only the cited sources are lost
                    logger.warning('Agent session %s turn %s grounding failed (%s)', self.id, number,
                                   type(exc).__name__)
            if sources is not None:
                end['grounding'] = sources  # the recorder redacts it with the turn's secrets
            recorder.add('end', end)
            recorder.close()  # no event after 'end'
            await asyncio.to_thread(recorder.flush)
            if status != 'waiting' and comments is not None:
                await asyncio.to_thread(self.return_comments, number, comments.ids)
            # finish_turn commits only while this worker holds the lease: when it was lost after the turn completed,
            # the comments delivered inside the turn stay delivered and its history is not saved. A session closed
            # during the turn is committed (harmless: a closed session takes no more turns).
            commit = None if history is None else (lambda conn: history.commit(conn, secrets))
            # usage=None: every model call already added its tokens (add_usage)
            await asyncio.to_thread(self.finish_turn, number, status, message, usage=None, commit=commit,
                                    draft=answer)
        finally:
            if history is not None:
                history.drop()
            if subagents is not None:
                subagents.forget()
            credentials.clear()
            self.secrets = []  # the worker forgets them; the turn's own list stays with what may still use it

    async def drive(self, config: dict, text: str, credentials: dict[str, str], history: HistorySession,
                    events: SessionEvents, comments: Comments, subagents: Any) -> tuple[str, Any]:
        """Run the turn without time or model-call limits: ('waiting', the run result) or ('stopped', None). Stop, Close or
        a lost lease cancels it wherever it is (connecting, in a model request, in a tool call); a runner that is itself
        cancelled (the worker stops) cancels it too."""
        turn = asyncio.create_task(self.run_agent(
            config, text, credentials=credentials, session=history, hooks=events, max_turns=None,
            turn_timeout_seconds=None, call_model_input_filter=comments, stream=False, subagents=subagents))
        try:
            while not turn.done():
                await asyncio.wait({turn}, timeout=self.poll)
                if not turn.done() and await asyncio.to_thread(self.stop_requested):
                    self.stop_deadline = asyncio.get_running_loop().time() + STOP_GRACE_SECONDS
                    await _cancel(turn, self.id)
                    return 'stopped', None
            return 'waiting', turn.result()  # raises the turn's error
        finally:
            if not turn.done() and self.stop_deadline is None:  # after Stop, _cancel has already waited its grace
                await _cancel(turn, self.id)


async def serve(store: RepositoryStore, run_agent=agent, *, stopped: asyncio.Event, worker_id: str | None = None,
                limit: int | None = None, poll: float = 1.0, idle_release: float = IDLE_RELEASE_SECONDS) -> None:
    """Run native sessions (session_worker.serve); AGENT_SESSION_MAX_SESSIONS of them at once (default 4)."""
    worker_id = worker_id or f'{socket.gethostname()}:{os.getpid()}'
    limit = limit or int(os.environ.get('AGENT_SESSION_MAX_SESSIONS', '4'))
    await shared.serve(
        store, lambda session, slot: NativeRunner(store, session, run_agent, poll=poll, idle_release=idle_release, slot=slot),
        runtime='openai', label='Agent session', stopped=stopped, worker_id=worker_id, limit=limit, poll=poll)


def main() -> None:
    """agent-session-worker: PostgreSQL from the PG* environment, run records like admin-api, and Agents SDK tracing to
    Phoenix (DOCS_WIKI_PHOENIX_ENDPOINT) as for native runs in admin-api."""
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s %(message)s')
    store = RepositoryStore('', base64.urlsafe_b64decode(os.environ['CREDENTIAL_ENCRYPTION_KEY']), os.environ['AUTH_ISSUER_URL'])
    run_record.configure(store, None)
    agent_tracing.setup()

    async def run() -> None:
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, stopped.set)
        await serve(store, stopped=stopped)

    asyncio.run(run())
