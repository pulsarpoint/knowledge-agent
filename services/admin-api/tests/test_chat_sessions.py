"""Run agent on the Agents page: POST /api/sessions with a saved agent and purpose 'chat' starts a chat session (0122).
It is not one of the agent's Test tab sessions, may be reviewed, and its turns are Chat runs."""

import asyncio
from pathlib import Path

import psycopg
import pytest
from fastapi.encoders import jsonable_encoder

from knowledge_admin.knowledge_agents import native_sessions
from knowledge_admin.knowledge_agents import session_worker as shared
from knowledge_admin.knowledge_agents.config import get_agent_config
from knowledge_admin.sessions import run_record
from knowledge_admin.sessions.review import NO_TURNS
from native_fakes import FakeAgent, llm, message
from run_helpers import post, wait_for
from session_rows import a_model, add_test_session
from test_generations import native_agent, sessions  # noqa: F401  (fixture)
from test_repositories import HEADERS
from test_sessions_saved_agent import agent, start  # noqa: F401  (fixture)
from test_sessions_world import ask  # noqa: F401  (fixture)
from test_test_session_worker import claimed

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0122_chat_sessions.up.sql', '0122_chat_sessions.down.sql'


def migrate(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def test_purpose_chat_starts_a_chat_session_and_omitted_keeps_test(agent):
    chat = start(agent.client, agent_id=agent.id, purpose='chat', resources={'server_ids': [agent.web]})
    assert chat.status_code == 201, chat.text
    assert (chat.json()['purpose'], chat.json()['agent_id']) == ('chat', agent.id)
    assert chat.json()['config']['id'] == agent.id  # its pinned configuration is shown, as a Test session's
    test = start(agent.client, agent_id=agent.id, resources={'server_ids': [agent.web]})
    assert test.json()['purpose'] == 'test'


def test_purpose_needs_a_saved_agent_and_no_start_and_only_chat(agent):
    for body in (
        {'purpose': 'chat', 'started_from': {'server_id': agent.web}, 'model_id': agent.model, 'system_prompt': 'Hi'},
        {'purpose': 'chat', 'agent_id': agent.id, 'started_from': {'server_id': agent.web}},
        {'purpose': 'test', 'agent_id': agent.id},
        {'purpose': 'event', 'agent_id': agent.id},
    ):
        for path in ('/preview', ''):
            response = start(agent.client, path, **body)
            assert response.status_code == 400, (body, response.text)
    response = start(agent.client, purpose='chat', started_from={'server_id': agent.web})
    assert response.json()['error']['message'].endswith('Only a saved agent chat takes a purpose.')


def test_the_test_tab_lists_no_chat_sessions_and_a_chat_may_be_reviewed(agent):
    chat = start(agent.client, agent_id=agent.id, purpose='chat', resources={'server_ids': [agent.web]}).json()
    test = start(agent.client, agent_id=agent.id, resources={'server_ids': [agent.web]}).json()
    listed = agent.client.get(f'/api/sessions?agent_id={agent.id}', headers=HEADERS).json()['sessions']
    assert [item['id'] for item in listed] == [test['id']]
    chats = agent.client.get('/api/sessions?purpose=chat', headers=HEADERS).json()['sessions']
    assert [item['id'] for item in chats] == [chat['id']]
    history = agent.client.get(f'/api/agents/{agent.id}/history', headers=HEADERS).json()
    assert (history['test_chats'], history['chats']) == (1, 1)  # a chat is history too: deleting the agent takes it
    agent.client.post(f'/api/sessions/{chat["id"]}/close', headers=HEADERS)
    review = agent.client.post(f'/api/sessions/{chat["id"]}/review', headers=HEADERS, json={'comment': 'Shorter.'})
    assert (review.status_code, review.json()['error']['message']) == (409, NO_TURNS)  # not "Test sessions only"


def test_a_chat_session_turn_is_a_chat_run(sessions, monkeypatch):
    monkeypatch.setattr(run_record, '_store', sessions.store)
    agent_id = native_agent(sessions.client, 'Chat agent', model_id=sessions.claude)
    config = jsonable_encoder(get_agent_config(agent_id, conninfo=sessions.store.conninfo))
    with sessions.store.connect() as conn:
        project = sessions.store.project(conn, 'shop')
        actor = sessions.store.actor(conn, 'admin-id', 'admin@example.test')
        sid = add_test_session(
            conn, project, actor, agent_id, sessions.claude, config=config, message='Hello', purpose='chat'
        )
    store = sessions.store
    fake = FakeAgent([{'steps': [llm(message('Hi.'))], 'answer': 'Hi.'}])

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, poll=0.02).run())
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 1)
        await post(sessions, f'/api/sessions/{sid}/close')
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())
    runs = sessions.client.get('/api/agent-runs?kind=chat_session', headers=HEADERS).json()['runs']
    assert [run['run_key'] for run in runs] == [shared.run_key(sid, 1)]


def test_the_migration_adds_chat_and_down_makes_chats_test_sessions(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        actor = store.actor(conn, 'admin-id', 'admin@example.test')
        agent_id = conn.execute(
            "INSERT INTO ka.agents(id,name,system_prompt) VALUES ('chatter','Chatter','') RETURNING id"
        ).fetchone()['id']
        model = a_model(conn, actor)
        chat = add_test_session(conn, project, actor, agent_id, model, purpose='chat')
        migrate(conn, DOWN)
        assert (
            conn.execute('SELECT purpose FROM ka.agent_sessions WHERE id=%s', (chat,)).fetchone()['purpose'] == 'test'
        )
    with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
        add_test_session(conn, project, actor, agent_id, model, purpose='chat')
    with store.connect() as conn:
        migrate(conn, UP)
        add_test_session(conn, project, actor, agent_id, model, purpose='chat')
    with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
        add_test_session(conn, project, actor, None, model, purpose='chat')  # a chat needs its agent
