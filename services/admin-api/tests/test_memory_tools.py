"""The memory tools as the model calls them: replies, refusals, the 2,000-character cut, who saved it."""

import asyncio
import json
from uuid import uuid4

import pytest
from knowledge_admin.capabilities import inventory
from knowledge_admin.tools.catalog import CATALOG
from knowledge_admin.tools.sdk import function_tool
from psycopg.types.json import Jsonb
from session_rows import a_model


@pytest.fixture
def run(store, monkeypatch):
    monkeypatch.setattr(inventory, '_store', store)
    monkeypatch.setattr('knowledge_admin.capabilities.memory.current_model', lambda conn, project_id: None)
    session = uuid4()
    with store.connect() as conn:
        pid = store.project(conn, 'shop')
        actor = store.actor(conn, 'tester', 'tester@example.test')
        agent = conn.execute(
            "INSERT INTO ka.agents(id,name,system_prompt) VALUES ('coder','Coder','x') RETURNING id"
        ).fetchone()['id']
        conn.execute(
            """INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
            context_snapshot,purpose,agent_config,turns) VALUES (%s,%s,%s,%s,%s,'Memory','{}','{}','test',%s,1)""",
            (session, pid, agent, actor, a_model(conn, actor), Jsonb({})),
        )
    config = {'project_id': str(pid), 'session_id': str(session)}
    return {'config': config, 'agent': agent, 'session': session, 'store': store}


def items(**pairs: str) -> list[dict]:
    """Metadata as the model sends it: key/value items (a strict schema has no free-form objects)."""
    return [{'key': key, 'value': value} for key, value in pairs.items()]


def call(run, tool: str, /, **arguments) -> dict:
    spec = CATALOG[tool]
    return json.loads(asyncio.run(spec.run({'config': run['config']}, spec.arguments.model_validate(arguments))))


def test_the_four_tools_are_in_the_memory_group_and_build_as_strict_function_tools():
    names = ['search_memory', 'get_memory', 'save_memory', 'update_memory']
    assert [name for name, spec in CATALOG.items() if spec.group == 'memory'] == names
    assert all(CATALOG[name].needs is None for name in names)
    for name in names:
        assert function_tool(CATALOG[name], CATALOG[name].usage).strict_json_schema


def test_save_get_and_who_saved_it(run):
    saved = call(run, 'save_memory', name='payments/deploy', content='Use make release.', metadata=items(service='p'))
    assert saved['saved'] == {'name': 'payments/deploy', 'revision': 1, 'embedded': False}
    assert 'found by words only' in saved['note']
    entry = call(run, 'get_memory', name='payments/deploy')['memory']
    assert (entry['content'], entry['revision'], entry['saved_by']) == ('Use make release.', 1, 'Coder')
    assert entry['metadata'] == {'service': 'p'}
    with run['store'].connect() as conn:
        row = conn.execute('SELECT created_by_agent,created_by_session FROM ka.memories').fetchone()
    assert (row['created_by_agent'], row['created_by_session']) == (run['agent'], run['session'])


def test_save_of_an_existing_name_is_refused_with_the_entry(run):
    call(run, 'save_memory', name='a', content='First.')
    refused = call(run, 'save_memory', name='a', content='Second.')
    assert refused['refused'].startswith('An entry with this name exists.')
    assert 'update_memory with revision 1' in refused['refused']
    assert refused['memory']['content'] == 'First.'


def test_update_with_the_current_and_a_stale_revision(run):
    call(run, 'save_memory', name='a', content='First.')
    updated = call(run, 'update_memory', name='a', content='Merged.', metadata=[], revision=1)
    assert updated['updated'] == {'name': 'a', 'revision': 2, 'embedded': False}
    stale = call(run, 'update_memory', name='a', content='Late.', metadata=[], revision=1)
    assert 'revision 2' in stale['refused'] and stale['memory']['content'] == 'Merged.'
    missing = call(run, 'update_memory', name='b', content='x', metadata=[], revision=1)
    assert missing == {'refused': 'No entry named b; use save_memory.'}


def test_search_cuts_long_content_and_filters(run):
    call(run, 'save_memory', name='long', content='payments ' + 'x' * 3000, metadata=items(service='payments'))
    call(run, 'save_memory', name='short', content='billing note', metadata=items(service='billing'))
    found = call(run, 'search_memory', query='payments')
    [hit] = found['results']
    assert hit['name'] == 'long' and hit['content'].endswith('… (call get_memory for the full entry)')
    assert len(hit['content']) <= 2000 + len('… (call get_memory for the full entry)')
    assert [r['name'] for r in call(run, 'search_memory', metadata=items(service='billing'))['results']] == ['short']
    assert [r['name'] for r in call(run, 'search_memory', agent='CODER')['results']] == [
        'short',
        'long',
    ]  # newest first
    assert call(run, 'search_memory', agent='Nobody') == {'refused': 'No agent named Nobody.'}


def test_validation_errors_come_back_as_the_rule(run):
    refused = call(run, 'save_memory', name='Bad Name', content='x')
    assert refused['refused'].startswith('A name is 1-200 characters')


def test_a_run_without_a_session_saves_without_agent(run):
    run['config'].pop('session_id')
    assert call(run, 'save_memory', name='test/run', content='x')['saved']['revision'] == 1
    assert call(run, 'get_memory', name='test/run')['memory']['saved_by'] is None
