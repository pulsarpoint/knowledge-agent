"""Claude Code worker: runs interactive documentation sessions with the Claude Agent SDK, one client per session.

The model key is decrypted here and passed only in the CLI process environment. Nothing imports claude_agent_sdk at
module import time; messages are recognised by class name, so tests drive the worker with same-named fakes."""
import asyncio
import base64
import logging
import os
import shutil
import signal
import stat
import socket
import time
from collections import deque
from collections.abc import Iterable
from contextlib import AsyncExitStack, suppress
from pathlib import Path
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidTag
from knowledge_llm import RuntimeModel, claude_model, decode_model
from psycopg.conninfo import make_conninfo

from knowledge_admin import run_record
from knowledge_admin.claude_code import ALLOWED_TOOLS, DISALLOWED_TOOLS
from knowledge_admin import claude_code_cli as cli
from knowledge_admin.claude_code_cli import HOME_ROOT, MAX_SLOTS, SESSION_HOMES, SLOT_ENV
from knowledge_admin.knowledge_agents import session_worker as shared
from knowledge_admin.knowledge_agents.session_worker import (  # noqa: F401  (Claude Code names kept for callers)
    CLOSED, END_STATUS, IDLE_RELEASE_SECONDS, INTERRUPTED, LEASE_SECONDS, LOST, MAX_PREVIEW_CHARS, MAX_TOOL_OUTPUT,
    PREVIEW_LINES, STOPPED, TOOL_TRUNCATED, capped, clean_text, run_key, scrub as _scrub)
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.run_record import RunRecorder

logger = logging.getLogger(__name__)

BASE_ENV = {'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'DISABLE_AUTOUPDATER': '1', 'DISABLE_TELEMETRY': '1', 'DISABLE_ERROR_REPORTING': '1',
            'CLAUDE_CODE_SUBPROCESS_ENV_SCRUB': '1', 'CLAUDE_CODE_DISABLE_AUTO_MEMORY': '1', 'SHELL': '/bin/bash'}
HINTS = (
    (('inference profile', 'on-demand throughput'),
     'Bedrock needs an inference profile ID for this model, such as eu.anthropic.claude-sonnet-4-5-20250929-v1:0. '
     'Change the model ID in LLM Settings.'),
    (('403', '401', 'accessdenied', 'authentication'), 'Check the key and model access of this connection in LLM Settings.'),
)
DEFAULT_HINT = 'Check the model connection in LLM Settings, then send a message to retry.'
INTERRUPT_GRACE_SECONDS = 30.0


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


def session_env(session_id, slot: int) -> dict[str, str]:
    """The session's own HOME (its CLI transcript lives there, for resume in any slot) and the slot whose user runs it."""
    if not 1 <= slot <= MAX_SLOTS:
        raise ValueError(f'Worker slots are 1 to {MAX_SLOTS}')
    home = f'{SESSION_HOMES}/{UUID(str(session_id))}'
    return {'HOME': home, 'TMPDIR': f'{home}/tmp', SLOT_ENV: str(slot)}


def session_tools(session: dict[str, Any]) -> list[str]:
    """The agent's Claude Code tools when the session started; sessions created before step 1 have all four."""
    chosen = session['prompt_snapshot'].get('claude_code_tools', list(ALLOWED_TOOLS))
    return [tool for tool in ALLOWED_TOOLS if tool in chosen]


def build_options(session: dict[str, Any], model: RuntimeModel, slot: int = 1) -> dict[str, Any]:
    """Keyword arguments for ClaudeAgentOptions: the agent's Claude Code tools, the chosen folders, no limits."""
    tools = session_tools(session)
    folders = [folder['path'] for folder in session['context_snapshot']['folders']]
    options = {
        'system_prompt': {'type': 'preset', 'preset': 'claude_code', 'append': session['prompt_snapshot']['system_prompt']},
        'tools': tools, 'allowed_tools': list(tools),
        'disallowed_tools': [*DISALLOWED_TOOLS, *(tool for tool in ALLOWED_TOOLS if tool not in tools)],
        'permission_mode': 'dontAsk', 'setting_sources': [], 'skills': [], 'mcp_servers': {}, 'strict_mcp_config': True,
        'cwd': folders[0], 'add_dirs': folders, 'model': model.model,
        'env': {**BASE_ENV, **session_env(session['id'], slot), **credential_env(model)},
        'resume': session['claude_session_id'],
    }
    if os.environ.get('CLAUDE_CODE_CLI'):
        options['cli_path'] = os.environ['CLAUDE_CODE_CLI']
    return options


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
    """Claude Code sessions only (session_worker.claim)."""
    return shared.claim(store, worker_id, active, free, 'claude_code')


