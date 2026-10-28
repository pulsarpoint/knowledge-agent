"""Session review: a reviewer chat (purpose 'review') on a finished chat or event run of a saved agent. It is started
with POST /api/agent-runs/{run_key}/reviews and then runs like any chat (the session API). The reviewer gets exactly
its tool set: the explorer tools bound to the pinned reviewed session, get_agent, memory read and save, read_file, and
the two write tools update_agent_instructions (revision checked, audited, the system prompt untouched) and create_skill
(always disabled). The old single-shot review is gone."""

import asyncio
import json
from pathlib import Path

import pytest
from agents.tool_context import ToolContext
from psycopg.types.json import Jsonb

from knowledge_admin.agents.runner import instructions_and_tools, load_credentials
from knowledge_admin.capabilities import inventory
from knowledge_admin.capabilities.skills import run_skills
from knowledge_admin.sessions import review_agent
from knowledge_admin.sessions.explorer import METHODS
from knowledge_admin.sessions.start import AGENT_NO_MODEL
from knowledge_admin.tools.catalog import CATALOG
from test_generations import sessions  # noqa: F401  (fixture)
from test_repositories import HEADERS
from test_sessions_saved_agent import agent, start  # noqa: F401  (fixture)
from test_sessions_world import ask  # noqa: F401  (fixture)

EXPLORER_TOOLS = {method.tool for method in METHODS.values()}
REVIEWER_TOOLS = EXPLORER_TOOLS | {
    'get_agent',
    'update_agent_instructions',
    'create_skill',
    'search_memory',
    'get_memory',
    'save_memory',
    'read_file',
}


def finished(store, session_id, turns: int = 1, status: str = 'waiting', answer: str = 'Disk is fine.') -> None:
    """A session that ran `turns` turns (each with a record) and now has `status`."""
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute('UPDATE ka.agent_sessions SET status=%s,turns=%s WHERE id=%s', (status, turns, session_id))
        conn.execute(
            "UPDATE ka.agent_session_inputs SET status='delivered',turn=1,delivered_at=now() WHERE session_id=%s",
            (session_id,),
        )
        for turn in range(1, turns + 1):
            events = [
                ('start', {'kind': 'chat_session', 'title': 'Check', 'agent': 'Operator', 'turn': turn}),
                ('input', {'task': f'Check the disk ({turn}).'}),
                ('message', {'text': f'{answer} ({session_id})'}),
                ('usage', {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}),
                ('end', {'status': 'completed'}),
            ]
            for seq, (kind, payload) in enumerate(events, start=1):
                conn.execute(
                    """INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
                    VALUES (%s,%s,%s,%s,'operator',%s)""",
                    (project, f'cc:{session_id}:{turn}', seq, kind, Jsonb(payload)),
                )


def a_chat(agent, store, **finish) -> str:
    response = start(agent.client, agent_id=agent.id, resources={'server_ids': [agent.web]})
    assert response.status_code == 201, response.text
    session_id = response.json()['id']
    finished(store, session_id, **finish)
    return session_id


def begin(client, run_key: str):
    return client.post(f'/api/agent-runs/{run_key}/reviews', headers=HEADERS)


@pytest.fixture
def world(agent, ask, monkeypatch, skills_folder):
    store = ask.s.store
    monkeypatch.setattr(inventory, '_store', store)
    chat = a_chat(agent, store, turns=2)
    review = begin(agent.client, f'cc:{chat}:1')
    assert review.status_code == 201, review.text
    review = review.json()
    with store.connect() as conn:
        config = conn.execute('SELECT agent_config FROM ka.agent_sessions WHERE id=%s', (review['id'],)).fetchone()
    config = {**config['agent_config'], 'session_id': review['id']}  # as the session worker loads it
    return type('World', (), {'agent': agent, 'store': store, 'chat': chat, 'review': review, 'config': config})


def invoke(tools, name: str, arguments: dict | None = None, config: dict | None = None) -> str:
    [tool] = [item for item in tools if item.name == name]
    raw = json.dumps(arguments or {})
    context = ToolContext(
        context={'tools': {item.name for item in tools}, 'config': config or {}, 'credentials': {}},
        tool_name=name,
        tool_call_id='t1',
        tool_arguments=raw,
    )
    return asyncio.run(tool.on_invoke_tool(context, raw))


