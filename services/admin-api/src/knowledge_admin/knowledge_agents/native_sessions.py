"""Native agents in Generate documents sessions: the session's working folders as the agent's code roots, a turn's SDK
lifecycle as the session's run events, and agent-session-worker, which runs native turns with the agent config pinned
when the session started through the native runtime (knowledge_admin.agents.agent) and the SDK Session HistorySession.
Turns have no time or model-call limit, like Claude Code turns: they end with the answer, Stop, Close or a lost lease.
Messages sent while a turn runs reach its next model call. Credentials are decrypted here, per turn, and redacted from
events, the draft, status messages and history."""
import asyncio
import base64
import copy
import json
import logging
import os
import signal
import socket
from collections.abc import Iterable
from contextlib import suppress
from typing import Any

from agents import RunHooks
from agents.run_config import CallModelData, ModelInputData
from cryptography.exceptions import InvalidTag

from knowledge_admin import agent_tracing, run_record
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


def code_roots(config: dict[str, Any], folders: list[dict[str, Any]]) -> dict[str, Any]:
    """The config a native session runs with: the agent's own config plus every working folder of the session that is
    not already one of its code project folders, as code roots grouped by code project. With such folders Code projects
    is on; the agent's own projects stay in only when its Code projects switch was on (a switched-off agent's saved
    selections stay out). Credentials stay references; nothing is decrypted here."""
    pinned = copy.deepcopy(config)
    own = pinned['code']['projects'] if pinned['code']['enabled'] else []
    known = {path for project in own for path in project['folder_paths']}
    added: dict[str, dict[str, Any]] = {}
    for folder in folders:
        if folder['path'] in known:
            continue
        known.add(folder['path'])
        project = added.setdefault(str(folder['code_project_id']), {
            'id': str(folder['code_project_id']), 'name': folder['name'], 'description': 'Chosen for this session.',
            'folder_paths': []})
        project['folder_paths'].append(folder['path'])
    if added:
        pinned['code'] = {**pinned['code'], 'enabled': True, 'all_projects': False, 'projects': [*own, *added.values()]}
    return pinned


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
    return ''


