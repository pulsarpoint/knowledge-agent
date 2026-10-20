"""Tuning reviews: the reviewer's output shape and applying a reviewed suggestion. Correcting invalid reviewer output
is tested with the Test session review in test_test_session_feedback.py."""

import json

import pytest
from access_helpers import of
from agents import AgentOutputSchema, ModelBehaviorError

import psycopg

from knowledge_admin.agents import reviews as agent_reviews
from knowledge_admin.agents.clones import NAME_TAKEN
from knowledge_admin.agents.reviews import (
    AGENT_CHANGED,
    INVALID_TEXT,
    NOTHING_TO_APPLY,
    REVIEW_NOT_FOUND,
    TuningSuggestion,
)
from knowledge_admin.memory import store as memory
from knowledge_admin.knowledge_agents.config import get_agent_config, sections
from session_rows import a_model, add_review, add_test_session
from test_agents import agent_mounts, saved_agent  # noqa: F401
from test_sdk_agent import responses_api  # noqa: F401  (fixture)
from test_repositories import HEADERS


NOTE = {'name': 'payments/retries', 'content': 'Payments retries three times.', 'metadata': {'service': 'payments'}}


def test_sdk_tuning_schema_decodes_nested_changes_and_notes():
    changes = {'instructions': 'Use "read-only" checks.\nReport uncertainty.'}
    schema = AgentOutputSchema(TuningSuggestion, strict_json_schema=False)
    proposal = schema.validate_json(
        json.dumps(
            {
                'suggestion': 'Clarify inspection steps.',
                'changes': json.dumps(changes),
                'memory_notes': json.dumps([NOTE]),
            }
        )
    )
    assert proposal.changes.model_dump(mode='json') == changes
    assert [note.model_dump(mode='json') for note in proposal.memory_notes] == [NOTE]
    dumped = json.loads(proposal.model_dump_json())
    assert isinstance(dumped['changes'], dict) and isinstance(dumped['memory_notes'], list)
    assert schema.json_schema()['properties']['changes'] == {'$ref': '#/$defs/InstructionChanges'}
    assert 'system_prompt' not in json.dumps(schema.json_schema())


def test_a_note_without_metadata_is_valid():
    proposal = TuningSuggestion.model_validate(
        {'suggestion': 'x', 'changes': {'instructions': None}, 'memory_notes': [{'name': 'a', 'content': 'b'}]}
    )
    assert proposal.memory_notes[0].metadata == {}


@pytest.mark.parametrize(
    'changes',
    [
        '{invalid JSON',
        '[]',
        'null',
        '{}',
        json.dumps({'instructions': None, 'ssh_enabled': True}),
        json.dumps({'system_prompt': None, 'instructions': None}),  # the system prompt is never proposed
        json.dumps({'instructions': None, 'server_instructions': []}),  # overrides are gone
    ],
)
def test_sdk_tuning_schema_still_rejects_invalid_encoded_changes(changes):
    schema = AgentOutputSchema(TuningSuggestion, strict_json_schema=False)
    with pytest.raises(ModelBehaviorError):
        schema.validate_json(json.dumps({'suggestion': 'Suggested changes.', 'changes': changes, 'memory_notes': []}))


@pytest.mark.parametrize(
    'notes',
    [
        [NOTE] * 11,  # at most 10
        [{**NOTE, 'name': 'Payments Retries'}],  # memory name rules
        [{**NOTE, 'metadata': {'Service': 'payments'}}],
        [{**NOTE, 'metadata': {f'k{n}': 'v' for n in range(20)}}],  # 21 keys with source: review
        [{**NOTE, 'content': '  '}],
        [{**NOTE, 'extra': 1}],
        '{invalid JSON',
    ],
)
def test_sdk_tuning_schema_rejects_invalid_notes(notes):
    schema = AgentOutputSchema(TuningSuggestion, strict_json_schema=False)
    encoded = notes if isinstance(notes, str) else json.dumps(notes)
    with pytest.raises(ModelBehaviorError):
        schema.validate_json(
            json.dumps({'suggestion': 'x', 'changes': {'instructions': None}, 'memory_notes': encoded})
        )


NARROWED = {'access': {'resources': [], 'roles': {'content_editor': False}}}  # a chat given none of its resources


def saved_review(store, agent_id: str, changes: dict, agent_config: dict | None = None, notes=None) -> str:
    """A tuning suggestion saved for one of the agent's Test sessions."""
    with store.connect() as conn:
        project, actor = store.project(conn, 'shop'), store.actor(conn, 'reviewer', 'reviewer@test')
        session = add_test_session(conn, project, actor, agent_id, a_model(conn, actor), status='waiting', turns=1)
        return str(add_review(conn, session, actor, changes=changes, agent_config=agent_config, notes=notes))