def call(world, name: str, arguments: dict | None = None) -> dict:
    tools = review_agent.reviewer_tools(world.config)
    output = invoke(tools, name, arguments, world.config)
    return json.loads(output)


# --- starting a review -------------------------------------------------------------------------------------------


def test_a_review_is_a_session_with_the_reviewer_config_pinned(world):
    review, agent = world.review, world.agent
    assert (review['purpose'], review['agent_id'], review['status'], review['turns']) == (
        'review',
        agent.id,
        'waiting',
        0,
    )
    config = world.config
    with world.store.connect() as conn:
        revision = conn.execute('SELECT revision FROM ka.agents WHERE id=%s', (agent.id,)).fetchone()['revision']
    assert config['review_of'] == f'cc:{world.chat}:1'
    assert (config['reviewed_agent_id'], config['reviewed_revision']) == (agent.id, revision)
    assert config['instructions'] == review_agent.REVIEWER_PROMPT
    assert str(config['model']['id']) == str(agent.model)
    assert config['access'] == {'resources': []} and config['experts'] == [] and config['disabled_tools'] == []


def test_a_review_is_read_and_continued_through_the_session_api(world):
    client, review = world.agent.client, world.review
    detail = client.get(f'/api/sessions/{review["id"]}', headers=HEADERS)
    assert detail.status_code == 200 and detail.json()['purpose'] == 'review'
    assert 'reviews' not in detail.json()
    sent = client.post(
        f'/api/sessions/{review["id"]}/messages', headers=HEADERS, json={'text': 'It should check memory first.'}
    )
    assert sent.status_code == 202, sent.text
    history = client.get(f'/api/sessions/{review["id"]}/events/history', headers=HEADERS)
    assert history.status_code == 200 and history.json()['events'] == []
    stop = client.post(f'/api/sessions/{review["id"]}/stop', headers=HEADERS)
    assert stop.status_code == 409  # not working: nothing to stop, as in any chat
    debug = client.get(f'/api/sessions/{review["id"]}/debug', headers=HEADERS).json()
    assert {tool['name'] for tool in debug['tools']} == REVIEWER_TOOLS  # the Debug tab shows what the reviewer gets


def test_earlier_reviews_of_the_session_are_listed(world):
    client = world.agent.client
    second = begin(client, f'cc:{world.chat}:1').json()
    for key in (f'cc:{world.chat}:1', f'cc:{world.chat}:2'):  # any turn of the session lists its reviews
        listed = client.get(f'/api/agent-runs/{key}/reviews', headers=HEADERS)
        assert listed.status_code == 200, listed.text
        assert [item['id'] for item in listed.json()['reviews']] == [second['id'], world.review['id']]
    other = a_chat(world.agent, world.store)
    assert client.get(f'/api/agent-runs/cc:{other}:1/reviews', headers=HEADERS).json()['reviews'] == []
    assert client.get(f'/api/agent-runs/cc:{world.chat}:3/reviews', headers=HEADERS).status_code == 404


def test_start_rules(agent, ask):
    store, client = ask.s.store, agent.client
    working = a_chat(agent, store, status='working')
    refused = begin(client, f'cc:{working}:1')
    assert (refused.status_code, refused.json()['error']['message']) == (409, review_agent.BUSY)
    assert review_agent.BUSY == 'Wait for the agent to finish before reviewing it.'

    document_run = client.post('/api/sessions', headers=HEADERS, json=ask.body).json()['id']
    finished(store, document_run)
    refused = begin(client, f'cc:{document_run}:1')
    assert (refused.status_code, refused.json()['error']['message']) == (400, review_agent.NOT_REVIEWABLE)
    assert review_agent.NOT_REVIEWABLE == "Reviews are for a saved agent's chats and event runs."

    chat = a_chat(agent, store)
    review = begin(client, f'cc:{chat}:1').json()
    refused = begin(client, f'cc:{review["id"]}:1')  # a review is not reviewed
    assert refused.status_code in (400, 404)

    with store.connect() as conn:
        conn.execute('UPDATE ka.agents SET model_id=NULL WHERE id=%s', (agent.id,))
    refused = begin(client, f'cc:{chat}:1')
    assert (refused.status_code, refused.json()['error']['message']) == (409, AGENT_NO_MODEL)
    assert AGENT_NO_MODEL == 'Select an enabled model in this agent’s General tab.'

    assert begin(client, f'cc:{chat}:7').status_code == 404  # no such turn
    assert begin(client, 'cc:00000000-0000-0000-0000-000000000001:1').status_code == 404


