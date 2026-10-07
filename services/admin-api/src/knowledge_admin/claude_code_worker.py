"""Claude Code worker: runs interactive documentation sessions with the Claude Agent SDK, one client per session.

The model key is decrypted here and passed only in the CLI process environment. Nothing imports claude_agent_sdk at
module import time; messages are recognised by class name, so tests drive the worker with same-named fakes."""
import asyncio
import base64
import logging
import os
import signal
import socket
import time
from collections import deque
from collections.abc import Iterable
from contextlib import AsyncExitStack, suppress
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from cryptography.exceptions import InvalidTag
from knowledge_llm import RuntimeModel, claude_model, decode_model
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from knowledge_admin import run_record
from knowledge_admin.agent_activity import redact
from knowledge_admin.claude_code import ALLOWED_TOOLS, DISALLOWED_TOOLS, close_idle
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.run_record import RunRecorder

logger = logging.getLogger(__name__)

PREVIEW_LINES = 20
MAX_PREVIEW_CHARS = 2000
MAX_TOOL_OUTPUT = 64 * 1024
TOOL_TRUNCATED = '… [truncated]'
BASE_ENV = {'HOME': '/home/claude', 'TMPDIR': '/home/claude/tmp', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'DISABLE_AUTOUPDATER': '1', 'DISABLE_TELEMETRY': '1', 'DISABLE_ERROR_REPORTING': '1',
            'CLAUDE_CODE_SUBPROCESS_ENV_SCRUB': '1', 'CLAUDE_CODE_DISABLE_AUTO_MEMORY': '1', 'SHELL': '/bin/bash'}
HINTS = (
    (('inference profile', 'on-demand throughput'),
     'Bedrock needs an inference profile ID for this model, such as eu.anthropic.claude-sonnet-4-5-20250929-v1:0. '
     'Change the model ID in LLM Settings.'),
    (('403', '401', 'accessdenied', 'authentication'), 'Check the key and model access of this connection in LLM Settings.'),
)
DEFAULT_HINT = 'Check the model connection in LLM Settings, then send a message to retry.'
LEASE_SECONDS = 60
IDLE_RELEASE_SECONDS = 300.0
INTERRUPT_GRACE_SECONDS = 30.0
STOPPED = 'Stopped. Send a message to continue.'
INTERRUPTED = 'The worker restarted during this turn. Send a message to continue.'
END_STATUS = {'waiting': 'completed', 'stopped': 'stopped', 'failed': 'failed'}
CLOSED = object()
LOST = object()  # another worker holds the session now: stop without further writes


def credential_env(model: RuntimeModel) -> dict[str, str]:
    """The CLI environment variables that carry this connection's key; nothing else ever receives the key."""
    if not claude_model(model.provider, model.api_format, model.model):
        raise ValueError('The session model is not a Claude connection. Start a new session with a Claude model.')
    if not model.api_key:
        raise ValueError('The model connection has no key. Save its key in LLM Settings.')
    models = {'ANTHROPIC_MODEL': model.model, 'ANTHROPIC_DEFAULT_HAIKU_MODEL': model.model,
              'ANTHROPIC_SMALL_FAST_MODEL': model.model}  # background calls use the chosen model, never another one
    if model.provider == 'bedrock':
        if not model.aws_region:
            raise ValueError('Set the AWS region of this connection in LLM Settings.')
        return {'CLAUDE_CODE_USE_BEDROCK': '1', 'AWS_REGION': model.aws_region,
                'AWS_BEARER_TOKEN_BEDROCK': model.api_key, **models}
    env = {'ANTHROPIC_API_KEY': model.api_key, **models}
    if model.base_url:
        base = model.base_url.rstrip('/')
        env['ANTHROPIC_BASE_URL'] = base[:-3] if base.endswith('/v1') else base
    return env


