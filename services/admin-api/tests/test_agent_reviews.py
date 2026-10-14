"""Tuning reviews: the reviewer's output shape and applying a reviewed suggestion. Correcting invalid reviewer output
is tested with the Test session review in test_test_session_feedback.py."""
import json
from uuid import uuid4

import pytest
from agents import AgentOutputSchema, ModelBehaviorError

import psycopg

from knowledge_admin.agents import reviews as agent_reviews
from knowledge_admin.agents.clones import NAME_TAKEN
from knowledge_admin.agents.reviews import (AGENT_CHANGED, INVALID_TEXT, PROMPT_REQUIRED, REVIEW_NOT_FOUND, UNKNOWN_SERVER,
                                          TuningSuggestion)
from knowledge_admin.knowledge_agents.config import get_agent_config, sections
from session_rows import a_model, add_review, add_test_session
from test_agents import agent_mounts, saved_agent  # noqa: F401
from test_sdk_agent import responses_api  # noqa: F401  (fixture)
from test_repositories import HEADERS


def test_sdk_tuning_schema_decodes_nested_changes_and_server_instructions():
    server_id = str(uuid4())
    changes = {'system_prompt':None,'instructions':'Use "read-only" checks.\nReport uncertainty.',
               'server_instructions':[{'server_id':server_id,'instructions':'Read logs first.'}]}
    schema = AgentOutputSchema(TuningSuggestion, strict_json_schema=False)
    proposal = schema.validate_json(json.dumps({'suggestion':'Clarify inspection steps.',
                                                'changes':json.dumps(changes)}))
    assert proposal.changes.model_dump(mode='json') == changes
    assert isinstance(json.loads(proposal.model_dump_json())['changes'],dict)
    assert schema.json_schema()['properties']['changes'] == {'$ref':'#/$defs/InstructionChanges'}


@pytest.mark.parametrize('changes', [
    '{invalid JSON',
    '[]',
    'null',
    '{}',
    json.dumps({'system_prompt':None,'instructions':None,'server_instructions':[], 'ssh_enabled':True}),
    json.dumps({'system_prompt':None,'instructions':None,'server_instructions':[{'server_id':'invalid','instructions':'Read logs.'}]}),
])
def test_sdk_tuning_schema_still_rejects_invalid_encoded_changes(changes):
    schema = AgentOutputSchema(TuningSuggestion, strict_json_schema=False)
    with pytest.raises(ModelBehaviorError):
        schema.validate_json(json.dumps({'suggestion':'Suggested changes.','changes':changes}))


NARROWED = {'run_resources': {'code_project_ids': []}, 'code': {'enabled': True, 'all_projects': False, 'projects': []}}


def saved_review(store, agent_id: str, changes: dict, agent_config: dict | None = None) -> str:
    """A tuning suggestion saved for one of the agent's Test sessions."""
    with store.connect() as conn:
        project, actor = store.project(conn, 'shop'), store.actor(conn, 'reviewer', 'reviewer@test')
        session = add_test_session(conn, project, actor, agent_id, a_model(conn, actor), status='waiting', turns=1)
        return str(add_review(conn, session, actor, changes=changes, agent_config=agent_config))


def changes(system_prompt=None, instructions=None, servers=()) -> dict:
    return {'system_prompt': system_prompt, 'instructions': instructions, 'server_instructions': list(servers)}


def apply_review(client, agent_id, review, body, target='agent', **change):
    return client.post(f'/api/agents/{agent_id}/reviews/{review}/apply', headers=HEADERS, json={
        'target': target, 'revision': body['revision'], 'fingerprint': body['fingerprint'], 'changes': changes(**change)})


def without(config, *keys) -> dict:
    return {key: value for key, value in sections(config).items() if key not in keys}


def test_a_suggestion_is_applied_to_this_agent_after_review(store, saved_agent):
    client = saved_agent[0]
    before = get_agent_config('operations', conninfo=store.conninfo)
    review = saved_review(store, 'operations', changes(instructions='Gather evidence first.'))
    source = client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS)
    assert source.status_code == 200, source.text
    body = source.json()
    assert (body['suggestion'], body['changes']['instructions']) == ('Be clearer.', 'Gather evidence first.')
    assert (body['system_prompt'], body['instructions'], body['revision']) == (
        before['system_prompt']['content'], 'Verify every change.', before['revision'])
    host = before['ssh']['servers'][0]
    assert body['servers'] == [{'id': host['id'], 'name': host['name'], 'instructions': 'Verify every change.', 'inherits': True}]
    applied = apply_review(client, 'operations', review, body, system_prompt='Answer from evidence.',
                           instructions='Gather evidence first.')
    assert applied.status_code == 200, applied.text
    assert applied.json() == {'agent_id': 'operations'}
    after = get_agent_config('operations', conninfo=store.conninfo)
    assert (after['system_prompt'], after['instructions']) == ({'content': 'Answer from evidence.'}, 'Gather evidence first.')
    assert after['ssh']['servers'][0]['instructions'] == 'Gather evidence first.'  # an inheriting server follows
    assert without(after, 'prompt', 'instructions') == without(before, 'prompt', 'instructions')
    assert after['revision'] == before['revision'] + 1
    again = apply_review(client, 'operations', review, body, instructions='Older text.')
    assert again.status_code == 409 and again.json()['error']['message'] == AGENT_CHANGED
    with store.connect() as conn:
        audit = conn.execute("SELECT details FROM ka.audit_events WHERE action='agent.suggestion.applied'").fetchall()
    assert [row['details'] for row in audit] == [{'agent_id': 'operations', 'source_agent_id': 'operations', 'review_id': review, 'target': 'agent'}]


