"""Handler templates: Jinja in a sandbox with the matched event as `event`. Emit renders the new event's type, subject,
title and payload (JSON that must be an object); Send to agent renders the agent's first message."""

import atexit
import json
import math
import multiprocessing
import re
import threading
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FutureTimeout
from concurrent.futures.process import BrokenProcessPool
from typing import Any

from jinja2 import StrictUndefined, TemplateSyntaxError
from jinja2.exceptions import SecurityError
from jinja2.sandbox import SandboxedEnvironment

MAX_OUTPUT = 20000
MAX_TEXT = 500
MAX_POWER_DIGITS = 50
RENDER_SECONDS = 2
CHILD_MEMORY = 512 * 1024 * 1024
START_SECONDS = 30
EVENT_TYPE = re.compile(r'^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$')
DEFAULTS = {'subject': '{{ event.subject }}', 'title': '{{ event.title }}', 'payload': '{{ event.payload | tojson }}'}
TOO_LONG = f'Template output is longer than {MAX_OUTPUT} characters.'


class TemplateProblem(ValueError):
    """A template that does not parse or render; shown on the handler form, in Preview and on the handler run."""


class Sandbox(SandboxedEnvironment):
    """Jinja's sandbox that also refuses to build huge strings, lists or numbers ('x' * 10**9, 9 ** 9 ** 9)."""

    intercepted_binops = frozenset({'*', '**'})

    def call_binop(self, context, operator, left, right):
        if operator == '**' and isinstance(right, (int, float)) and isinstance(left, (int, float)):
            # Jinja parses 9 ** 9 ** 9 left to right, so the exponent alone stays small: also bound the result's digits.
            digits = abs(right) * math.log10(abs(left)) if left and abs(right) <= 100 else 0
            if abs(right) > 100 or digits > MAX_POWER_DIGITS:
                raise SecurityError('The exponent is too large.')
        if operator == '*':
            sized = next((side for side in (left, right) if isinstance(side, (str, list, tuple))), None)
            count = next((side for side in (left, right) if isinstance(side, int) and not isinstance(side, bool)), 0)
            if sized is not None and count * len(sized) > MAX_OUTPUT:
                raise SecurityError(TOO_LONG)
        return super().call_binop(context, operator, left, right)


ENV = Sandbox(undefined=StrictUndefined, autoescape=False)


def context(event: dict) -> dict[str, Any]:
    occurred = event.get('occurred_at')
    return {
        'id': event['id'],
        'source': event['source'],
        'type': event['type'],
        'subject': event['subject'],
        'title': event['title'],
        'occurred_at': occurred.isoformat() if hasattr(occurred, 'isoformat') else occurred,
        'payload': event['payload'],
        'parent_id': event.get('parent_id'),
        'root_id': event.get('root_id'),
        'depth': event.get('depth', 0),
    }


def check(source: str, label: str) -> None:
    try:
        ENV.parse(source)
    except TemplateSyntaxError as exc:
        raise TemplateProblem(f'{label}: {exc.message} (line {exc.lineno}).') from None
    except RecursionError:
        raise TemplateProblem(f'{label}: the template is nested too deeply.') from None


def render_in_child(source: str, ctx: dict) -> str:
    """Runs in the child process: the sandboxed render with the output cap."""
    try:
        chunks, size = [], 0
        for chunk in ENV.from_string(source).generate(event=ctx):
            size += len(chunk)
            if size > MAX_OUTPUT:
                raise SecurityError(TOO_LONG)
            chunks.append(chunk)
    except MemoryError:
        raise
    except Exception as exc:  # undefined fields, sandbox refusals, errors raised inside filters
        message = str(exc)
        raise TemplateProblem(message if message == TOO_LONG else f'Template error: {message}') from None
    return ''.join(chunks)


def init_child() -> None:
    """Caps the child's address space where the platform enforces it (Linux); elsewhere the timeout is the guard."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (CHILD_MEMORY, CHILD_MEMORY))
    except Exception:  # noqa: BLE001 - not supported on this platform
        pass


def ping() -> None:
    """Lets the parent wait for the child to finish starting up outside a render's time budget."""


