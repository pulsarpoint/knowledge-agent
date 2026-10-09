"""Handler templates: Jinja in a sandbox with the matched event as `event`. Emit renders the new event's type, subject,
title and payload (JSON that must be an object); Send to agent renders the agent's first message."""
import json
import math
import re
from typing import Any

from jinja2 import StrictUndefined, TemplateSyntaxError
from jinja2.exceptions import SecurityError
from jinja2.sandbox import SandboxedEnvironment

MAX_OUTPUT = 20000
MAX_TEXT = 500
MAX_POWER_DIGITS = 50
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
    return {'id': event['id'], 'source': event['source'], 'type': event['type'], 'subject': event['subject'],
            'title': event['title'], 'occurred_at': occurred.isoformat() if hasattr(occurred, 'isoformat') else occurred,
            'payload': event['payload'], 'parent_id': event.get('parent_id'), 'root_id': event.get('root_id'),
            'depth': event.get('depth', 0)}


def check(source: str, label: str) -> None:
    try:
        ENV.parse(source)
    except TemplateSyntaxError as exc:
        raise TemplateProblem(f'{label}: {exc.message} (line {exc.lineno}).') from None


def render(source: str, event: dict) -> str:
    try:
        chunks, size = [], 0
        for chunk in ENV.from_string(source).generate(event=context(event)):
            size += len(chunk)
            if size > MAX_OUTPUT:
                raise SecurityError(TOO_LONG)
            chunks.append(chunk)
    except Exception as exc:  # undefined fields, sandbox refusals, errors raised inside filters
        message = str(exc)
        raise TemplateProblem(message if message == TOO_LONG else f'Template error: {message}') from None
    return ''.join(chunks)


def check_emit(emit: dict) -> None:
    if not (emit.get('type') or '').strip():
        raise TemplateProblem('New event type: enter a type, e.g. triage.requested.')
    for field, label in (('type', 'New event type'), ('subject', 'Subject'), ('title', 'Title'), ('payload', 'Payload')):
        check(emit.get(field) or DEFAULTS.get(field, ''), label)


def check_prompt(prompt: str) -> None:
    if not prompt.strip():
        raise TemplateProblem('Prompt: write what the agent should do.')
    check(prompt, 'Prompt')


def render_emit(emit: dict, event: dict) -> dict:
    kind = render(emit['type'], event).strip()
    if not EVENT_TYPE.fullmatch(kind):
        raise TemplateProblem(f'The new event type "{kind[:100]}" is not valid; use lowercase dotted names such as triage.requested.')
    subject = render(emit.get('subject') or DEFAULTS['subject'], event).strip()
    if not subject:
        raise TemplateProblem('The new event subject is empty.')
    title = render(emit.get('title') or DEFAULTS['title'], event).strip()
    if len(subject) > MAX_TEXT or len(title) > MAX_TEXT:
        raise TemplateProblem(f'The new event subject and title must be at most {MAX_TEXT} characters.')
    try:
        payload = json.loads(render(emit.get('payload') or DEFAULTS['payload'], event))
    except json.JSONDecodeError as exc:
        raise TemplateProblem(f'The payload template must render JSON ({exc.msg}, line {exc.lineno}). Use | tojson for values.') from None
    if not isinstance(payload, dict):
        raise TemplateProblem('The payload template must render a JSON object.')
    return {'type': kind, 'subject': subject, 'title': title, 'payload': payload}


def render_prompt(prompt: str, event: dict) -> str:
    text = render(prompt, event).strip()
    if not text:
        raise TemplateProblem('The prompt rendered empty.')
    if '\x00' in text:
        raise TemplateProblem('The prompt contains a null character.')
    return text