def test_the_old_single_shot_review_is_gone(world):
    client, chat = world.agent.client, world.chat
    old = client.post(f'/api/sessions/{chat}/review', headers=HEADERS, json={'comment': 'Shorter.'})
    assert old.status_code in (404, 405)
    some = '00000000-0000-0000-0000-000000000001'
    assert client.get(f'/api/agents/{world.agent.id}/reviews/{some}', headers=HEADERS).status_code == 404
    apply = client.post(f'/api/agents/{world.agent.id}/reviews/{some}/apply', headers=HEADERS, json={})
    assert apply.status_code == 404
    root = Path(review_agent.__file__).parent
    assert not (root / 'review.py').exists() and not (root.parent / 'agents' / 'reviews.py').exists()


# --- the reviewer's tools and instructions ------------------------------------------------------------------------


def test_the_reviewer_gets_exactly_its_tools(world):
    config = {**world.config, 'experts': [{'id': 'x', 'tool': 'ask_x', 'description': 'X', 'name': 'X'}]}
    prompt, tools, experts, mcp = instructions_and_tools(config, subagents=True)
    assert {tool.name for tool in tools} == REVIEWER_TOOLS and len(tools) == len(REVIEWER_TOOLS)
    assert experts == [] and mcp == []
    assert review_agent.REVIEWER_PROMPT in prompt and f'cc:{world.chat}:1' in prompt
    assert 'get_available_resources' not in prompt


def test_the_prompt_keeps_its_key_rules():
    text = ' '.join(review_agent.REVIEWER_PROMPT.split())
    for rule in (
        'record_digest',
        "Never change anything until the person's latest message agrees to that specific change",
        'If no change is justified, say so',
        'Never put secrets in any text',
        'The system prompt is out of scope',
        "the agent's instructions",
        'a skill',
        'memory',
    ):
        assert rule in text, rule


def test_a_review_run_loads_only_its_model_key(world):
    with world.store.connect() as conn:
        conn.execute('UPDATE ka.agents SET enabled=false WHERE id=%s', (world.agent.id,))
    assert load_credentials(world.store, world.config) == {}  # the reviewer is no agents row


def test_explorer_tools_read_only_the_pinned_session(world):
    other = a_chat(world.agent, world.store, answer='Other session')
    overview = call(world, 'record_overview')
    assert [turn['run_key'] for turn in overview['turns']] == [f'cc:{world.chat}:1', f'cc:{world.chat}:2']
    digest = json.dumps(call(world, 'record_digest'))
    assert world.chat in digest and other not in digest
    tools = review_agent.reviewer_tools(world.config)
    for arguments in ({'run_key': f'cc:{other}:1'}, {'session_id': other}):
        output = invoke(tools, 'record_overview', arguments, world.config)
        assert output.startswith('An error occurred') and other not in output.split('Error:')[0]
    found = call(world, 'record_search', {'text': other})
    assert found['total_hits'] == 0


def test_get_agent_shows_the_reviewed_agent(world):
    with world.store.connect() as conn:
        conn.execute(
            "UPDATE ka.agents SET instructions='Be brief.',disabled_tools=ARRAY['ssh_run'] WHERE id=%s",
            (world.agent.id,),
        )
        row = conn.execute('SELECT name,description,revision FROM ka.agents WHERE id=%s', (world.agent.id,)).fetchone()
    found = call(world, 'get_agent')['agent']
    assert (found['id'], found['name'], found['description']) == (world.agent.id, row['name'], row['description'])
    assert (found['instructions'], found['revision']) == ('Be brief.', row['revision'])
    assert found['tools'] == sorted(name for name in CATALOG if name != 'ssh_run')
    assert found['skills'] == []
    assert 'system_prompt' not in found


