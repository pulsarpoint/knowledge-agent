"""Apply to original: the clone's configuration replaces the original's, except name and description; refused for a
stale dialog, while the original works, and when there is no original."""

import threading
import time
from uuid import uuid4

from knowledge_admin.agents.clones import APPLY_WORKING, NOT_A_CLONE, ORIGINAL_CHANGED
from knowledge_admin.knowledge_agents.config import get_agent_config, sections
from test_agent_clones import clone
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_profiles import update
from test_repositories import HEADERS


def rows(conn, agent_id: str) -> dict[str, list]:
    """What decides how the agent runs: its configuration columns (access and instructions among them), comparable
    between two agents."""
    return {
        'agent': conn.execute(
            """SELECT when_to_use,model_id,system_prompt,access,instructions
            FROM ka.agents WHERE id=%s""",
            (agent_id,),
        ).fetchone(),
    }


def preview(client, agent_id: str) -> dict:
    response = client.get(f'/api/agents/{agent_id}/apply', headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def apply(client, agent_id: str, value: dict):
    return client.post(
        f'/api/agents/{agent_id}/apply',
        headers=HEADERS,
        json={'revision': value['original']['revision'], 'fingerprint': value['fingerprint']},
    )


def edit_prompt(client, agent_id: str, text: str) -> None:
    revision = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()['revision']
    saved = client.put(
        f'/api/agents/{agent_id}/prompt', headers=HEADERS, json={'revision': revision, 'system_prompt': text}
    )
    assert saved.status_code == 200, saved.text


def test_apply_copies_everything_but_name_and_description(store, saved_agent):
    client, mcp = saved_agent[0], saved_agent[4]
    copy_id = clone(client)['id']
    original = client.get('/api/agents/operations', headers=HEADERS).json()
    edit_prompt(client, copy_id, 'Answer from evidence.')
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET instructions='Gather evidence first.' WHERE id=%s", (copy_id,))
        conn.execute(  # the clone drops the MCP server and becomes a content editor
            """UPDATE ka.agents SET access=jsonb_build_object(
                'resources',(SELECT jsonb_agg(r) FROM jsonb_array_elements(access->'resources') r WHERE r->>'id'<>%s),
                'roles',jsonb_build_object('content_editor',true)) WHERE id=%s""",
            (mcp['id'], copy_id),
        )
        conn.execute(
            """UPDATE ka.agents SET name='Experiment',description='Evidence first',when_to_use='Payments',
            enabled=false WHERE id=%s""",
            (copy_id,),
        )
    value = preview(client, copy_id)
    assert value['original'] == {'id': 'operations', 'name': original['name'], 'revision': original['revision']}
    assert value['changes'] == ['General', 'Resources']  # each tab once: an MCP server is a resource
    applied = apply(client, copy_id, value)
    assert applied.status_code == 200, applied.text
    assert applied.json() == {'id': 'operations', 'revision': original['revision'] + 1}
    assert sections(get_agent_config('operations', conninfo=store.conninfo)) == sections(
        get_agent_config(copy_id, conninfo=store.conninfo)
    )
    with store.connect() as conn:
        assert rows(conn, 'operations') == rows(conn, copy_id)
        assert rows(conn, 'operations')['agent']['access']['roles'] == {'content_editor': True}
    after = client.get('/api/agents/operations', headers=HEADERS).json()
    assert (after['name'], after['description'], after['enabled'], after['cloned_from']) == (
        original['name'],
        original['description'],
        original['enabled'],
        None,
    )
    assert preview(client, copy_id)['changes'] == []
    with store.connect() as conn:
        audit = conn.execute("SELECT details FROM ka.audit_events WHERE action='agent.config.applied'").fetchall()
    assert [row['details'] for row in audit] == [{'agent_id': 'operations', 'clone_id': copy_id}]


def test_apply_is_refused_when_the_original_changed_or_is_working(store, saved_agent):
    client, model = saved_agent[0], saved_agent[6]
    copy_id = clone(client)['id']
    edit_prompt(client, copy_id, 'Answer from evidence.')
    value = preview(client, copy_id)
    with store.connect() as conn:  # a write that keeps agents.revision but changes what runs
        conn.execute("UPDATE ka.agents SET instructions='Edited meanwhile.' WHERE id='operations'")
    stale = apply(client, copy_id, value)
    assert stale.status_code == 409 and stale.json()['error']['message'] == ORIGINAL_CHANGED
    value = preview(client, copy_id)
    edit_prompt(client, 'operations', 'Newer.')
    assert apply(client, copy_id, value).json()['error']['message'] == ORIGINAL_CHANGED
    value = preview(client, copy_id)
    session = uuid4()
    with store.connect() as conn:  # a documentation session of the original is working
        conn.execute(
            """INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
            context_snapshot,document_name,status,agent_config) SELECT %s,p.id,'operations',(SELECT id FROM ka.principals LIMIT 1),
            %s,'Payments','{}','{}','Payments','working','{"id": "operations"}' FROM ka.projects p WHERE p.archived_at IS NULL""",
            (session, model['id']),
        )
    working = apply(client, copy_id, value)
    assert working.status_code == 409 and working.json()['error']['message'] == APPLY_WORKING
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET status='stopped' WHERE id=%s", (session,))
    assert apply(client, copy_id, value).status_code == 200


def test_apply_needs_a_clone_whose_original_still_exists(store, saved_agent):
    client = saved_agent[0]
    copy_id = clone(client)['id']
    plain = client.get('/api/agents/operations/apply', headers=HEADERS)
    assert plain.status_code == 404 and plain.json()['error']['message'] == NOT_A_CLONE
    assert client.get(f'/api/agents/{copy_id}/apply').status_code == 403
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
    assert client.get(f'/api/agents/{copy_id}/apply', headers=HEADERS).status_code == 404
    gone = client.post(f'/api/agents/{copy_id}/apply', headers=HEADERS, json={'revision': 1, 'fingerprint': '0' * 64})
    assert gone.status_code == 404 and gone.json()['error']['message'] == NOT_A_CLONE


def waits_for_the_agent(store, write) -> None:
    """While another transaction (Apply) holds the original's row, the write waits for it, then succeeds."""
    with store.connect() as holder, store.connect() as watcher:
        watcher.autocommit = True  # pg_stat_activity is a snapshot per transaction
        holder.execute("SELECT id FROM ka.agents WHERE id='operations' FOR UPDATE")
        done: list = []
        worker = threading.Thread(target=lambda: done.append(write()))
        worker.start()
        deadline = time.monotonic() + 20
        while not watcher.execute("""SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock'
                AND query LIKE 'SELECT id FROM ka.agents WHERE id=%%FOR UPDATE%%'""").fetchone():
            assert worker.is_alive() and time.monotonic() < deadline, 'the write did not wait for the agent'
            time.sleep(0.05)
        assert not done
    worker.join(20)
    assert done and done[0].status_code == 200, done and done[0].text


def test_a_profile_save_waits_for_apply(store, saved_agent):
    client = saved_agent[0]
    current = client.get('/api/agents/operations', headers=HEADERS).json()  # the form is read before Apply locks
    waits_for_the_agent(store, lambda: update(client, current, instructions='Check only the queue.'))


def test_switching_a_skill_off_keeps_the_fingerprint(store, saved_agent):
    """Skills are not part of an agent's configuration (every run gets every enabled skill)."""
    client, skill = saved_agent[0], saved_agent[3]
    copy_id = clone(client)['id']
    before = preview(client, copy_id)
    with store.connect() as conn:
        conn.execute('UPDATE ka.agent_skills SET enabled=false WHERE id=%s', (skill['id'],))
    after = preview(client, copy_id)
    assert after['fingerprint'] == before['fingerprint'] and after['changes'] == []
