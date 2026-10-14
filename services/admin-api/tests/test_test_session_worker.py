"""agent-session-worker runs Test sessions like Generate documents sessions — turns, comments while the agent works,
Stop — but a Test session has no draft, a completed turn's end event carries its grounding (redacted), and every model
call adds its tokens, reasoning tokens and one model call to the session."""

import asyncio
import json

import pytest
from fastapi.encoders import jsonable_encoder

from native_fakes import FakeAgent, call, hang, llm, message, tool
from session_rows import add_test_session
from knowledge_admin.sessions import run_record
from knowledge_admin.knowledge_agents import native_sessions
from knowledge_admin.knowledge_agents import session_worker as shared
from knowledge_admin.knowledge_agents.config import get_agent_config
from run_helpers import events, post, wait_for
from test_generations import native_agent, sessions  # noqa: F401  (fixture)

KEY = 'bedrock-secret-token'  # the Claude connection's key, decrypted for each turn
SOURCES = {
    'excerpts': [
        {'n': 1, 'path': 'retries.md', 'collection': 'payments', 'text': 'Three retries.'},
        {'n': 2, 'path': 'timeouts.md', 'collection': 'payments', 'text': 'Thirty seconds.'},
    ],
    'searches': 1,
    'empty_searches': 0,
    'reported': None,
}


@pytest.fixture
def tested(sessions, monkeypatch):
    """A native agent with the Claude model and a Test session of it whose first message is queued."""
    monkeypatch.setattr(run_record, '_store', sessions.store)
    agent_id = native_agent(sessions.client, 'Native tester', model_id=sessions.claude)
    config = jsonable_encoder(get_agent_config(agent_id, conninfo=sessions.store.conninfo))
    with sessions.store.connect() as conn:
        project = sessions.store.project(conn, 'shop')
        actor = sessions.store.actor(conn, 'admin-id', 'admin@example.test')
        sid = add_test_session(
            conn, project, actor, agent_id, sessions.claude, config=config, message='How many retries?'
        )
    return sessions, sid


def claimed(store) -> dict:
    [row] = shared.claim(store, 'worker-1', [], 4)
    return row


def test_a_test_session_turn_leaves_no_draft_and_records_grounding_and_usage(tested):
    sessions, sid = tested
    store, url = sessions.store, f'/api/sessions/{sid}'
    started, gate, hung, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event(), []
    fake = FakeAgent(
        [
            {
                'steps': [
                    llm(
                        call('c1', 'search_documents', {'query': 'retries'}),
                        input_tokens=100,
                        output_tokens=10,
                        cached=40,
                        reasoning=6,
                    ),
                    tool('c1', 'search_documents', '[1] Three retries.', started=started, gate=gate),
                    llm(message('Three retries [1].'), input_tokens=120, output_tokens=20, reasoning=4),
                ],
                'answer': 'Three retries [1].',
                'grounding': SOURCES,
            },
            {'steps': [hang('model', hung, cancelled)], 'answer': 'never reached'},
        ]
    )

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, poll=0.02).run())
        await asyncio.wait_for(started.wait(), 5)
        await post(sessions, url + '/messages', {'text': 'Cite the runbook.'})  # reaches the turn's next model call
        gate.set()
        first = await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 1)
        await post(sessions, url + '/messages', {'text': 'And timeouts?'})
        await asyncio.wait_for(hung.wait(), 5)
        assert (await post(sessions, url + '/stop')).status_code == 202
        stopped = await wait_for(store, sid, lambda s: s['status'] == 'stopped')
        await post(sessions, url + '/close')
        await asyncio.wait_for(runner, 5)
        return first, stopped

    first, stopped = asyncio.run(scenario())
    assert (first['purpose'], first['draft'], first['draft_turn']) == ('test', None, None)
    assert (
        first['input_tokens'],
        first['output_tokens'],
        first['cached_tokens'],
        first['reasoning_tokens'],
        first['model_calls'],
    ) == (220, 30, 40, 10, 2)
    assert (stopped['draft'], stopped['status_message'], cancelled) == (None, shared.STOPPED, ['model'])
    assert fake.calls[0]['inputs'][1][-1] == {'role': 'user', 'content': native_sessions.COMMENT + 'Cite the runbook.'}
    recorded = events(store, sid)
    turn1 = [e for e in recorded if e['run_key'] == f'cc:{sid}:1']
    assert turn1[0]['payload']['kind'] == 'test_session'
    assert [e['payload'].get('comment', False) for e in turn1 if e['kind'] == 'input'] == [False, True]
    assert [e['payload']['reasoning_tokens'] for e in turn1 if e['kind'] == 'usage'] == [6, 4]
    assert turn1[-1]['payload'] == {
        'status': 'completed',
        'grounding': {
            'sources': [{'n': 1, 'path': 'payments/retries.md', 'heading_path': 'payments', 'anchor': None}],
            'not_covered': False,
            'results': 2,
            'reason': None,
            'near_misses': [
                {'path': 'payments/retries.md', 'heading_path': 'payments'},
                {'path': 'payments/timeouts.md', 'heading_path': 'payments'},
            ],
        },
    }
    assert [e['payload'] for e in recorded if e['run_key'] == f'cc:{sid}:2' and e['kind'] == 'end'] == [
        {'status': 'stopped'}
    ]  # a stopped turn has no answer, so no grounding


