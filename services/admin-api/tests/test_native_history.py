"""Native session history through the Agents SDK Session interface: the SDK reads the saved history and buffers the
turn's items; only a committed successful turn reaches the session row."""
import asyncio
import json

import pytest
from agents import RunHooks
from agents.memory import Session
from fastapi.encoders import jsonable_encoder

from knowledge_admin.agents import agent
from knowledge_admin.knowledge_agents.history import HistorySession
from test_claude_code_sessions import sessions, start  # noqa: F401  (fixture)
from test_sdk_agent import config, responses_api  # noqa: F401  (fixtures)

SECRET = {'model-key': 'fixture-api-secret'}


def test_history_session_buffers_until_commit_and_never_changes_saved_items():
    saved = [{'role': 'user', 'content': 'First'}]
    history = HistorySession('s1', saved)
    added = [{'role': 'assistant', 'content': 'Hello'}, {'role': 'user', 'content': 'Next'}]

    async def scenario():
        assert await history.get_items() == saved
        await history.add_items(added)
        assert await history.get_items() == [*saved, *added]
        assert await history.get_items(limit=1) == [added[-1]]
        assert await history.get_items(limit=0) == []
        assert await history.pop_item() == added[-1]
        await history.clear_session()  # this turn's items only
        assert await history.get_items() == saved
        assert await history.pop_item() is None  # the saved history is never popped

    asyncio.run(scenario())
    assert isinstance(history, Session) and history.session_settings is None and history.session_id == 's1'
    assert saved == [{'role': 'user', 'content': 'First'}]  # the caller's list is not changed


def test_commit_appends_scrubbed_items_in_the_callers_transaction_and_drop_discards(sessions):
    store = sessions.store
    sid = start(sessions)['id']
    with store.connect() as conn:
        history = HistorySession.load(conn, sid)
    assert (history.session_id, history.saved, history.pending) == (sid, [], [])
    asyncio.run(history.add_items([{'role': 'user', 'content': 'key fixture-api-secret\x00 here'},
                                   {'type': 'function_call_output', 'call_id': 'c1', 'output': 'fixture-api-secret'}]))
    with store.connect() as conn:
        history.commit(conn, ['fixture-api-secret'])
        conn.rollback()  # the caller's transaction decides
    with store.connect() as conn:
        assert HistorySession.load(conn, sid).saved == []
    with store.connect() as conn:
        history.commit(conn, ['fixture-api-secret', ''])
    with store.connect() as conn:
        assert HistorySession.load(conn, sid).saved == [
            {'role': 'user', 'content': 'key [redacted] here'},
            {'type': 'function_call_output', 'call_id': 'c1', 'output': '[redacted]'}]
    history.drop()
    assert history.pending == []
    with store.connect() as conn:
        with pytest.raises(LookupError):
            HistorySession.load(conn, '00000000-0000-0000-0000-000000000000')


def test_agent_reads_and_buffers_the_conversation_through_the_sdk_session(config, responses_api):
    _, requests = responses_api
    calls = []

    class Hooks(RunHooks):
        async def on_llm_end(self, context, agent, response):
            calls.append(response.usage.total_tokens)

    first = HistorySession('s1', [])
    result = asyncio.run(agent(config, 'Remember the test word apricot.', credentials=SECRET, session=first,
                               hooks=Hooks(), stream=False))
    assert result.final_output == 'Configured agent completed the task.' and calls == [30]
    assert first.saved == []  # nothing is saved by the run itself
    turn = json.dumps(jsonable_encoder(first.pending))  # what commit() stores
    assert 'Remember the test word apricot.' in turn and 'Configured agent completed the task.' in turn
    second = HistorySession('s1', json.loads(turn))
    asyncio.run(agent(config, 'What word did I give you?', credentials=SECRET, session=second, stream=False))
    resumed = json.dumps(requests[1]['body'])
    assert 'Remember the test word apricot.' in resumed and 'What word did I give you?' in resumed
    assert 'apricot' not in json.dumps(jsonable_encoder(second.pending))  # only the new turn is buffered
    for invalid in ({'history': [{'role': 'user', 'content': 'Earlier'}], 'stream': False}, {'stream': True}):
        with pytest.raises(ValueError, match='without history and without streaming'):
            asyncio.run(agent(config, 'Again.', credentials=SECRET, session=HistorySession('s1', []), **invalid))
    assert len(requests) == 2  # refused before the SDK started
