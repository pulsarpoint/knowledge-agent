"""Suggest improvements for a Test session: the reviewer reads the conversation built from the session's run events
and the pinned configuration; the suggestion is stored with its turn, and Review and apply finds it."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agents import ModelBehaviorError

from access_helpers import entry
from knowledge_admin.sessions.review import NO_RESPONSE, NO_TURNS, NOT_A_CONVERSATION, REVIEW_BUSY
from test_generations import record
from test_repositories import HEADERS
from test_test_sessions import start, tester  # noqa: F401  (fixture)

PROPOSAL = {
    'suggestion': 'Cite the runbook.',
    'changes': {'instructions': 'Cite sources.'},
    'memory_notes': [{'name': 'runbooks/retries', 'content': 'The runbook sets three retries.', 'metadata': {}}],
}
TURNS = [
    {
        'turn': 1,
        'input': 'How many retries?',
        'comments': ['Cite the runbook.'],
        'tools': [{'name': 'search_documents', 'summary': 'retries'}],
        'answer': 'Three retries [1].',
        'error': '',
        'status': 'completed',
    },
    {
        'turn': 2,
        'input': 'And timeouts?',
        'comments': [],
        'tools': [],
        'answer': '',
        'error': 'Agent execution failed (RuntimeError).',
        'status': 'failed',
    },
]


@pytest.fixture
def chat(tester):
    """A Test session with two finished turns, recorded as agent-session-worker records them."""
    t = tester
    sid = start(t, 'How many retries?')['id']
    with t.store.connect() as conn:
        for turn, seq, kind, payload in (
            (1, 1, 'start', {'kind': 'test_session', 'turn': 1}),
            (1, 2, 'input', {'task': 'How many retries?'}),
            (1, 3, 'message', {'text': 'Let me search.'}),
            (
                1,
                4,
                'tool_call',
                {'name': 'search_documents', 'call_id': 'c1', 'summary': 'retries', 'arguments': {'query': 'retries'}},
            ),
            (
                1,
                5,
                'tool_result',
                {
                    'name': 'search_documents',
                    'call_id': 'c1',
                    'preview': '[1] Three retries.',
                    'output': '[1] Three retries.',
                    'is_error': False,
                },
            ),
            (1, 6, 'input', {'task': 'Cite the runbook.', 'comment': True}),
            (1, 7, 'message', {'text': 'Three retries [1].'}),
            (1, 8, 'end', {'status': 'completed'}),
            (2, 1, 'start', {'kind': 'test_session', 'turn': 2}),
            (2, 2, 'input', {'task': 'And timeouts?'}),
            (2, 3, 'error', {'type': 'RuntimeError', 'message': 'Agent execution failed (RuntimeError).'}),
            (2, 4, 'end', {'status': 'failed'}),
        ):
            record(conn, sid, turn, seq, kind, payload)
        conn.execute("UPDATE ka.agent_sessions SET status='failed',turns=2 WHERE id=%s", (sid,))
    t.session, t.review = sid, f'/api/sessions/{sid}/review'
    return t


def test_feedback_reviews_the_conversation_built_from_the_events(chat, monkeypatch):
    calls = []

    async def review(config, task, **kwargs):
        calls.append((config, json.loads(task), kwargs))
        return SimpleNamespace(final_output=kwargs['output_type'].model_validate(PROPOSAL))

    monkeypatch.setattr('knowledge_admin.sessions.review.agent', review)
    response = chat.client.post(chat.review, headers=HEADERS, json={'comment': 'Cite your sources.', 'turn': 1})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert (saved['comment'], saved['suggestion'], saved['response_turn']) == (
        'Cite your sources.',
        'Cite the runbook.',
        1,
    )
    assert saved['changes'] == {'instructions': 'Cite sources.'}
    assert saved['memory_notes'] == PROPOSAL['memory_notes']
    config, context, kwargs = calls[0]
    assert config['id'] == 'agent-tuning'
    assert config['access']['resources'] == [] and 'grounded' not in config
    assert kwargs['timeout_seconds'] == 240 and kwargs['credentials'] == {}  # cleared when the review ends
    assert kwargs['tools'] is False  # an internal LLM call: no tools
    assert (context['conversation'], context['turn'], context['comment']) == (TURNS, 1, 'Cite your sources.')
    assert context['agent_config']['id'] == chat.agent and 'output_schema' in context
    assert 'tester-model-secret' not in json.dumps(context)
    detail = chat.client.get(f'/api/sessions/{chat.session}', headers=HEADERS).json()
    assert [(row['id'], row['response_turn']) for row in detail['reviews']] == [(saved['id'], 1)]
    source = chat.client.get(f'/api/agents/{chat.agent}/reviews/{saved["id"]}', headers=HEADERS)
    assert source.status_code == 200 and source.json()['changes']['instructions'] == 'Cite sources.'  # Review and apply
    assert chat.client.get(f'/api/agents/code/reviews/{saved["id"]}', headers=HEADERS).status_code == 404


def test_the_reviewer_never_reads_a_connection(chat, monkeypatch):
    """The pinned access entries reach the reviewer model with their public fields only (access.listing)."""
    server = {
        **entry(
            'server',
            uuid4(),
            'web-1',
            hostname='10.9.9.9',
            port=2222,
            username='opsuser',
            allow_sudo=True,
            known_hosts='web-1 ssh-ed25519 AAAA',
            enabled=True,
            ssh_allowed=True,
            private_key_ref={'table': 'ka.credentials', 'id': str(uuid4()), 'aad': 'x'},
        ),
        'source': 'given',
    }
    mcp = {
        **entry('mcp_server', uuid4(), 'Docs', url='http://mcp.example/mcp', enabled=True, bearer_token_ref=None),
        'source': 'given',
    }
    with chat.store.connect() as conn:
        conn.execute(
            """UPDATE ka.agent_sessions SET agent_config=jsonb_set(agent_config,'{access,resources}',
            (agent_config->'access'->'resources') || %s::jsonb) WHERE id=%s""",
            (json.dumps([server, mcp]), chat.session),
        )
    calls = []

    async def review(config, task, **kwargs):
        calls.append(json.loads(task))
        return SimpleNamespace(final_output=kwargs['output_type'].model_validate(PROPOSAL))

    monkeypatch.setattr('knowledge_admin.sessions.review.agent', review)
    response = chat.client.post(chat.review, headers=HEADERS, json={'comment': 'Shorter.'})
    assert response.status_code == 200, response.text
    [context] = calls
    shown = {item['id']: item for item in context['agent_config']['access']['resources']}
    assert shown[server['id']] == {key: server[key] for key in ('type', 'id', 'name', 'description_short', 'source')}
    assert set(shown[mcp['id']]) == {'type', 'id', 'name', 'description_short', 'source'}
    for secret in ('10.9.9.9', 'opsuser', 'AAAA', 'mcp.example', 'private_key_ref', 'ka.credentials'):
        assert secret not in json.dumps(context)


def test_a_secret_in_the_feedback_is_not_stored(chat, monkeypatch):
    async def review(config, task, **kwargs):
        return SimpleNamespace(final_output=kwargs['output_type'].model_validate(PROPOSAL))

    monkeypatch.setattr('knowledge_admin.sessions.review.agent', review)
    response = chat.client.post(chat.review, headers=HEADERS, json={'comment': 'The key is tester-model-secret.'})
    assert response.status_code == 200, response.text
    assert 'tester-model-secret' not in response.json()['comment']
    with chat.store.connect() as conn:
        [row] = conn.execute(
            'SELECT comment,context FROM ka.agent_session_reviews WHERE session_id=%s', (chat.session,)
        ).fetchall()
    assert row['comment'].startswith('The key is ') and 'tester-model-secret' not in json.dumps(row, default=str)


def test_feedback_is_refused_for_other_sessions_turns_and_while_the_agent_works(chat, monkeypatch):
    async def never(*args, **kwargs):
        pytest.fail('No review may run')

    monkeypatch.setattr('knowledge_admin.sessions.review.agent', never)

    def post(path, body, status):
        response = chat.client.post(path, headers=HEADERS, json=body)
        assert response.status_code == status, response.text
        return response.json()['error']['message']

    assert post(chat.review, {'comment': 'x', 'turn': 3}, 404) == NO_RESPONSE
    assert post(f'/api/sessions/{uuid4()}/review', {'comment': 'x'}, 404) == 'Session not found.'
    post(chat.review, {'comment': ' '}, 400)
    post(chat.review, {'comment': 'x', 'turn': 0}, 400)
    assert chat.client.post(chat.review, json={'comment': 'x'}).status_code == 403
    with chat.store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET status='working' WHERE id=%s", (chat.session,))
        documents = conn.execute(
            """INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,
            prompt_snapshot,context_snapshot,document_name,folder,agent_config) SELECT %s,project_id,agent_id,actor_id,model_id,'Docs',
            '{}','{}','Docs','docs',agent_config FROM ka.agent_sessions WHERE id=%s RETURNING id""",
            (uuid4(), chat.session),
        ).fetchone()['id']
    assert post(chat.review, {'comment': 'x'}, 409) == REVIEW_BUSY
    assert post(f'/api/sessions/{documents}/review', {'comment': 'x'}, 400) == NOT_A_CONVERSATION
    fresh = start(chat)['id']
    with chat.store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET status='waiting' WHERE id=%s", (fresh,))
    assert post(f'/api/sessions/{fresh}/review', {'comment': 'x'}, 409) == NO_TURNS
    with chat.store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_session_reviews').fetchone()['n'] == 0


def test_invalid_tuning_output_is_corrected_once_before_saving(chat, monkeypatch):
    calls = []

    async def review(config, task, **kwargs):
        calls.append(json.loads(task))
        if len(calls) == 1:
            raise ModelBehaviorError('Invalid model output')
        return SimpleNamespace(final_output=kwargs['output_type'].model_validate(PROPOSAL))

    monkeypatch.setattr('knowledge_admin.sessions.review.agent', review)
    result = chat.client.post(chat.review, headers=HEADERS, json={'comment': 'Be concise.'})
    assert result.status_code == 200, result.text
    assert len(calls) == 2 and 'correction' in calls[1] and 'output_schema' in calls[0]
    assert result.json()['response_turn'] is None and result.json()['changes']['instructions'] == 'Cite sources.'


def test_the_tuning_prompt_proposes_notes_and_instructions_never_the_system_prompt():
    from knowledge_admin.sessions.review import TUNING_PROMPT

    assert 'memory_notes' in TUNING_PROMPT and 'instructions' in TUNING_PROMPT
    assert 'do not claim an automatic memory store exists' not in TUNING_PROMPT
    assert 'system prompt is out of scope' in TUNING_PROMPT