def changes(instructions=None) -> dict:
    return {'instructions': instructions}


def apply_review(client, agent_id, review, body, target='agent', notes=(), **change):
    return client.post(
        f'/api/agents/{agent_id}/reviews/{review}/apply',
        headers=HEADERS,
        json={
            'target': target,
            'revision': body['revision'],
            'fingerprint': body['fingerprint'],
            'memory_notes': list(notes),
            **change,
        },
    )


def without(config, *keys) -> dict:
    return {key: value for key, value in sections(config).items() if key not in keys}


def applied_audit(store) -> list[dict]:
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT details FROM ka.audit_events WHERE action='agent.suggestion.applied' ORDER BY id"
        ).fetchall()
    return [row['details'] for row in rows]


def test_a_suggestion_is_applied_to_this_agent_after_review(store, saved_agent):
    client = saved_agent[0]
    before = get_agent_config('operations', conninfo=store.conninfo)
    review = saved_review(store, 'operations', changes(instructions='Gather evidence first.'))
    source = client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS)
    assert source.status_code == 200, source.text
    body = source.json()
    assert (body['suggestion'], body['changes'], body['memory_notes']) == (
        'Be clearer.',
        {'instructions': 'Gather evidence first.'},
        [],
    )
    assert (body['instructions'], body['revision']) == ('Verify every change.', before['revision'])
    assert 'servers' not in body and 'system_prompt' not in body
    applied = apply_review(client, 'operations', review, body, instructions='Gather evidence first.')
    assert applied.status_code == 200, applied.text
    assert applied.json() == {'agent_id': 'operations', 'saved': [], 'refused': []}
    after = get_agent_config('operations', conninfo=store.conninfo)
    assert after['instructions'] == 'Gather evidence first.'
    assert without(after, 'instructions') == without(before, 'instructions')
    assert after['revision'] == before['revision'] + 1
    again = apply_review(client, 'operations', review, body, instructions='Older text.')
    assert again.status_code == 409 and again.json()['error']['message'] == AGENT_CHANGED
    assert applied_audit(store) == [
        {
            'agent_id': 'operations',
            'source_agent_id': 'operations',
            'review_id': review,
            'target': 'agent',
            'instructions_changed': True,
            'memory_saved': [],
            'memory_refused': [],
        }
    ]


def test_the_system_prompt_is_never_applied(store, saved_agent):
    client = saved_agent[0]
    before = get_agent_config('operations', conninfo=store.conninfo)
    review = saved_review(store, 'operations', changes())
    body = client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS).json()
    refused = apply_review(client, 'operations', review, body, system_prompt='Answer from evidence.')
    assert refused.status_code == 400  # an unknown field (request validation is 400 in this API)
    assert sections(get_agent_config('operations', conninfo=store.conninfo)) == sections(before)
    assert client.get(f'/api/agents/code/reviews/{review}', headers=HEADERS).status_code == 404  # another agent's


def test_source_marks_notes_whose_name_already_exists(store, saved_agent):
    client = saved_agent[0]
    taken = {'name': 'payments/deploy', 'content': 'Deploy with make release.', 'metadata': {}}
    review = saved_review(store, 'operations', changes(), notes=[NOTE, taken])
    with store.connect() as conn:
        existing = memory.create(
            conn, store.project(conn, 'shop'), 'payments/deploy', {}, 'Use the pipeline.', memory.Author(), None, None
        )
    body = client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS).json()
    assert body['memory_notes'] == [
        {**NOTE, 'exists': False, 'memory_id': None, 'current_content': None},
        {**taken, 'exists': True, 'memory_id': str(existing['id']), 'current_content': 'Use the pipeline.'},
    ]


