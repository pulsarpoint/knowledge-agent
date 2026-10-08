"""Draft snapshots, explicit activation, and session isolation through the native SDK."""
import json
from agents import AgentOutputSchema, ModelBehaviorError
from types import SimpleNamespace
from uuid import uuid4

import pytest

from knowledge_admin.agent_versions import TuningSuggestion
from test_agent_runs import direct, events
from test_sdk_agent import responses_api
from test_repositories import HEADERS


def draft(client, agent_id, instructions='Draft instructions', **source):
    path = f'/api/agents/{agent_id}/versions'
    prepared = client.get(path+'/source', headers=HEADERS, params=source).json()
    value = {'label':instructions,'source_hash':prepared['source_hash'],
             **{'source_'+key:value for key,value in source.items()},
             'changes':{'system_prompt':None,'instructions':instructions,'server_instructions':[]}}
    result = client.post(path, headers=HEADERS, json=value)
    assert result.status_code == 201,result.text
    return result.json()


def test_drafts_activation_rollback_and_pinned_native_conversations(direct, store, responses_api):
    client, agent_id, run_path = direct
    path = f'/api/agents/{agent_id}/versions'
    initial = client.get(f'/api/agents/{agent_id}/config',headers=HEADERS).json()
    proposed = draft(client,agent_id,'Always include an uncertainty note.')
    versions = client.get(path,headers=HEADERS).json()
    baseline_id = versions['active_id']
    assert [(v['version'],v['status']) for v in versions['versions']] == [(2,'draft'),(1,'active')]
    baseline = client.post(run_path,headers=HEADERS,json={'task':'First baseline message'})
    baseline_session = events(baseline)[0]['session_id']
    experimental = client.post(run_path,headers=HEADERS,json={'task':'First draft message','version_id':proposed['id']})
    draft_session = events(experimental)[0]['session_id']
    assert baseline_session != draft_session
    assert 'Always include an uncertainty note.' in json.dumps(responses_api[1][-1]['body'])
    assert client.post(path+'/'+proposed['id']+'/activate',headers=HEADERS,json={'expected_active_id':baseline_id}).status_code == 200
    active = client.get(f'/api/agents/{agent_id}/config',headers=HEADERS).json()
    assert active['config_version']['id'] == proposed['id']
    assert active['code'] == initial['code'] and active['model'] == initial['model']
    followup = client.post(run_path,headers=HEADERS,json={'task':'Continue baseline','session_id':baseline_session})
    config = events(followup)[-1]['context']['config']
    assert config['instructions'] == initial['instructions'] and config['config_version']['id'] == baseline_id
    assert 'First baseline message' in json.dumps(responses_api[1][-1]['body'])
    assert client.post(run_path,headers=HEADERS,json={'task':'Switch','session_id':baseline_session,'version_id':proposed['id']}).status_code == 409
    assert client.post(path+'/'+baseline_id+'/activate',headers=HEADERS,json={'expected_active_id':baseline_id}).status_code == 409
    assert client.post(path+'/'+baseline_id+'/activate',headers=HEADERS,json={'expected_active_id':proposed['id']}).status_code == 200
    assert client.get(f'/api/agents/{agent_id}/config',headers=HEADERS).json()['config_version']['id'] == baseline_id
    assert client.get(f'/api/agents/{agent_id}/sessions/{draft_session}',headers=HEADERS).json()['agent_version_id'] == proposed['id']
    # Mutable working settings do not leak into an active snapshot or session.
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_configs SET instructions='New working copy' WHERE agent=%s AND server_id IS NULL", (agent_id,))
    assert client.get(path+'/source',headers=HEADERS).json()['config']['instructions'] == 'New working copy'
    assert client.get(f'/api/agents/{agent_id}/config',headers=HEADERS).json()['instructions'] == initial['instructions']
    with store.connect() as conn:
        conn.execute('UPDATE ka.agents SET enabled=false WHERE id=%s', (agent_id,))
    assert client.post(run_path,headers=HEADERS,json={'task':'Disabled','session_id':draft_session}).status_code == 409


