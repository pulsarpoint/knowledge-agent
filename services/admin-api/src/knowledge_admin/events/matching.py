"""Event handler matching: an event type pattern plus field conditions that must all hold. Used by the event processor
and by the handler API (validation on save, the reason shown by Preview)."""
import json
import re
from typing import Any

import regex

OPERATORS = ('equals', 'not_equals', 'contains', 'in', 'matches', 'exists')
PATTERN = re.compile(r'^(\*|[a-z][a-z0-9_]*)(\.(\*|[a-z][a-z0-9_]*))*$')
FIELD = re.compile(r'^(source|type|subject|title|payload(\.[A-Za-z0-9_-]+)+)$')
MAX_PATTERN, MAX_REGEX, MAX_VALUE = 200, 500, 1000
REGEX_SECONDS = 0.1
MISSING = object()
BAD_PATTERN = 'Use lowercase dotted names with * for any part, e.g. jira.ticket.* or jira.ticket.updated.'
WANTED = {'not_equals': 'not be', 'contains': 'contain', 'in': 'be one of', 'matches': 'match', 'exists': 'exist'}


class ConditionError(ValueError):
    """A pattern or condition that cannot be saved; the message is shown on the handler form."""


def check_pattern(pattern: str) -> None:
    if len(pattern) > MAX_PATTERN or not PATTERN.fullmatch(pattern):
        raise ConditionError(BAD_PATTERN)


def check_conditions(conditions: list[dict]) -> list[dict]:
    """The conditions as stored, {field, op, value}: value is None for exists, a list of strings for in, else a string."""
    checked = []
    for number, condition in enumerate(conditions, 1):
        field, op, value = condition.get('field') or '', condition.get('op'), condition.get('value')
        where = f'Condition {number}'
        if not FIELD.fullmatch(field):
            raise ConditionError(f'{where}: use source, type, subject, title or payload.<field>, e.g. payload.status.')
        if op not in OPERATORS:
            raise ConditionError(f'{where}: choose an operator.')
        if op == 'exists':
            value = None
        elif op == 'in':
            if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
                raise ConditionError(f'{where}: list one or more values.')
        elif not isinstance(value, str) or not value:
            raise ConditionError(f'{where}: enter a value.')
        if op == 'matches' and len(value) > MAX_REGEX:
            raise ConditionError(f'{where}: the pattern is longer than {MAX_REGEX} characters.')
        if value is not None and any(len(item) > MAX_VALUE for item in (value if isinstance(value, list) else [value])):
            raise ConditionError(f'{where}: the value is longer than {MAX_VALUE} characters.')
        if op == 'matches':
            try:
                regex.compile(value)
            except regex.error as exc:
                raise ConditionError(f'{where}: the pattern is not a valid regular expression ({exc}).') from None
        checked.append({'field': field, 'op': op, 'value': value})
    return checked


def type_matches(pattern: str, event_type: str) -> bool:
    """`*` alone matches every type; a `*` part matches one part, a trailing `.*` one or more trailing parts."""
    if pattern == '*':
        return True
    parts = pattern.split('.')
    expression = r'\.'.join(
        (r'[a-z0-9_]+(\.[a-z0-9_]+)*' if index == len(parts) - 1 else r'[a-z0-9_]+') if part == '*' else re.escape(part)
        for index, part in enumerate(parts))
    return re.fullmatch(expression, event_type) is not None


def lookup(event: dict, field: str) -> Any:
    if not field.startswith('payload.'):
        return event.get(field, MISSING)
    value: Any = event.get('payload')
    for key in field.split('.')[1:]:
        if not isinstance(value, dict) or key not in value:
            return MISSING
        value = value[key]
    return value


def text(value: Any) -> str:
    """Strings as they are; numbers, booleans and null as JSON (3 → "3", true → "true")."""
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True)


def holds(condition: dict, actual: Any) -> bool:
    op, expected = condition['op'], condition['value']
    if op == 'exists':
        return actual is not MISSING and actual is not None
    if actual is MISSING:
        return op == 'not_equals'
    if op == 'equals':
        return text(actual) == expected
    if op == 'not_equals':
        return text(actual) != expected
    if op == 'contains':
        if isinstance(actual, list):
            return any(text(item) == expected for item in actual)
        return isinstance(actual, str) and expected in actual
    if op == 'in':
        return text(actual) in expected
    if op == 'matches':
        if isinstance(actual, (dict, list)):
            return False
        try:
            return regex.search(expected, text(actual), timeout=REGEX_SECONDS) is not None
        except TimeoutError:
            return False
    return False


def show(value: Any) -> str:
    return 'missing' if value is MISSING else json.dumps(value, ensure_ascii=False)


def explain(handler: dict, event: dict) -> str | None:
    """None when the handler matches the event, else the first failing check in words (Preview shows it)."""
    if not type_matches(handler['type_pattern'], event['type']):
        return f"type is {show(event['type'])}, not {handler['type_pattern']}"
    for condition in handler['conditions']:
        actual = lookup(event, condition['field'])
        if holds(condition, actual):
            continue
        field, op, value = condition['field'], condition['op'], condition['value']
        if op == 'equals':
            return f'{field} is {show(actual)}, not {show(value)}'
        return f"{field} is {show(actual)}; it must {WANTED[op]}{'' if op == 'exists' else ' ' + show(value)}"
    return None


def matches(handler: dict, event: dict) -> bool:
    return explain(handler, event) is None