def test_notes_only_are_saved_without_the_agent_check(store, saved_agent, monkeypatch):
    client = saved_agent[0]
    monkeypatch.setattr(agent_reviews, 'current_model', lambda conn, project: 'model')
    monkeypatch.setattr(agent_reviews, 'embed_text', lambda model, text: ([1.0, 0.0, 0.0], f'{model}-profile'))
    before = get_agent_config('operations', conninfo=store.conninfo)
    review = saved_review(store, 'operations', changes(), notes=[NOTE])
    body = client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS).json()
    with store.connect() as conn:  # the agent changed meanwhile: notes do not modify it
        conn.execute("UPDATE ka.agents SET instructions='Saved meanwhile.' WHERE id='operations'")
        session = conn.execute('SELECT session_id FROM ka.agent_session_reviews WHERE id=%s', (review,)).fetchone()
    applied = apply_review(client, 'operations', review, {**body, 'fingerprint': '0' * 64}, notes=[NOTE])
    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert result['agent_id'] == 'operations' and result['refused'] == []
    assert [row['name'] for row in result['saved']] == ['payments/retries']
    after = get_agent_config('operations', conninfo=store.conninfo)
    assert (after['instructions'], after['revision']) == ('Saved meanwhile.', before['revision'])
    with store.connect() as conn:
        entry = conn.execute(
            """SELECT m.id,m.metadata,m.content,m.created_by_session,m.created_by_actor,m.created_by_agent,
            m.embedding_profile,p.email FROM ka.memories m JOIN ka.principals p ON p.id=m.created_by_actor"""
        ).fetchone()
        event = conn.execute("SELECT type,payload FROM ka.events WHERE source='memory'").fetchone()
    assert str(entry['id']) == result['saved'][0]['id']
    assert entry['metadata'] == {'service': 'payments', 'source': 'review'}
    assert entry['content'] == NOTE['content'] and entry['embedding_profile'] == 'model-profile'
    assert (entry['created_by_session'], entry['created_by_agent']) == (session['session_id'], None)
    assert entry['email'] == 'admin@example.test'  # the person who applied it
    assert event['type'] == 'memory.saved'
    assert event['payload']['session_id'] == str(session['session_id']) and event['payload']['actor'] is not None
    assert applied_audit(store) == [
        {
            'agent_id': 'operations',
            'source_agent_id': 'operations',
            'review_id': review,
            'target': 'agent',
            'instructions_changed': False,
            'memory_saved': ['payments/retries'],
            'memory_refused': [],
        }
    ]


def test_an_existing_name_is_refused_while_the_rest_applies(store, saved_agent):
    client = saved_agent[0]
    other = {'name': 'payments/timeouts', 'content': 'Timeouts are 30 seconds.', 'metadata': {}}
    review = saved_review(store, 'operations', changes(instructions='Be brief.'), notes=[NOTE, other])
    with store.connect() as conn:
        memory.create(conn, store.project(conn, 'shop'), NOTE['name'], {}, 'Older fact.', memory.Author(), None, None)
    body = client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS).json()
    applied = apply_review(client, 'operations', review, body, instructions='Be brief.', notes=[NOTE, other])
    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert [row['name'] for row in result['saved']] == ['payments/timeouts']
    assert result['refused'] == [{'name': NOTE['name'], 'message': f'An entry named {NOTE["name"]} already exists.'}]
    assert get_agent_config('operations', conninfo=store.conninfo)['instructions'] == 'Be brief.'
    with store.connect() as conn:
        kept = conn.execute('SELECT content FROM ka.memories WHERE name=%s', (NOTE['name'],)).fetchone()
    assert kept['content'] == 'Older fact.'
    details = applied_audit(store)[0]
    assert (details['instructions_changed'], details['memory_saved'], details['memory_refused']) == (
        True,
        ['payments/timeouts'],
        [NOTE['name']],
    )


def test_instructions_with_notes_keep_the_agent_check(store, saved_agent):
    client = saved_agent[0]
    review = saved_review(store, 'operations', changes(), notes=[NOTE])
    body = client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS).json()
    stale = apply_review(
        client, 'operations', review, {**body, 'revision': body['revision'] + 1}, instructions='x', notes=[NOTE]
    )
    assert stale.status_code == 409 and stale.json()['error']['message'] == AGENT_CHANGED
    with store.connect() as conn:  # nothing saved
        assert conn.execute('SELECT count(*) AS n FROM ka.memories').fetchone()['n'] == 0


def test_invalid_notes_in_the_request_are_refused(store, saved_agent):
    client = saved_agent[0]
    review = saved_review(store, 'operations', changes())
    body = client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS).json()
    for notes in ([NOTE] * 11, [{**NOTE, 'name': 'Bad Name'}]):
        assert apply_review(client, 'operations', review, body, notes=notes).status_code == 400
    full = {**NOTE, 'metadata': {f'k{n}': 'v' for n in range(20)}}  # source: review would make it 21 keys
    assert apply_review(client, 'operations', review, body, notes=[full]).status_code == 400
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.memories').fetchone()['n'] == 0


def test_nothing_to_apply_is_refused(store, saved_agent):
    client = saved_agent[0]
    review = saved_review(store, 'operations', changes())
    body = client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS).json()
    empty = apply_review(client, 'operations', review, body)
    assert empty.status_code == 400 and empty.json()['error']['message'] == NOTHING_TO_APPLY
    assert applied_audit(store) == []


