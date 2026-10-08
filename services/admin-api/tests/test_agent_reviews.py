"""Tuning reviews: the reviewer's output shape, its correction, and (Task 6) applying a reviewed suggestion."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agents import AgentOutputSchema, ModelBehaviorError

from knowledge_admin.agent_reviews import TuningSuggestion
from test_agent_runs import direct, events  # noqa: F401  (fixture)
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