def test_update_agent_instructions_checks_the_revision_and_is_audited(world):
    columns = 'name,description,when_to_use,enabled,model_id,system_prompt,access,disabled_tools,expert'
    with world.store.connect() as conn:
        before = conn.execute(f'SELECT {columns},instructions FROM ka.agents WHERE id=%s', (world.agent.id,)).fetchone()
    revision = call(world, 'get_agent')['agent']['revision']
    stale = call(
        world, 'update_agent_instructions', {'instructions': 'New.', 'summary': 'Shorter', 'revision': revision + 5}
    )
    assert stale == {'refused': review_agent.REVISION_CHANGED}
    assert review_agent.REVISION_CHANGED == (
        'The agent changed since you read it. Read it again with get_agent and propose the change again.'
    )
    done = call(
        world,
        'update_agent_instructions',
        {'instructions': 'Check memory first.', 'summary': 'Memory before SSH', 'revision': revision},
    )
    assert done['updated']['revision'] == revision + 1
    with world.store.connect() as conn:
        after = conn.execute(
            f'SELECT {columns},instructions,revision FROM ka.agents WHERE id=%s', (world.agent.id,)
        ).fetchone()
        audit = conn.execute(
            """SELECT actor_id,details FROM ka.audit_events WHERE action='agent.instructions.updated'
            ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        actor = conn.execute('SELECT actor_id FROM ka.agent_sessions WHERE id=%s', (world.review['id'],)).fetchone()[
            'actor_id'
        ]
    assert after['instructions'] == 'Check memory first.' and after['revision'] == revision + 1
    assert {key: after[key] for key in columns.split(',')} == {key: before[key] for key in columns.split(',')}
    assert audit['actor_id'] == actor
    assert audit['details'] == {
        'agent_id': world.agent.id,
        'review_session_id': world.review['id'],
        'summary': 'Memory before SSH',
        'old_instructions': before['instructions'],
        'new_instructions': 'Check memory first.',
        'revision': revision + 1,
    }
    again = call(world, 'update_agent_instructions', {'instructions': 'X.', 'summary': 'X', 'revision': revision})
    assert again == {'refused': review_agent.REVISION_CHANGED}  # the same revision a second time is stale


def test_create_skill_is_disabled_and_not_in_any_run(world, skills_folder):
    created = call(
        world,
        'create_skill',
        {'name': 'disk-checks', 'description': 'Use when checking disks.', 'content': '# Disk checks\n\nRun df -h.\n'},
    )['created']
    assert created['enabled'] is False and created['link'] == f'/skills/{created["id"]}'
    with world.store.connect() as conn:
        row = conn.execute(
            'SELECT name,enabled,source,folder FROM ka.agent_skills WHERE id=%s', (created['id'],)
        ).fetchone()
        project = world.store.project(conn, 'shop')
        assert run_skills(conn, project) == []  # disabled: no run gets it until a person enables it
    assert (row['name'], row['enabled']) == ('disk-checks', False)
    assert row['source'] == {'kind': 'review', 'session_id': world.review['id']}
    text = (skills_folder / row['folder'] / 'SKILL.md').read_text()
    assert text.startswith('---\nname: disk-checks\ndescription: Use when checking disks.\n---\n')
    assert text.endswith('# Disk checks\n\nRun df -h.\n')
    clash = call(world, 'create_skill', {'name': 'disk-checks', 'description': 'Again.', 'content': 'x'})
    assert clash == {'refused': review_agent.SKILL_EXISTS}
    assert review_agent.SKILL_EXISTS == 'A skill with this name already exists.'
    bad = call(world, 'create_skill', {'name': 'Disk Checks', 'description': 'Again.', 'content': 'x'})
    assert 'refused' in bad


def test_a_review_turn_runs_in_the_session_worker_as_a_review_run(world, monkeypatch):
    from knowledge_admin.knowledge_agents import native_sessions
    from knowledge_admin.sessions import run_record
    from native_fakes import FakeAgent, llm, message
    from run_helpers import wait_for
    from test_test_session_worker import claimed

    store, client, review = world.store, world.agent.client, world.review
    monkeypatch.setattr(run_record, '_store', store)
    client.post(f'/api/sessions/{review["id"]}/messages', headers=HEADERS, json={'text': 'Check memory first.'})
    fake = FakeAgent([{'steps': [llm(message('I read the digest.'))], 'answer': 'Put it in the instructions?'}])

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, poll=0.02).run())
        await wait_for(store, review['id'], lambda s: s['status'] == 'waiting' and s['turns'] == 1)
        client.post(f'/api/sessions/{review["id"]}/close', headers=HEADERS)
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())
    [turn] = fake.calls
    assert turn['task'] == 'Check memory first.' and turn['config']['review_of'] == f'cc:{world.chat}:1'
    assert turn['config']['session_id'] == review['id']
    runs = client.get('/api/agent-runs?kind=review_session', headers=HEADERS).json()['runs']
    assert [run['run_key'] for run in runs] == [f'cc:{review["id"]}:1']