def test_grounding_in_the_end_event_is_redacted(tested):
    sessions, sid = tested
    store, url = sessions.store, f'/api/sessions/{sid}'
    leaked = {**SOURCES, 'excerpts': [{'n': 1, 'path': f'keys/{KEY}.md', 'collection': 'payments', 'text': KEY}]}
    fake = FakeAgent(
        [
            {
                'steps': [llm(message('See [1].'), input_tokens=3, output_tokens=2)],
                'answer': 'See [1].',
                'grounding': leaked,
            },
            {'steps': [llm(message('Nothing to cite.'))], 'answer': 'Nothing to cite.'},
        ]
    )

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, poll=0.02).run())
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 1)
        await post(sessions, url + '/messages', {'text': 'Anything else?'})
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 2)
        await post(sessions, url + '/close')
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())
    first, second = [e['payload'] for e in events(store, sid) if e['kind'] == 'end']
    assert KEY not in json.dumps(first) and first['grounding']['sources'][0]['n'] == 1
    assert second == {'status': 'completed'}  # an ungrounded turn has no grounding


def test_a_session_pinned_before_claude_code_was_removed_still_runs(tested):
    """Configs pinned before 0084 may keep runtime and claude_code_tools keys; the worker ignores them and runs."""
    sessions, sid = tested
    store, url = sessions.store, f'/api/sessions/{sid}'
    with store.connect() as conn:
        conn.execute(
            """UPDATE ka.agent_sessions SET agent_config=agent_config
            || '{"runtime": "claude_code", "claude_code_tools": ["Read", "Grep"]}' WHERE id=%s""",
            (sid,),
        )
    fake = FakeAgent([{'steps': [llm(message('Three retries.'))], 'answer': 'Three retries.'}])

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, poll=0.02).run())
        done = await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 1)
        await post(sessions, url + '/close')
        await asyncio.wait_for(runner, 5)
        return done

    assert asyncio.run(scenario())['status_message'] == ''
    assert fake.calls[0]['config']['claude_code_tools'] == ['Read', 'Grep']
    assert [e['payload'] for e in events(store, sid) if e['kind'] == 'end'] == [{'status': 'completed'}]


def test_an_event_session_turn_leaves_no_draft(sessions, monkeypatch):
    monkeypatch.setattr(run_record, '_store', sessions.store)
    agent_id = native_agent(sessions.client, 'Event agent', model_id=sessions.claude)
    config = jsonable_encoder(get_agent_config(agent_id, conninfo=sessions.store.conninfo))
    with sessions.store.connect() as conn:
        project = sessions.store.project(conn, 'shop')
        actor = sessions.store.actor(conn, 'admin-id', 'admin@example.test')
        sid = add_test_session(
            conn, project, actor, agent_id, sessions.claude, config=config, message='Triage DEMO-1', purpose='event'
        )
    store = sessions.store
    fake = FakeAgent([{'steps': [llm(message('Triaged.'))], 'answer': 'Triaged.'}])

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, poll=0.02).run())
        done = await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 1)
        await post(sessions, f'/api/sessions/{sid}/close')
        await asyncio.wait_for(runner, 5)
        return done

    done = asyncio.run(scenario())
    assert (done['purpose'], done['draft'], done['draft_turn']) == ('event', None, None)
    with store.connect() as conn:
        start = conn.execute(
            "SELECT payload FROM ka.agent_run_events WHERE run_key=%s AND kind='start'", (shared.run_key(sid, 1),)
        ).fetchone()['payload']
    assert start['kind'] == 'event_session'