class SessionRunner(shared.SessionRunner):
    """One Claude Code session in one asyncio task: one client, created on the first turn and kept until the session
    closes or has been idle for `idle_release` seconds. This task connects, sends and interrupts; a child task reads
    responses. Claiming, queued messages, Stop and finishing a turn are shared (session_worker)."""

    def __init__(self, store: RepositoryStore, session: dict, client_factory=sdk_client, *, poll: float = 1.0,
                 idle_release: float = IDLE_RELEASE_SECONDS, slot: int = 1):
        super().__init__(store, session, poll=poll, idle_release=idle_release, slot=slot)
        self.client_factory = client_factory
        self.stack: AsyncExitStack | None = None
        self.client = None
        self.stderr: deque[str] = deque(maxlen=20)

    async def run_turn(self, number: int, text: str) -> None:
        self.stderr.clear()
        recorder = RunRecorder(run_key=run_key(self.id, number), project_id=str(self.session['project_id']),
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
        await asyncio.to_thread(self.finish_turn, number, status, message, draft=clean_text(turn.draft, self.secrets),
                                usage=turn.usage, claude_session_id=turn.session_id)

    async def consume(self, turn: Turn, recorder: RunRecorder) -> None:
        async for message in self.client.receive_response():
            turn.add(message)
            await asyncio.to_thread(recorder.flush)

    async def connect(self) -> None:
        if self.client is not None:
            return
        model, session, keys = await asyncio.to_thread(self.load)
        self.secrets[:] = list(dict.fromkeys(key for key in [model.api_key, *keys] if key))
        missing = next((f['path'] for f in session['context_snapshot']['folders'] if not Path(f['path']).is_dir()), None)
        if missing is not None:
            raise ValueError(f'The folder {missing} no longer exists. Start a new session without it.')
        options = build_options(session, model, self.slot)
        options['stderr'] = self.stderr.append
        stack = AsyncExitStack()
        try:
            self.client = await stack.enter_async_context(self.client_factory(options))
        except BaseException:
            await stack.aclose()
            raise
        self.stack = stack

    def load(self) -> tuple[RuntimeModel, dict, list[str]]:
        """The session's model, its snapshots and the keys of every enabled Claude connection: each is redacted from
        events, drafts and errors, not only the session's own key."""
        with self.store.connect() as conn:
            row = conn.execute('SELECT * FROM ka.llm_models WHERE id=%s', (self.session['model_id'],)).fetchone()
            session = conn.execute('''SELECT id,context_snapshot,prompt_snapshot,claude_session_id FROM ka.agent_sessions_cc
                WHERE id=%s''', (self.id,)).fetchone()
            others = conn.execute('''SELECT * FROM ka.llm_models WHERE enabled AND api_key_ciphertext IS NOT NULL
                AND id<>%s''', (self.session['model_id'],)).fetchall()
        if row is None or not row['enabled']:
            raise ValueError('The session model is disabled or was removed. Enable it in LLM Settings.')
        keys = []
        for other in others:
            if claude_model(other['provider'], other['api_format'], other['model']):
                with suppress(InvalidTag, ValueError):
                    keys.append(decode_model(other, self.store.cipher).api_key)
        try:
            return decode_model(row, self.store.cipher), session, keys
        except InvalidTag:
            raise ValueError('The model key could not be decrypted. Save it again in LLM Settings.') from None

    async def disconnect(self) -> None:
        stack, self.stack, self.client = self.stack, None, None
        if stack is not None:
            with suppress(Exception):
                await stack.aclose()


async def serve(store: RepositoryStore, client_factory=sdk_client, *, stopped: asyncio.Event, worker_id: str | None = None,
                limit: int | None = None, poll: float = 1.0, idle_release: float = IDLE_RELEASE_SECONDS) -> None:
    """Run Claude Code sessions (session_worker.serve). Each running session holds one slot (its own uid for the CLI);
    a later turn may run in any free slot. A slot is cleared (reclaim_slot) before it is used again."""
    worker_id = worker_id or f'{socket.gethostname()}:{os.getpid()}'
    limit = limit or int(os.environ.get('CLAUDE_CODE_MAX_SESSIONS', '4'))
    if limit > MAX_SLOTS:
        logger.warning('CLAUDE_CODE_MAX_SESSIONS is %s; this worker runs at most %s sessions', limit, MAX_SLOTS)
        limit = MAX_SLOTS
    await shared.serve(
        store, lambda session, slot: SessionRunner(store, session, client_factory, poll=poll, idle_release=idle_release,
                                                   slot=slot),
        runtime='claude_code', label='Claude Code', stopped=stopped, worker_id=worker_id, limit=limit, poll=poll,
        reclaim=lambda session_id, slot: reclaim_slot(session_id, slot))  # looked up per call: tests replace it


def secrets_from_environment() -> tuple[bytes, str]:
    """Take the encryption key and the database password out of os.environ: the SDK passes os.environ to the CLI."""
    key = base64.urlsafe_b64decode(os.environ.pop('CREDENTIAL_ENCRYPTION_KEY'))
    password = os.environ.pop('PGPASSWORD', None)
    return key, make_conninfo('', password=password) if password else ''


def _root_top(home: str) -> None:
    """A HOME that could not be taken back whole: its top folder is root's (0700), so no slot user can enter it."""
    os.lchown(home, 0, 0)
    os.chmod(home, 0o700)


def _take_back(session_id: str, home: str) -> None:
    try:
        cli.own_tree(home, 0, 0)
    except cli.HomeError:
        logger.warning('Claude Code session %s: its folder could not be taken back whole; only its top folder is '
                       "root's now, and the session cannot start again", session_id)
        _root_top(home)


def sweep_shm(uid: int, root: str = '/dev/shm') -> None:
    """Remove what the slot user left in /dev/shm (POSIX shared memory, files, folders), never following a link.
    Root owns /dev/shm, so it may unlink there; a folder is first made root's (own_tree), then removed."""
    try:
        names = os.listdir(root)
    except FileNotFoundError:
        return
    for name in names:
        path = f'{root}/{name}'
        try:
            info = os.lstat(path)
            if info.st_uid != uid:
                continue
            if stat.S_ISDIR(info.st_mode):
                cli.own_tree(path, 0, 0)
                shutil.rmtree(path)
            else:
                os.unlink(path)
        except (OSError, cli.HomeError) as exc:
            logger.warning('Claude Code slot cleanup: an entry in %s could not be removed (%s)', root, type(exc).__name__)


def reclaim_slot(session_id, slot: int) -> None:
    """A session left its slot: kill every process of the slot user, sweep its /dev/shm entries, then take the session
    HOME back for root. Only a failed kill (or an unprotectable HOME) raises: the slot then stays out of use."""
    uid, gid = cli.slot_ids(slot)
    cli.kill_slot(uid, gid)
    sweep_shm(uid)
    session_id = str(UUID(str(session_id)))
    home = f'{SESSION_HOMES}/{session_id}'
    if os.path.lexists(home):
        _take_back(session_id, home)


def reclaim_all(sessions: str = SESSION_HOMES) -> None:
    """At start no session holds a slot: kill every slot user's processes, then take back each session HOME that is
    not root's. One slot or HOME that fails is logged and skipped; it never stops the worker."""
    for slot in range(1, MAX_SLOTS + 1):
        try:
            uid, gid = cli.slot_ids(slot)
            cli.kill_slot(uid, gid)
            sweep_shm(uid)
        except Exception as exc:
            logger.warning('Claude Code slot %s could not be cleared at start (%s)', slot, type(exc).__name__)
    for name in os.listdir(sessions):
        path = f'{sessions}/{name}'
        try:
            if os.lstat(path).st_uid != 0:
                _take_back(name, path)
        except OSError as exc:
            logger.warning('Claude Code session %s: its folder could not be taken back at start (%s)', name,
                           type(exc).__name__)


def prepare_home_root(root: str = HOME_ROOT) -> None:
    """/home/claude and its sessions folder belong to root, traversable but not listable (0711): a slot user reaches
    only its session's own 0700 folder. /home/claude/tmp is the worker's (root's, 0700). Fixes a volume created by an
    earlier image, where the single claude user owned /home/claude."""
    for path in (root, f'{root}/sessions'):
        os.makedirs(path, mode=0o711, exist_ok=True)
        info = os.lstat(path)
        if info.st_uid != 0 or info.st_gid != 0:
            os.lchown(path, 0, 0)
        os.chmod(path, 0o711)
        if path == root:  # the worker's own TMPDIR; what an earlier image's single claude user left is no slot user's
            os.makedirs(f'{root}/tmp', mode=0o700, exist_ok=True)
            for name in os.listdir(root):
                entry = f'{root}/{name}'
                if name != 'sessions' and os.lstat(entry).st_uid != 0:
                    os.lchown(entry, 0, 0)
                    if not os.path.islink(entry):
                        os.chmod(entry, 0o700)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s %(message)s')
    key, conninfo = secrets_from_environment()
    prepare_home_root()
    reclaim_all()
    # The SDK's version probe would start the CLI wrapper without a slot; the bundled CLI is the SDK's own version.
    os.environ['CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK'] = '1'
    store = RepositoryStore(conninfo, key, os.environ['AUTH_ISSUER_URL'])
    run_record.configure(store, None)

    async def run() -> None:
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, stopped.set)
        await serve(store, stopped=stopped)

    asyncio.run(run())
