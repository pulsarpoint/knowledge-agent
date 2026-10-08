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