def build_options(session: dict[str, Any], model: RuntimeModel) -> dict[str, Any]:
    """Keyword arguments for ClaudeAgentOptions: fixed read-only tools plus Bash, the chosen folders, no limits."""
    folders = [folder['path'] for folder in session['context_snapshot']['folders']]
    options = {
        'system_prompt': {'type': 'preset', 'preset': 'claude_code', 'append': session['prompt_snapshot']['system_prompt']},
        'tools': list(ALLOWED_TOOLS), 'allowed_tools': list(ALLOWED_TOOLS), 'disallowed_tools': list(DISALLOWED_TOOLS),
        'permission_mode': 'dontAsk', 'setting_sources': [], 'skills': [], 'mcp_servers': {}, 'strict_mcp_config': True,
        'cwd': folders[0], 'add_dirs': folders, 'model': model.model, 'env': {**BASE_ENV, **credential_env(model)},
        'resume': session['claude_session_id'],
    }
    if os.environ.get('CLAUDE_CODE_CLI'):
        options['cli_path'] = os.environ['CLAUDE_CODE_CLI']
    return options


def clean_text(text: str, secrets: Iterable[str]) -> str:
    """Text to store: without U+0000 (Postgres rejects it in text and jsonb), removed first so it cannot split a key,
    then redacted."""
    return redact(text.replace('\x00', ''), [secret for secret in secrets if secret])


def safe_error(text: str, secrets: Iterable[str]) -> str:
    """A provider or CLI error for people: redacted, one line, at most 500 characters, with a hint."""
    reason = ' '.join(clean_text(text or '', secrets).split())[:500]
    reason = reason or 'Claude Code stopped with an error.'
    lowered = reason.lower()
    hint = next((hint for needles, hint in HINTS if any(needle in lowered for needle in needles)), DEFAULT_HINT)
    return f'{reason} {hint}'


def result_text(content: Any) -> str:
    if content is None:
        return ''
    if isinstance(content, str):
        return content
    return '\n'.join(item.get('text', '') for item in content if isinstance(item, dict) and item.get('type') == 'text')