@pytest.mark.parametrize('encoded_changes', [False, True])
def test_save_review_as_draft_uses_reviewed_snapshot_and_keeps_other_fields(direct, store, monkeypatch, encoded_changes):
    client, agent_id, run_path = direct
    result = client.post(run_path,headers=HEADERS,json={'task':'Summarize'})
    session_id = events(result)[0]['session_id']
    async def review(*args,**kwargs):
        assert kwargs['output_type'].model_json_schema()['type'] == 'object'
        changes = {'system_prompt':None,'instructions':'Explain uncertainty.','server_instructions':[]}
        output = json.dumps({'suggestion':'Explain uncertainty.',
                             'changes':json.dumps(changes) if encoded_changes else changes})
        schema = AgentOutputSchema(kwargs['output_type'], strict_json_schema=False)
        return SimpleNamespace(final_output=schema.validate_json(output))
    monkeypatch.setattr('knowledge_admin.agent_sessions.agent',review)
    reviewed = client.post(f'/api/agents/{agent_id}/sessions/{session_id}/review',headers=HEADERS,json={'comment':'Explain uncertainty.'}).json()
    path = f'/api/agents/{agent_id}/versions'
    prepared = client.get(path+'/source',headers=HEADERS,params={'review_id':reviewed['id']}).json()
    assert prepared['changes']['instructions'] == 'Explain uncertainty.'
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_configs SET instructions='Unrelated working edits' WHERE agent=%s AND server_id IS NULL", (agent_id,))
    created = client.post(path,headers=HEADERS,json={'label':'Reviewed draft','source_review_id':reviewed['id'],
        'source_hash':prepared['source_hash'],'changes':prepared['changes']})
    assert created.status_code == 201,created.text
    snapshot = client.get(f'/api/agents/{agent_id}/config',headers=HEADERS,params={'version_id':created.json()['id']}).json()
    assert snapshot['instructions'] == 'Explain uncertainty.'
    assert snapshot['system_prompt'] == prepared['config']['system_prompt']
    assert client.get(f'/api/agents/{agent_id}/config',headers=HEADERS).json()['instructions'] == 'Unrelated working edits'
    assert 'direct-test-secret' not in json.dumps(snapshot)


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


def test_draft_validation_cross_agent_and_stale_source(direct, store):
    client, agent_id, _ = direct
    path = f'/api/agents/{agent_id}/versions'
    prepared = client.get(path+'/source',headers=HEADERS).json()
    payload = {'label':'Draft','source_hash':prepared['source_hash'],
               'changes':{'system_prompt':None,'instructions':'Changed','server_instructions':[]}}
    assert client.post(path,json=payload).status_code == 403
    assert client.post(path,headers=HEADERS,json={**payload,'config':{'ssh':{'enabled':True}}}).status_code == 400
    assert client.post(path,headers=HEADERS,json={**payload,'changes':{**payload['changes'],'system_prompt':'  '}}).status_code == 422
    assert client.post(path,headers=HEADERS,json={**payload,'changes':{**payload['changes'],'server_instructions':[{'server_id':str(uuid4()),'instructions':'Forged'}]}}).status_code == 422
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_configs SET instructions='Concurrent edit' WHERE agent=%s AND server_id IS NULL",(agent_id,))
    assert client.post(path,headers=HEADERS,json=payload).status_code == 409
    created = draft(client,agent_id)
    assert client.get('/api/agents/code/versions/source',headers=HEADERS,params={'version_id':created['id']}).status_code == 404
    assert client.post(f"/api/agents/code/versions/{created['id']}/activate",headers=HEADERS,json={'expected_active_id':None}).status_code == 404


def test_invalid_tuning_output_is_corrected_once_before_saving(direct, monkeypatch):
    client, agent_id, path = direct
    initial = client.post(path,headers=HEADERS,json={'task':'Check formatting.'})
    session_id = events(initial)[0]['session_id']
    calls = []
    async def review(config,task,**kwargs):
        calls.append(json.loads(task))
        output = {'unexpected':'bad shape'} if len(calls)==1 else {
            'suggestion':'Be concise.','changes':{'system_prompt':None,'instructions':'Be concise.','server_instructions':[]}}
        if len(calls)==1:
            raise ModelBehaviorError('Invalid model output')
        return SimpleNamespace(final_output=kwargs['output_type'].model_validate(output))
    monkeypatch.setattr('knowledge_admin.agent_sessions.agent',review)
    result = client.post(f'/api/agents/{agent_id}/sessions/{session_id}/review',headers=HEADERS,json={'comment':'Be concise.'})
    assert result.status_code == 200,result.text
    assert len(calls) == 2 and 'correction' in calls[1] and 'output_schema' in calls[0]
    assert result.json()['changes']['instructions'] == 'Be concise.'
