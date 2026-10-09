"""Handler templates: the matched event is `event`; strict, sandboxed, size-limited; payload must be a JSON object."""
from datetime import UTC, datetime

import pytest

from knowledge_admin.event_templates import TemplateProblem, check_emit, check_prompt, render_emit, render_prompt

EVENT = {'id': 7, 'source': 'jira', 'type': 'jira.ticket.updated', 'subject': 'DEMO-1', 'title': 'Fix <login>',
         'occurred_at': datetime(2026, 10, 9, 10, tzinfo=UTC), 'parent_id': None, 'root_id': 7, 'depth': 0,
         'payload': {'status': 'In Progress', 'summary': 'Fix "login"', 'labels': ['agent']}}


def test_defaults_copy_the_parent():
    assert render_emit({'type': 'triage.requested'}, EVENT) == {
        'type': 'triage.requested', 'subject': 'DEMO-1', 'title': 'Fix <login>', 'payload': EVENT['payload']}


def test_custom_templates_see_every_event_field():
    emit = {'type': 'triage.{{ event.payload.status | lower | replace(" ", "_") }}',
            'subject': '{{ event.subject }}-triage', 'title': 'Triage {{ event.title }} ({{ event.occurred_at }})',
            'payload': '{"summary": {{ event.payload.summary | tojson }}, "from": {{ event.id }}, "root": {{ event.root_id }}}'}
    assert render_emit(emit, EVENT) == {'type': 'triage.in_progress', 'subject': 'DEMO-1-triage',
                                        'title': 'Triage Fix <login> (2026-10-09T10:00:00+00:00)',
                                        'payload': {'summary': 'Fix "login"', 'from': 7, 'root': 7}}


@pytest.mark.parametrize('emit,message', [
    ({'type': '{{ event.payload.nope }}'}, 'Template error:'),
    ({'type': 'Bad Type'}, 'The new event type "Bad Type" is not valid'),
    ({'type': 'a.b', 'subject': '{{ "" }}'}, 'The new event subject is empty.'),
    ({'type': 'a.b', 'title': '{{ "x" * 501 }}'}, 'The new event subject and title must be at most 500 characters.'),
    ({'type': 'a.b', 'payload': '{"title": {{ event.title }}}'}, 'The payload template must render JSON'),
    ({'type': 'a.b', 'payload': '[1, 2]'}, 'The payload template must render a JSON object.'),
    ({'type': 'a.b', 'subject': "{{ ''.__class__.__mro__ }}"}, 'Template error:'),
])
def test_render_problems(emit, message):
    with pytest.raises(TemplateProblem) as raised:
        render_emit(emit, EVENT)
    assert str(raised.value).startswith(message)


def test_huge_output_is_refused_before_it_is_built():
    with pytest.raises(TemplateProblem, match='longer than 20000 characters'):
        render_prompt("{{ 'x' * 10000000 }}", EVENT)
    with pytest.raises(TemplateProblem, match='exponent is too large'):
        render_prompt('{{ 9 ** 9 ** 9 }}', EVENT)
    with pytest.raises(TemplateProblem, match='longer than 20000 characters'):
        render_prompt('{% for i in range(30000) %}x{% endfor %}', EVENT)


def test_prompt():
    assert render_prompt('  Triage {{ event.subject }}: {{ event.payload.summary }}  ', EVENT) == 'Triage DEMO-1: Fix "login"'
    with pytest.raises(TemplateProblem, match='The prompt rendered empty.'):
        render_prompt('{{ "" }}', EVENT)


def test_syntax_is_checked_on_save():
    check_emit({'type': 'triage.requested', 'payload': '{"a": {{ event.id }}}'})
    check_prompt('Triage {{ event.subject }}')
    with pytest.raises(TemplateProblem, match=r'^Subject: .*line 1'):
        check_emit({'type': 'a.b', 'subject': '{{ event.subject'})
    with pytest.raises(TemplateProblem, match='^New event type: enter a type'):
        check_emit({'type': '  '})
    with pytest.raises(TemplateProblem, match='^Prompt: write what the agent should do.'):
        check_prompt(' ')
    with pytest.raises(TemplateProblem, match='^Prompt: '):
        check_prompt('{% if %}')