def capped(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + TOOL_TRUNCATED


def tool_summary(name: str, arguments: dict[str, Any]) -> str:
    if name == 'Read':
        return str(arguments.get('file_path', ''))
    if name in ('Grep', 'Glob'):
        pattern, path = str(arguments.get('pattern', '')), arguments.get('path')
        return f'{pattern} in {path}' if path else pattern
    if name == 'Bash':
        return str(arguments.get('command', ''))
    return ''


def usage_payload(result: Any) -> dict[str, Any]:
    usage = result.usage or {}
    cached = int(usage.get('cache_read_input_tokens') or 0)
    prompt = int(usage.get('input_tokens') or 0) + cached + int(usage.get('cache_creation_input_tokens') or 0)
    output = int(usage.get('output_tokens') or 0)
    return {'input_tokens': prompt, 'output_tokens': output, 'cached_tokens': cached, 'total_tokens': prompt + output,
            'cost_usd': float(result.total_cost_usd or 0)}


def result_error(result: Any) -> str:
    if result is None:
        return 'Claude Code ended the turn without a result.'
    return ' '.join(part for part in [result.result or '', *(getattr(result, 'errors', None) or [])] if part)


def _scrub(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, str):
        return clean_text(value, secrets)
    if isinstance(value, dict):
        return {_scrub(key, secrets) if isinstance(key, str) else key: _scrub(item, secrets) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_scrub(item, secrets) for item in value]
    return value


class _Redacting:
    """Removes the connection keys and U+0000 from every event: Bash runs in the CLI environment, so `env` could print
    the key, and a binary file can print NUL bytes."""

    def __init__(self, recorder, secrets: list[str]):
        self.recorder, self.secrets = recorder, secrets

    def add(self, kind: str, payload: dict) -> None:
        self.recorder.add(kind, _scrub(payload, self.secrets))


class Turn:
    """One turn's SDK messages as run record events, plus its draft, usage and CLI session id."""

    def __init__(self, recorder, *, secrets: Iterable[str]):
        self.recorder = _Redacting(recorder, [secret for secret in secrets if secret])
        self.tools: dict[str, str] = {}
        self.texts: list[str] = []
        self.result = None
        self.usage: dict[str, Any] | None = None
        self.session_id: str | None = None

    def add(self, message: Any) -> None:
        kind = type(message).__name__
        if kind == 'SystemMessage' and message.subtype == 'init':
            self.session_id = message.data.get('session_id') or self.session_id
        elif kind == 'AssistantMessage':
            for block in message.content:
                self._block(block)
            if getattr(message, 'error', None):
                self.recorder.add('error', {'message': f'The model reported an error: {message.error}'})
        elif kind == 'UserMessage' and isinstance(message.content, list):
            for block in message.content:
                if type(block).__name__ == 'ToolResultBlock':
                    output = _scrub(result_text(block.content), self.recorder.secrets)  # before capping: no partial key
                    preview = capped('\n'.join(output.splitlines()[:PREVIEW_LINES]), MAX_PREVIEW_CHARS)
                    self.recorder.add('tool_result', {
                        'name': self.tools.get(block.tool_use_id, ''), 'call_id': block.tool_use_id,
                        'output': capped(output, MAX_TOOL_OUTPUT), 'preview': preview, 'is_error': bool(block.is_error)})
        elif kind == 'ResultMessage':
            self.result = message
            self.session_id = message.session_id or self.session_id
            self.usage = usage_payload(message)
            self.recorder.add('usage', self.usage)

    def _block(self, block: Any) -> None:
        name = type(block).__name__
        if name == 'TextBlock' and block.text.strip():
            self.texts.append(block.text)
            self.recorder.add('message', {'text': block.text})
        elif name == 'ThinkingBlock' and block.thinking.strip():
            self.recorder.add('reasoning', {'text': block.thinking})
        elif name == 'ToolUseBlock':
            self.tools[block.id] = block.name
            self.recorder.add('tool_call', {'name': block.name, 'call_id': block.id, 'arguments': block.input,
                                            'summary': tool_summary(block.name, block.input)})

    @property
    def draft(self) -> str:
        final = (self.result.result if self.result is not None else None) or ''
        return final if final.strip() else (self.texts[-1] if self.texts else '')


def sdk_client(options: dict[str, Any]):
    """The real client. The SDK is imported only here, when a session starts its first turn."""
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
    return ClaudeSDKClient(options=ClaudeAgentOptions(**options))


def claim(store: RepositoryStore, worker_id: str, active: list[UUID], free: int) -> list[dict]:
    """Close idle sessions, renew this worker's leases, then claim sessions that need a worker: a queued message, or a
    turn left `working` by a worker that stopped (that turn is recorded as failed)."""
    with store.connect() as conn:
        close_idle(conn)
        conn.execute("""UPDATE ka.agent_sessions_cc SET lease_expires_at=now()+%s*interval '1 second'
            WHERE worker_id=%s AND id=ANY(%s::uuid[]) AND status<>'closed'""", (LEASE_SECONDS, worker_id, active))
        if free <= 0:
            return []
        rows = conn.execute("""WITH picked AS (
                SELECT s.id,s.status AS previous FROM ka.agent_sessions_cc s
                WHERE s.status<>'closed' AND (s.lease_expires_at IS NULL OR s.lease_expires_at<now())
                  AND NOT s.id=ANY(%s::uuid[])
                  AND (s.status='working' OR EXISTS (SELECT 1 FROM ka.agent_session_inputs i
                       WHERE i.session_id=s.id AND i.status='queued'))
                ORDER BY s.created_at,s.id LIMIT %s FOR UPDATE OF s SKIP LOCKED)
            UPDATE ka.agent_sessions_cc s SET worker_id=%s,lease_expires_at=now()+%s*interval '1 second',
                status=CASE WHEN s.status='working' THEN 'failed' ELSE s.status END,
                status_message=CASE WHEN s.status='working' THEN %s ELSE s.status_message END,
                stop_requested_at=CASE WHEN s.status='working' THEN NULL ELSE s.stop_requested_at END
            FROM picked WHERE s.id=picked.id
            RETURNING s.*,picked.previous,(SELECT a.name FROM ka.agents a WHERE a.id=s.agent_id) AS agent_name,
                EXISTS (SELECT 1 FROM ka.agent_session_inputs i WHERE i.session_id=s.id AND i.status='queued') AS queued""",
            (active, free, worker_id, LEASE_SECONDS, INTERRUPTED)).fetchall()
        for row in rows:
            if row['previous'] == 'working' and not row['queued']:  # failed now; it needs a worker only for a message
                conn.execute('UPDATE ka.agent_sessions_cc SET worker_id=NULL,lease_expires_at=NULL WHERE id=%s AND worker_id=%s',
                             (row['id'], worker_id))
            if row['previous'] == 'working' and row['turns'] > 0:
                key = f"cc:{row['id']}:{row['turns']}"
                for kind, payload in (('error', {'message': INTERRUPTED}), ('end', {'status': 'failed'})):
                    conn.execute("""INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,actor_id,payload)
                        SELECT %s,%s,coalesce(max(seq),0)+1,%s,%s,%s,%s FROM ka.agent_run_events WHERE run_key=%s""",
                        (row['project_id'], key, kind, row['agent_id'], row['actor_id'], Jsonb(payload), key))
    return rows


class SessionRunner:
    """One session in one asyncio task: one client, created on the first turn and kept until the session closes or has
    been idle for `idle_release` seconds. This task connects, sends and interrupts; a child task reads responses."""

    def __init__(self, store: RepositoryStore, session: dict, client_factory=sdk_client, *, poll: float = 1.0,
                 idle_release: float = IDLE_RELEASE_SECONDS):
        self.store, self.session, self.client_factory = store, session, client_factory
        self.id, self.worker_id = session['id'], session['worker_id']
        self.poll, self.idle_release = poll, idle_release
        self.secrets: list[str] = []
        self.stack: AsyncExitStack | None = None
        self.client = None
        self.stderr: deque[str] = deque(maxlen=20)

    async def run(self) -> None:
        idle_since = time.monotonic()
        try:
            while True:
                turn = await asyncio.to_thread(self.next_turn)
                if turn is CLOSED or turn is LOST:
                    return
                if turn is None:
                    if time.monotonic() - idle_since >= self.idle_release:
                        return
                    await asyncio.sleep(self.poll)
                    continue
                await self.run_turn(*turn)
                idle_since = time.monotonic()
        finally:
            await self.disconnect()
            self.secrets.clear()
            with suppress(psycopg.Error):
                await asyncio.to_thread(self.release)

    def next_turn(self):
        """Deliver every queued message as the next turn, once; CLOSED when the session is closed, LOST when this worker
        no longer holds its lease."""
        with self.store.connect() as conn:
            row = conn.execute('''SELECT status,turns,(worker_id=%s AND lease_expires_at>now()) AS owned
                FROM ka.agent_sessions_cc WHERE id=%s FOR UPDATE''', (self.worker_id, self.id)).fetchone()
            if row is None or row['status'] == 'closed':
                return CLOSED
            if not row['owned']:
                return LOST
            inputs = conn.execute("""SELECT id,text FROM ka.agent_session_inputs WHERE session_id=%s AND status='queued'
                ORDER BY seq""", (self.id,)).fetchall()
            if not inputs:
                return None
            number = row['turns'] + 1
            conn.execute("""UPDATE ka.agent_session_inputs SET status='delivered',turn=%s,delivered_at=now()
                WHERE id=ANY(%s)""", (number, [item['id'] for item in inputs]))
            conn.execute("""UPDATE ka.agent_sessions_cc SET status='working',status_message='',turns=%s,
                stop_requested_at=NULL,last_activity_at=now() WHERE id=%s""", (number, self.id))
            return number, '\n\n'.join(item['text'] for item in inputs)

    async def run_turn(self, number: int, text: str) -> None:
        self.stderr.clear()
        recorder = RunRecorder(run_key=f'cc:{self.id}:{number}', project_id=str(self.session['project_id']),
                               agent_id=self.session['agent_id'], actor_id=str(self.session['actor_id']), secrets=self.secrets)
        recorder.add('start', {'kind': 'claude_code', 'title': self.session['title'], 'agent': self.session['agent_name'],
                               'turn': number})
        if number == 1:
            recorder.add('instructions', {'agent': self.session['agent_name'],
                                          'text': self.session['prompt_snapshot']['system_prompt']})
        recorder.add('input', {'task': text})
        await asyncio.to_thread(recorder.flush)
        turn = Turn(recorder, secrets=self.secrets)
        status, message, consumer = 'failed', '', None
        try:
            await self.connect()
            turn = Turn(recorder, secrets=self.secrets)  # the key is known once connected: Turn redacts it from every event
            await self.client.query(text)
            consumer = asyncio.create_task(self.consume(turn, recorder))
            interrupted_at = None
            while not consumer.done():
                await asyncio.wait({consumer}, timeout=self.poll)
                if consumer.done():
                    break
                if interrupted_at is None:
                    if await asyncio.to_thread(self.stop_requested):
                        interrupted_at = time.monotonic()
                        await self.client.interrupt()
                elif time.monotonic() - interrupted_at > INTERRUPT_GRACE_SECONDS:
                    consumer.cancel()
                    with suppress(asyncio.CancelledError):
                        await consumer
                    await self.disconnect()
            if interrupted_at is not None:
                if not consumer.cancelled():
                    consumer.exception()  # retrieved: a stopped turn's error does not matter
                status, message = 'stopped', STOPPED
            else:
                consumer.result()  # raises the SDK error of this turn
                if turn.result is None or turn.result.is_error:
                    message = safe_error(result_error(turn.result), self.secrets)
                    recorder.add('error', {'message': message})
                else:
                    status = 'waiting'
        except Exception as exc:
            status = 'failed'
            message = safe_error(f"{type(exc).__name__}: {exc} {' '.join(self.stderr)}", self.secrets)
            recorder.add('error', {'type': type(exc).__name__, 'message': message})
            logger.warning('Claude Code session %s turn %s failed (%s)', self.id, number, type(exc).__name__)
            await self.disconnect()
        finally:
            if consumer is not None and not consumer.done():
                consumer.cancel()
        recorder.add('end', {'status': END_STATUS[status]})
        await asyncio.to_thread(recorder.flush)
        await asyncio.to_thread(self.finish_turn, number, status, message, turn)

    async def consume(self, turn: Turn, recorder: RunRecorder) -> None:
        async for message in self.client.receive_response():
            turn.add(message)
            await asyncio.to_thread(recorder.flush)

    async def connect(self) -> None:
        if self.client is not None:
            return
        model, session = await asyncio.to_thread(self.load)
        self.secrets[:] = [model.api_key] if model.api_key else []
        missing = next((f['path'] for f in session['context_snapshot']['folders'] if not Path(f['path']).is_dir()), None)
        if missing is not None:
            raise ValueError(f'The folder {missing} no longer exists. Start a new session without it.')
        options = build_options(session, model)
        options['stderr'] = self.stderr.append
        stack = AsyncExitStack()
        try:
            self.client = await stack.enter_async_context(self.client_factory(options))
        except BaseException:
            await stack.aclose()
            raise
        self.stack = stack

    def load(self) -> tuple[RuntimeModel, dict]:
        with self.store.connect() as conn:
            row = conn.execute('SELECT * FROM ka.llm_models WHERE id=%s', (self.session['model_id'],)).fetchone()
            session = conn.execute('''SELECT context_snapshot,prompt_snapshot,claude_session_id FROM ka.agent_sessions_cc
                WHERE id=%s''', (self.id,)).fetchone()
        if row is None or not row['enabled']:
            raise ValueError('The session model is disabled or was removed. Enable it in LLM Settings.')
        try:
            return decode_model(row, self.store.cipher), session
        except InvalidTag:
            raise ValueError('The model key could not be decrypted. Save it again in LLM Settings.') from None

    async def disconnect(self) -> None:
        stack, self.stack, self.client = self.stack, None, None
        if stack is not None:
            with suppress(Exception):
                await stack.aclose()

    def stop_requested(self) -> bool:
        with self.store.connect() as conn:
            row = conn.execute('''SELECT status,stop_requested_at,(worker_id=%s AND lease_expires_at>now()) AS owned
                FROM ka.agent_sessions_cc WHERE id=%s''', (self.worker_id, self.id)).fetchone()
        return row is None or row['status'] == 'closed' or row['stop_requested_at'] is not None or not row['owned']

    def finish_turn(self, number: int, status: str, message: str, turn: Turn) -> None:
        usage = turn.usage or {'input_tokens': 0, 'output_tokens': 0, 'cached_tokens': 0, 'cost_usd': 0.0}
        draft = clean_text(turn.draft, self.secrets)
        draft = draft if status == 'waiting' and draft.strip() else None
        with self.store.connect() as conn:
            conn.execute("""UPDATE ka.agent_sessions_cc SET
                status=CASE WHEN status='closed' THEN status ELSE %(status)s END,
                status_message=CASE WHEN status='closed' THEN status_message ELSE %(message)s END,
                draft=coalesce(%(draft)s::text, draft),
                draft_turn=CASE WHEN %(draft)s::text IS NULL THEN draft_turn ELSE %(turn)s END,
                claude_session_id=coalesce(%(cli)s::text, claude_session_id),
                input_tokens=input_tokens+%(input)s, output_tokens=output_tokens+%(output)s,
                cached_tokens=cached_tokens+%(cached)s, cost_usd=cost_usd+%(cost)s::numeric,
                stop_requested_at=NULL, last_activity_at=now() WHERE id=%(id)s AND worker_id=%(worker)s""",
                {'status': status, 'message': message, 'draft': draft, 'turn': number, 'cli': turn.session_id,
                 'input': usage['input_tokens'], 'output': usage['output_tokens'], 'cached': usage['cached_tokens'],
                 'cost': usage['cost_usd'], 'id': self.id, 'worker': self.worker_id})

    def release(self) -> None:
        with self.store.connect() as conn:
            conn.execute('UPDATE ka.agent_sessions_cc SET worker_id=NULL,lease_expires_at=NULL WHERE id=%s AND worker_id=%s',
                         (self.id, self.worker_id))


async def serve(store: RepositoryStore, client_factory=sdk_client, *, stopped: asyncio.Event, worker_id: str | None = None,
                limit: int | None = None, poll: float = 1.0, idle_release: float = IDLE_RELEASE_SECONDS) -> None:
    """Claim sessions and run one task per session until `stopped` is set; leases are renewed every poll."""
    worker_id = worker_id or f'{socket.gethostname()}:{os.getpid()}'
    limit = limit or int(os.environ.get('CLAUDE_CODE_MAX_SESSIONS', '4'))
    active: dict[UUID, asyncio.Task] = {}
    try:
        while not stopped.is_set():
            for session_id, task in list(active.items()):
                if task.done():
                    del active[session_id]
                    if not task.cancelled() and task.exception() is not None:
                        logger.warning('Claude Code session %s ended with %s', session_id, type(task.exception()).__name__)
            try:
                claimed = await asyncio.to_thread(claim, store, worker_id, list(active), limit - len(active))
            except psycopg.Error as exc:
                logger.warning('Claude Code worker database unavailable (%s); retrying', type(exc).__name__)
                claimed = []
            for session in claimed:
                if session['previous'] == 'working' and not session['queued']:
                    continue  # its interrupted turn is recorded; claim released it until a message arrives
                runner = SessionRunner(store, session, client_factory, poll=poll, idle_release=idle_release)
                active[session['id']] = asyncio.create_task(runner.run())
            with suppress(TimeoutError):
                await asyncio.wait_for(stopped.wait(), poll)
    finally:
        for task in active.values():
            task.cancel()
        await asyncio.gather(*active.values(), return_exceptions=True)


def secrets_from_environment() -> tuple[bytes, str]:
    """Take the encryption key and the database password out of os.environ: the SDK passes os.environ to the CLI."""
    key = base64.urlsafe_b64decode(os.environ.pop('CREDENTIAL_ENCRYPTION_KEY'))
    password = os.environ.pop('PGPASSWORD', None)
    return key, make_conninfo('', password=password) if password else ''


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s %(message)s')
    key, conninfo = secrets_from_environment()
    store = RepositoryStore(conninfo, key, os.environ['AUTH_ISSUER_URL'])
    run_record.configure(store, None)

    async def run() -> None:
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, stopped.set)
        await serve(store, stopped=stopped)

    asyncio.run(run())