def test_server_instructions_become_an_override_and_unknown_servers_are_refused(store, saved_agent):
    client, server = saved_agent[0], saved_agent[1]
    review = saved_review(store, 'operations', changes())
    body = client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS).json()
    forged = apply_review(client, 'operations', review, body, servers=[{'server_id': str(uuid4()), 'instructions': 'Forged'}])
    assert forged.status_code == 422 and forged.json()['error']['message'] == UNKNOWN_SERVER
    blank = apply_review(client, 'operations', review, body, system_prompt='  ')
    assert blank.status_code == 400 and blank.json()['error']['message'] == PROMPT_REQUIRED
    applied = apply_review(client, 'operations', review, body,
                           servers=[{'server_id': server['id'], 'instructions': 'Check only the queue.'}])
    assert applied.status_code == 200, applied.text
    host = get_agent_config('operations', conninfo=store.conninfo)['ssh']['servers'][0]
    assert (host['instructions'], host['instructions_source']) == ('Check only the queue.', 'server_override')
    assert (host['skills_source'], host['mcp_source']) == ('agent', 'agent')  # a new override inherits the rest
    assert client.get(f'/api/agents/code/reviews/{review}', headers=HEADERS).status_code == 404  # another agent's


def test_a_suggestion_applied_to_a_new_clone_leaves_the_agent_as_it_was(store, saved_agent):
    client = saved_agent[0]
    before = get_agent_config('operations', conninfo=store.conninfo)
    review = saved_review(store, 'operations', changes(system_prompt='Answer from evidence.'))
    body = client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS).json()
    applied = apply_review(client, 'operations', review, body, target='clone', system_prompt='Answer from evidence.')
    assert applied.status_code == 200, applied.text
    clone_id = applied.json()['agent_id']
    assert clone_id != 'operations'
    assert sections(get_agent_config('operations', conninfo=store.conninfo)) == sections(before)
    copied = get_agent_config(clone_id, conninfo=store.conninfo)
    assert copied['system_prompt'] == {'content': 'Answer from evidence.'}
    assert without(copied, 'prompt') == without(before, 'prompt')
    assert client.get(f'/api/agents/{clone_id}', headers=HEADERS).json()['cloned_from'] == 'operations'
    with store.connect() as conn:
        audit = conn.execute('''SELECT action,details FROM ka.audit_events
            WHERE action IN ('agent.cloned','agent.suggestion.applied') ORDER BY id''').fetchall()
    assert [(row['action'], row['details']) for row in audit] == [
        ('agent.cloned', {'agent_id': clone_id, 'cloned_from': 'operations'}),
        ('agent.suggestion.applied', {'agent_id': clone_id, 'source_agent_id': 'operations', 'review_id': review,
                                      'target': 'clone'})]


def test_a_suggestion_from_a_per_run_chat_never_narrows_the_agent(store, saved_agent):
    client = saved_agent[0]
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET resources='per_run' WHERE id='operations'")
    before = get_agent_config('operations', conninfo=store.conninfo)
    review = saved_review(store, 'operations', changes(instructions='Be brief.'), agent_config=NARROWED)
    body = client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS).json()
    assert apply_review(client, 'operations', review, body, instructions='Be brief.').status_code == 200
    after = get_agent_config('operations', conninfo=store.conninfo)
    assert without(after, 'instructions') == without(before, 'instructions')
    assert after['code']['projects'] == before['code']['projects']



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
    client, server = saved_agent[0], saved_agent[1]
    before = get_agent_config('operations', conninfo=store.conninfo)
    review = saved_review(store, 'operations', changes())
    body = client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS).json()
    for change in ({'instructions': 'Be\x00brief.'},
                   {'servers': [{'server_id': server['id'], 'instructions': 'Read\x00logs.'}]}):
        refused = apply_review(client, 'operations', review, body, **change)
        assert refused.status_code == 400 and refused.json()['error']['message'] == INVALID_TEXT
    for change in ({'instructions': 'x' * 40001}, {'system_prompt': 'x' * 100001},
                   {'servers': [{'server_id': server['id'], 'instructions': 'x' * 40001}]}):
        assert apply_review(client, 'operations', review, body, **change).status_code == 400
    assert sections(get_agent_config('operations', conninfo=store.conninfo)) == sections(before)


def test_instructions_saved_elsewhere_make_the_suggestion_stale(store, saved_agent):
    client = saved_agent[0]
    review = saved_review(store, 'operations', changes())
    body = client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS).json()
    with store.connect() as conn:  # as the instructions endpoint does: agents.revision stays
        conn.execute("""UPDATE ka.agent_configs SET instructions='Saved meanwhile.',revision=revision+1
            WHERE agent='operations' AND server_id IS NULL""")
    assert get_agent_config('operations', conninfo=store.conninfo)['revision'] == body['revision']
    stale = apply_review(client, 'operations', review, body, instructions='Be brief.')
    assert stale.status_code == 409 and stale.json()['error']['message'] == AGENT_CHANGED
    assert get_agent_config('operations', conninfo=store.conninfo)['instructions'] == 'Saved meanwhile.'


def test_a_deleted_agent_has_no_suggestions(store, saved_agent):
    client = saved_agent[0]
    review = saved_review(store, 'operations', changes())
    body = client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS).json()
    revision = client.get('/api/agents/operations', headers=HEADERS).json()['revision']
    assert client.request('DELETE', '/api/agents/operations', headers=HEADERS,
                          json={'revision': revision, 'confirmed': True, 'with_history': True}).status_code == 204
    for response in (client.get(f'/api/agents/operations/reviews/{review}', headers=HEADERS),
                     apply_review(client, 'operations', review, body, instructions='Be brief.')):
        assert response.status_code == 404 and response.json()['error']['message'] == REVIEW_NOT_FOUND