def test_a_suggestion_applied_to_a_new_clone_leaves_the_agent_as_it_was(store, saved_agent):
    client = saved_agent[0]
    before = get_agent_config('operations', conninfo=store.conninfo)
    review = saved_review(store, 'operations', changes(instructions='Answer from evidence.'), notes=[NOTE])
    body = client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS).json()
    applied = apply_review(
        client, 'operations', review, body, target='clone', instructions='Answer from evidence.', notes=[NOTE]
    )
    assert applied.status_code == 200, applied.text
    clone_id = applied.json()['agent_id']
    assert clone_id != 'operations'
    assert sections(get_agent_config('operations', conninfo=store.conninfo)) == sections(before)
    copied = get_agent_config(clone_id, conninfo=store.conninfo)
    assert copied['instructions'] == 'Answer from evidence.'
    assert without(copied, 'instructions') == without(before, 'instructions')
    assert client.get(f'/api/agents/{clone_id}', headers=HEADERS).json()['cloned_from'] == 'operations'
    with store.connect() as conn:
        audit = conn.execute("""SELECT action,details FROM ka.audit_events
            WHERE action IN ('agent.cloned','agent.suggestion.applied') ORDER BY id""").fetchall()
    assert [(row['action'], row['details']) for row in audit] == [
        ('agent.cloned', {'agent_id': clone_id, 'cloned_from': 'operations'}),
        (
            'agent.suggestion.applied',
            {
                'agent_id': clone_id,
                'source_agent_id': 'operations',
                'review_id': review,
                'target': 'clone',
                'instructions_changed': True,
                'memory_saved': [NOTE['name']],
                'memory_refused': [],
            },
        ),
    ]


def test_a_suggestion_from_a_narrowed_chat_never_narrows_the_agent(store, saved_agent):
    client = saved_agent[0]
    before = get_agent_config('operations', conninfo=store.conninfo)
    review = saved_review(store, 'operations', changes(instructions='Be brief.'), agent_config=NARROWED)
    body = client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS).json()
    assert apply_review(client, 'operations', review, body, instructions='Be brief.').status_code == 200
    after = get_agent_config('operations', conninfo=store.conninfo)
    assert without(after, 'instructions') == without(before, 'instructions')
    assert of(after, 'code_project') == of(before, 'code_project')


def test_a_clone_name_taken_concurrently_is_a_conflict(store, saved_agent, monkeypatch):
    client = saved_agent[0]
    review = saved_review(store, 'operations', changes())
    body = client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS).json()

    def taken(*_):
        raise psycopg.errors.UniqueViolation('duplicate name')

    monkeypatch.setattr(agent_reviews, 'clone', taken)
    conflict = apply_review(client, 'operations', review, body, target='clone', instructions='Be brief.')
    assert conflict.status_code == 409 and conflict.json()['error']['message'] == NAME_TAKEN


def test_invalid_texts_are_refused(store, saved_agent):
    client = saved_agent[0]
    before = get_agent_config('operations', conninfo=store.conninfo)
    review = saved_review(store, 'operations', changes())
    body = client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS).json()
    refused = apply_review(client, 'operations', review, body, instructions='Be\x00brief.')
    assert refused.status_code == 400 and refused.json()['error']['message'] == INVALID_TEXT
    assert apply_review(client, 'operations', review, body, instructions='x' * 40001).status_code == 400
    assert sections(get_agent_config('operations', conninfo=store.conninfo)) == sections(before)


def test_instructions_saved_elsewhere_make_the_suggestion_stale(store, saved_agent):
    client = saved_agent[0]
    review = saved_review(store, 'operations', changes())
    body = client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS).json()
    with store.connect() as conn:  # saved another way, agents.revision staying
        conn.execute("UPDATE ka.agents SET instructions='Saved meanwhile.' WHERE id='operations'")
    assert get_agent_config('operations', conninfo=store.conninfo)['revision'] == body['revision']
    stale = apply_review(client, 'operations', review, body, instructions='Be brief.')
    assert stale.status_code == 409 and stale.json()['error']['message'] == AGENT_CHANGED
    assert get_agent_config('operations', conninfo=store.conninfo)['instructions'] == 'Saved meanwhile.'


def test_a_deleted_agent_has_no_suggestions(store, saved_agent):
    client = saved_agent[0]
    review = saved_review(store, 'operations', changes())
    body = client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS).json()
    revision = client.get('/api/agents/operations', headers=HEADERS).json()['revision']
    assert (
        client.request(
            'DELETE',
            '/api/agents/operations',
            headers=HEADERS,
            json={'revision': revision, 'confirmed': True, 'with_history': True},
        ).status_code
        == 204
    )
    for response in (
        client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS),
        apply_review(client, 'operations', review, body, instructions='Be brief.'),
        apply_review(client, 'operations', review, body, notes=[NOTE]),
    ):
        assert response.status_code == 404 and response.json()['error']['message'] == REVIEW_NOT_FOUND