def _arguments(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


class SessionEvents(RunHooks):
    """A native turn's SDK lifecycle as the run events of a Claude Code turn: instructions (first turn), reasoning,
    message, tool_call with a summary, tool_result with a preview, and one usage event for the turn (finish). Every
    hook flushes, so the session page follows the turn live. The recorder redacts every event with `secrets`; tool
    output is also cleaned before it is capped, so no partial key survives the cut."""

    def __init__(self, recorder, *, secrets: list[str], first: bool):
        self.recorder, self.secrets, self.first = recorder, secrets, first
        self.texts: list[str] = []
        self.calls = 0
        self.usage = {'input_tokens': 0, 'output_tokens': 0, 'cached_tokens': 0, 'total_tokens': 0, 'cost_usd': 0.0}

    async def _flush(self) -> None:
        await asyncio.to_thread(self.recorder.flush)

    async def on_agent_start(self, context, agent) -> None:
        if not self.first:
            return
        self.first = False
        text = agent.instructions if isinstance(agent.instructions, str) else '(generated at run time)'
        self.recorder.add('instructions', {'agent': agent.name, 'text': text})
        await self._flush()

    async def on_llm_end(self, context, agent, response) -> None:
        for item in response.output:
            kind = getattr(item, 'type', None)
            if kind == 'reasoning':
                parts = [part.text for part in (getattr(item, 'summary', None) or [])]
                parts += [part.text for part in (getattr(item, 'content', None) or [])]
                if any(part.strip() for part in parts):
                    self.recorder.add('reasoning', {'text': '\n'.join(parts)})
            elif kind == 'message':
                text = ''.join(getattr(part, 'text', '') for part in item.content)
                if text.strip():
                    self.texts.append(text)
                    self.recorder.add('message', {'text': text})
            elif kind == 'function_call':
                arguments = _arguments(item.arguments)
                self.recorder.add('tool_call', {'name': item.name, 'call_id': item.call_id, 'arguments': arguments,
                                                'summary': summary(item.name, arguments)})
        usage = response.usage
        details = getattr(usage, 'input_tokens_details', None)
        self.usage['input_tokens'] += int(usage.input_tokens or 0)
        self.usage['output_tokens'] += int(usage.output_tokens or 0)
        self.usage['cached_tokens'] += int(getattr(details, 'cached_tokens', 0) or 0)
        self.usage['total_tokens'] += int(usage.total_tokens or 0)
        self.calls += 1
        await self._flush()

    async def on_tool_end(self, context, agent, tool, result) -> None:
        output = clean_text(str(result), self.secrets)
        self.recorder.add('tool_result', {
            'name': tool.name, 'call_id': getattr(context, 'tool_call_id', None) or '',
            'output': capped(output, MAX_TOOL_OUTPUT),
            'preview': capped('\n'.join(output.splitlines()[:PREVIEW_LINES]), MAX_PREVIEW_CHARS),
            'is_error': tool_failed(output)})
        await self._flush()

    def finish(self) -> None:
        """The turn's one usage event (sums of its model calls); none when no model call finished."""
        if self.calls:
            self.recorder.add('usage', dict(self.usage))

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


async def _cancel(task: asyncio.Task) -> None:
    """Cancel a turn and wait until agent() has closed its model and MCP connections."""
    task.cancel()
    await asyncio.wait({task})
    if not task.cancelled():
        task.exception()  # retrieved: a stopped turn's error does not matter


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

    def _record(self, conn, inputs: list[dict]) -> None:
        for item in inputs:
            self.recorder.add('input', {'task': item['text'], 'comment': True})
        self.recorder.flush_in(conn)
        self.ids.extend(item['id'] for item in inputs)

    async def __call__(self, data: CallModelData) -> ModelInputData:
        model = data.model_data
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

    def __init__(self, store: RepositoryStore, session: dict, run_agent=agent, *, poll: float = 1.0,
                 idle_release: float = IDLE_RELEASE_SECONDS, slot: int = 1):
        super().__init__(store, session, poll=poll, idle_release=idle_release, slot=slot)
        self.run_agent = run_agent

    def load(self) -> tuple[dict, dict[str, str], HistorySession]:
        """The pinned config, its credentials (decrypted now, for this turn) and the saved history. load_credentials
        refuses a disabled agent, model, SSH server or MCP server."""
        with self.store.connect() as conn:
            config = conn.execute('SELECT agent_config FROM ka.agent_sessions_cc WHERE id=%s',
                                  (self.id,)).fetchone()['agent_config']
            history = HistorySession.load(conn, self.id)
        config.pop('record', None)  # the turn's events are recorded here, not by agent()
        return config, load_credentials(self.store, config), history

    async def run_turn(self, number: int, text: str) -> None:
        recorder = RunRecorder(run_key=run_key(self.id, number), project_id=str(self.session['project_id']),
                               agent_id=self.session['agent_id'], actor_id=str(self.session['actor_id']), secrets=self.secrets)
        recorder.add('start', {'kind': 'native_session', 'title': self.session['title'], 'agent': self.session['agent_name'],
                               'turn': number})
        recorder.add('input', {'task': text})
        await asyncio.to_thread(recorder.flush)
        events = SessionEvents(recorder, secrets=self.secrets, first=number == 1)
        credentials: dict[str, str] = {}
        history: HistorySession | None = None
        comments: Comments | None = None
        status, message, final = 'failed', '', None
        try:
            try:
                config, credentials, history = await asyncio.to_thread(self.load)
                self.secrets[:] = list(dict.fromkeys(value for value in credentials.values() if value))
                comments = Comments(self, number, recorder, history)
                status, final = await self.drive(config, text, credentials, history, events, comments)
                message = STOPPED if status == 'stopped' else ''
            except Exception as exc:
                status, message = 'failed', failure(exc, self.secrets)
                recorder.add('error', {'type': type(exc).__name__, 'message': message})
                logger.warning('Agent session %s turn %s failed (%s)', self.id, number, type(exc).__name__)
            if comments is not None:
                await comments.settle()
            events.finish()
            recorder.add('end', {'status': END_STATUS[status]})
            await asyncio.to_thread(recorder.flush)
            if status != 'waiting' and comments is not None:
                await asyncio.to_thread(self.return_comments, number, comments.ids)
            commit = None if history is None else (lambda conn: history.commit(conn, self.secrets))
            await asyncio.to_thread(self.finish_turn, number, status, message, usage=events.usage, commit=commit,
                                    draft=clean_text(events.draft(final), self.secrets))
        finally:
            if history is not None:
                history.drop()
            credentials.clear()
            self.secrets.clear()

    async def drive(self, config: dict, text: str, credentials: dict[str, str], history: HistorySession,
                    events: SessionEvents, comments: Comments) -> tuple[str, Any]:
        """Run the turn without time or model-call limits: ('waiting', final answer) or ('stopped', None). Stop, Close or
        a lost lease cancels it wherever it is (connecting, in a model request, in a tool call); a runner that is itself
        cancelled (the worker stops) cancels it too."""
        turn = asyncio.create_task(self.run_agent(
            config, text, credentials=credentials, session=history, hooks=events, max_turns=None,
            turn_timeout_seconds=None, call_model_input_filter=comments, stream=False))
        try:
            while not turn.done():
                await asyncio.wait({turn}, timeout=self.poll)
                if not turn.done() and await asyncio.to_thread(self.stop_requested):
                    await _cancel(turn)
                    return 'stopped', None
            return 'waiting', turn.result().final_output  # raises the turn's error
        finally:
            if not turn.done():
                await _cancel(turn)


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
