"""Handler matching: type patterns with *, field conditions on source/type/subject/title/payload.*, Preview reasons."""

import time

import pytest

from knowledge_admin.events.matching import (
    ConditionError,
    check_conditions,
    check_pattern,
    explain,
    matches,
    type_matches,
)

EVENT = {
    'id': 1,
    'source': 'jira',
    'type': 'jira.ticket.updated',
    'subject': 'DEMO-1',
    'title': 'Fix login',
    'payload': {
        'status': 'In Progress',
        'labels': ['agent', 'ui'],
        'priority': None,
        'points': 3,
        'changes': {'status': {'from': 'Open', 'to': 'In Progress'}},
    },
}


def handler(pattern: str = 'jira.ticket.*', *conditions: dict) -> dict:
    return {'type_pattern': pattern, 'conditions': check_conditions(list(conditions))}


def cond(field: str, op: str, value=None) -> dict:
    return {'field': field, 'op': op, 'value': value}


@pytest.mark.parametrize(
    'pattern,expected',
    [
        ('jira.ticket.updated', True),
        ('jira.ticket.*', True),
        ('jira.*', True),
        ('*', True),
        ('jira.*.updated', True),
        ('jira.*.created', False),
        ('jira.ticket', False),
        ('jira.ticket.updated.more', False),
        ('mail.*', False),
    ],
)
def test_type_patterns(pattern, expected):
    check_pattern(pattern)
    assert type_matches(pattern, 'jira.ticket.updated') is expected


@pytest.mark.parametrize('pattern', ['', 'Jira.*', 'jira..x', 'jira.t*', '.jira', 'jira.', 'a' * 201])
def test_bad_patterns(pattern):
    with pytest.raises(ConditionError, match='lowercase dotted names'):
        check_pattern(pattern)


@pytest.mark.parametrize(
    'condition,expected',
    [
        (cond('payload.status', 'equals', 'In Progress'), True),
        (cond('payload.status', 'equals', 'Done'), False),
        (cond('payload.points', 'equals', '3'), True),
        (cond('payload.nope', 'equals', 'x'), False),
        (cond('payload.status', 'not_equals', 'Done'), True),
        (cond('payload.nope', 'not_equals', 'x'), True),
        (cond('payload.labels', 'contains', 'agent'), True),
        (cond('payload.labels', 'contains', 'ag'), False),
        (cond('title', 'contains', 'login'), True),
        (cond('payload.status', 'in', ['Open', 'In Progress']), True),
        (cond('payload.status', 'in', ['Open']), False),
        (cond('subject', 'matches', r'^DEMO-\d+$'), True),
        (cond('payload.labels', 'matches', 'agent'), False),
        (cond('payload.changes.status', 'exists'), True),
        (cond('payload.priority', 'exists'), False),
        (cond('payload.nope', 'exists'), False),
        (cond('source', 'equals', 'jira'), True),
    ],
)
def test_operators(condition, expected):
    assert matches(handler('*', condition), EVENT) is expected


def test_all_conditions_must_hold():
    both = handler(
        'jira.ticket.*', cond('payload.status', 'equals', 'In Progress'), cond('payload.labels', 'contains', 'x')
    )
    assert matches(both, EVENT) is False


def test_explain_names_the_first_failing_check():
    assert explain(handler('mail.*'), EVENT) == 'type is "jira.ticket.updated", not mail.*'
    assert (
        explain(handler('*', cond('payload.status', 'equals', 'Done')), EVENT)
        == 'payload.status is "In Progress", not "Done"'
    )
    assert explain(handler('*', cond('payload.nope', 'exists')), EVENT) == 'payload.nope is missing; it must exist'
    assert (
        explain(handler('*', cond('payload.labels', 'contains', 'x')), EVENT)
        == 'payload.labels is ["agent", "ui"]; it must contain "x"'
    )
    assert explain(handler('*'), EVENT) is None


@pytest.mark.parametrize(
    'condition,message',
    [
        (cond('payload', 'equals', 'x'), 'Condition 1: use source, type, subject, title or payload.<field>'),
        (cond('labels', 'equals', 'x'), 'Condition 1: use source'),
        (cond('title', 'like', 'x'), 'Condition 1: choose an operator'),
        (cond('title', 'equals', ''), 'Condition 1: enter a value'),
        (cond('title', 'equals', ['x']), 'Condition 1: enter a value'),
        (cond('title', 'in', 'x'), 'Condition 1: list one or more values'),
        (cond('title', 'in', []), 'Condition 1: list one or more values'),
        (cond('title', 'matches', '('), 'Condition 1: the pattern is not a valid regular expression'),
        (cond('title', 'matches', 'a' * 501), 'Condition 1: the pattern is longer than 500 characters'),
        (cond('title', 'equals', 'x' * 1001), 'Condition 1: the value is longer than 1000 characters'),
    ],
)
def test_bad_conditions(condition, message):
    with pytest.raises(ConditionError) as raised:
        check_conditions([condition])
    assert str(raised.value).startswith(message)


def test_exists_drops_its_value():
    assert check_conditions([cond('title', 'exists', 'ignored')]) == [{'field': 'title', 'op': 'exists', 'value': None}]


def test_a_runaway_regex_times_out_as_no_match():
    event = {**EVENT, 'subject': 'a' * 40 + 'b'}
    started = time.monotonic()
    assert matches(handler('*', cond('subject', 'matches', r'^(a+)+$')), event) is False
    assert time.monotonic() - started < 2