_lock = threading.Lock()
_pool: ProcessPoolExecutor | None = None


def discard_pool() -> None:
    """Kills the child (a runaway render cannot be interrupted) so the next render starts a fresh one."""
    global _pool
    pool, _pool = _pool, None
    if pool is None:
        return
    for process in list((getattr(pool, '_processes', None) or {}).values()):
        try:
            process.kill()
        except Exception:  # noqa: BLE001 - already gone
            pass
    pool.shutdown(wait=False, cancel_futures=True)


def shutdown() -> None:
    with _lock:
        discard_pool()


atexit.register(shutdown)


def render(source: str, event: dict) -> str:
    global _pool
    ctx = context(event)
    with _lock:  # one render at a time, so a timeout only ever kills the render that overran
        try:
            if _pool is None:
                _pool = ProcessPoolExecutor(
                    max_workers=1, mp_context=multiprocessing.get_context('spawn'), initializer=init_child
                )
                _pool.submit(ping).result(timeout=START_SECONDS)
            return _pool.submit(render_in_child, source, ctx).result(timeout=RENDER_SECONDS)
        except FutureTimeout:
            discard_pool()
            raise TemplateProblem('Template took too long to render.') from None
        except (BrokenProcessPool, MemoryError):
            discard_pool()
            raise TemplateProblem('Template used too much memory.') from None


def check_emit(emit: dict) -> None:
    if not (emit.get('type') or '').strip():
        raise TemplateProblem('New event type: enter a type, e.g. triage.requested.')
    for field, label in (
        ('type', 'New event type'),
        ('subject', 'Subject'),
        ('title', 'Title'),
        ('payload', 'Payload'),
    ):
        check(emit.get(field) or DEFAULTS.get(field, ''), label)


def check_prompt(prompt: str) -> None:
    if not prompt.strip():
        raise TemplateProblem('Prompt: write what the agent should do.')
    check(prompt, 'Prompt')


def has_nul(value: Any) -> bool:
    if isinstance(value, str):
        return '\x00' in value
    if isinstance(value, dict):
        return any(has_nul(key) or has_nul(item) for key, item in value.items())
    if isinstance(value, list):
        return any(has_nul(item) for item in value)
    return False


def render_emit(emit: dict, event: dict) -> dict:
    if not (emit.get('type') or '').strip():
        raise TemplateProblem('New event type: enter a type, e.g. triage.requested.')
    kind = render(emit['type'], event).strip()
    if not EVENT_TYPE.fullmatch(kind):
        raise TemplateProblem(
            f'The new event type "{kind[:100]}" is not valid; use lowercase dotted names such as triage.requested.'
        )
    subject = render(emit.get('subject') or DEFAULTS['subject'], event).strip()
    if not subject:
        raise TemplateProblem('The new event subject is empty.')
    title = render(emit.get('title') or DEFAULTS['title'], event).strip()
    if len(subject) > MAX_TEXT or len(title) > MAX_TEXT:
        raise TemplateProblem(f'The new event subject and title must be at most {MAX_TEXT} characters.')
    try:
        payload = json.loads(render(emit.get('payload') or DEFAULTS['payload'], event))
    except json.JSONDecodeError as exc:
        raise TemplateProblem(
            f'The payload template must render JSON ({exc.msg}, line {exc.lineno}). Use | tojson for values.'
        ) from None
    if not isinstance(payload, dict):
        raise TemplateProblem('The payload template must render a JSON object.')
    if has_nul(subject) or has_nul(title) or has_nul(payload):
        raise TemplateProblem('Template output contains a NUL character.')
    return {'type': kind, 'subject': subject, 'title': title, 'payload': payload}


def render_prompt(prompt: str, event: dict) -> str:
    text = render(prompt, event).strip()
    if not text:
        raise TemplateProblem('The prompt rendered empty.')
    if '\x00' in text:
        raise TemplateProblem('The prompt contains a null character.')
    return text
