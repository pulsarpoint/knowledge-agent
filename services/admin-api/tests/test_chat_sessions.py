"""A saved agent's conversation: POST /api/sessions with a saved agent starts a chat session (0122; 0142 makes the old
Test sessions chats). The agent page's Chat tab lists its chats and event runs, and its turns are Chat runs."""

import asyncio
from pathlib import Path

import psycopg
import pytest
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb

from knowledge_admin.knowledge_agents import native_sessions
from knowledge_admin.knowledge_agents import session_worker as shared
from knowledge_admin.knowledge_agents.config import get_agent_config
from knowledge_admin.sessions import run_record
from native_fakes import FakeAgent, llm, message
from run_helpers import post, wait_for
from session_rows import a_model, add_chat_session
from test_generations import native_agent, sessions  # noqa: F401  (fixture)
from test_repositories import HEADERS
from test_sessions_saved_agent import agent, start  # noqa: F401  (fixture)
from test_sessions_world import ask  # noqa: F401  (fixture)
from test_test_session_worker import claimed

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0122_chat_sessions.up.sql', '0122_chat_sessions.down.sql'
TEST_IS_CHAT_UP, TEST_IS_CHAT_DOWN = '0142_test_is_chat.up.sql', '0142_test_is_chat.down.sql'


def migrate(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def test_a_saved_agent_run_is_a_chat_and_takes_no_purpose(agent):
    chat = start(agent.client, agent_id=agent.id, resources={'server_ids': [agent.web]})
    assert chat.status_code == 201, chat.text
    assert (chat.json()['purpose'], chat.json()['agent_id']) == ('chat', agent.id)
    assert chat.json()['config']['id'] == agent.id  # its pinned configuration is shown
    for purpose in ('chat', 'test', 'event'):
        response = start(agent.client, agent_id=agent.id, purpose=purpose)
        assert response.status_code == 400, (purpose, response.text)


def test_the_agent_conversations_are_its_chats_and_event_runs(agent, ask):
    chat = start(agent.client, agent_id=agent.id, resources={'server_ids': [agent.web]}).json()
    store = ask.s.store
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        actor = store.actor(conn, 'admin-id', 'admin@example.test')
        event = add_chat_session(conn, project, actor, agent.id, agent.model, purpose='event')
    listed = agent.client.get(f'/api/sessions?agent_id={agent.id}', headers=HEADERS).json()['sessions']
    assert {item['id'] for item in listed} == {chat['id'], str(event)}
    history = agent.client.get(f'/api/agents/{agent.id}/history', headers=HEADERS).json()
    assert history['conversations'] == 2 and 'test_chats' not in history and 'chats' not in history


def test_a_chat_session_turn_is_a_chat_run(sessions, monkeypatch):
    monkeypatch.setattr(run_record, '_store', sessions.store)
    agent_id = native_agent(sessions.client, 'Chat agent', model_id=sessions.claude)
    config = jsonable_encoder(get_agent_config(agent_id, conninfo=sessions.store.conninfo))
    with sessions.store.connect() as conn:
        project = sessions.store.project(conn, 'shop')
        actor = sessions.store.actor(conn, 'admin-id', 'admin@example.test')
        sid = add_chat_session(
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
        chat = add_chat_session(conn, project, actor, agent_id, model, purpose='chat')
        migrate(conn, TEST_IS_CHAT_DOWN)  # golang-migrate rolls back the later migrations first
        migrate(conn, DOWN)
        assert (
            conn.execute('SELECT purpose FROM ka.agent_sessions WHERE id=%s', (chat,)).fetchone()['purpose'] == 'test'
        )
    with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
        add_chat_session(conn, project, actor, agent_id, model, purpose='chat')
    with store.connect() as conn:
        migrate(conn, UP)
        add_chat_session(conn, project, actor, agent_id, model, purpose='chat')
    with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
        add_chat_session(conn, project, actor, None, model, purpose='chat')  # a chat needs its agent


def test_0142_makes_test_sessions_and_their_run_records_chats(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        actor = store.actor(conn, 'admin-id', 'admin@example.test')
        agent_id = conn.execute(
            "INSERT INTO ka.agents(id,name,system_prompt) VALUES ('tester','Tester','') RETURNING id"
        ).fetchone()['id']
        model = a_model(conn, actor)
        migrate(conn, TEST_IS_CHAT_DOWN)
        tested = add_chat_session(conn, project, actor, agent_id, model, purpose='test')
        event = add_chat_session(conn, project, actor, agent_id, model, purpose='event')
        for key, kind in (('cc:a:1', 'test_session'), ('cc:b:1', 'event_session'), ('run:c', 'test')):
            conn.execute(
                """INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,actor_id,agent_id,payload)
                VALUES (%s,%s,1,'start',%s,%s,%s)""",
                (project, key, actor, agent_id, Jsonb({'kind': kind, 'title': 'Run'})),
            )
        migrate(conn, TEST_IS_CHAT_UP)
        purposes = {
            row['id']: row['purpose']
            for row in conn.execute('SELECT id,purpose FROM ka.agent_sessions WHERE id IN (%s,%s)', (tested, event))
        }
        assert purposes == {tested: 'chat', event: 'event'}
        kinds = {
            row['run_key']: row['payload']
            for row in conn.execute("SELECT run_key,payload FROM ka.agent_run_events WHERE kind='start'")
        }
        assert {key: kinds[key]['kind'] for key in ('cc:a:1', 'cc:b:1', 'run:c')} == {
            'cc:a:1': 'chat_session',
            'cc:b:1': 'event_session',
            'run:c': 'test',  # a Test chat from before 0081 keeps its kind
        }
        assert kinds['cc:a:1']['title'] == 'Run'
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            add_chat_session(conn, project, actor, agent_id, model, purpose='test')
        migrate(conn, TEST_IS_CHAT_DOWN)  # restores the constraint only: chats stay chats
        assert (
            conn.execute('SELECT purpose FROM ka.agent_sessions WHERE id=%s', (tested,)).fetchone()['purpose'] == 'chat'
        )
        add_chat_session(conn, project, actor, agent_id, model, purpose='test')
