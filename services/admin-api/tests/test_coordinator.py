"""Exercise native agents-as-tools with the real SDK model/tool loop."""
import asyncio
import json

import pytest
from agents import Agent, MaxTurnsExceeded, ModelSettings, OpenAIResponsesModel
from openai import AsyncOpenAI

from knowledge_admin.coordinator import coordinator
from test_board import team_api


def test_coordinator_uses_each_native_specialist_as_a_tool(team_api):
    activity, child_events = [], []

    async def publish(value):
        activity.append(value)

    async def stream(value):
        child_events.append(value)

    async def output(result):
        return json.dumps({'status':'completed', 'result':str(result.final_output)})

    async def run():
        async with AsyncOpenAI(base_url=team_api[0], api_key='fixture') as client:
            specialists = [Agent(name=name, instructions='Inspect your assigned resources.',
                model=OpenAIResponsesModel(name+'-fixture', client)) for name in ('search','operations')]
            return await coordinator(model=OpenAIResponsesModel('coordinator-fixture', client),
                model_settings=ModelSettings(), system_prompt='Coordinate.', summary_prompt='Write the final response from the evidence.', task='Inspect.',
                tools=[a.as_tool(tool_name='delegate_'+a.name, tool_description='Ask '+a.name,
                    on_stream=stream, custom_output_extractor=output) for a in specialists],
                on_activity=publish, secrets=['private-fixture-key'], max_turns=5,
                max_subtasks=10, timeout_seconds=10)

    result = asyncio.run(run())
    assert 'Findings from search-fixture' in result.final_output
    assert 'Findings from operations-fixture' in result.final_output
    assert child_events and activity
    assert 'private-fixture-key' not in json.dumps(activity)
    assert len(team_api[1]) == 7
    request = team_api[1][0]['body']
    assert {t['name'] for t in request['tools']} == {'delegate_search','delegate_operations'}
    assert all(set(t['parameters']['properties']) == {'input'} for t in request['tools'])
    summary = team_api[1][-1]['body']
    assert not summary['tools']
    assert summary['instructions'] == 'Write the final response from the evidence.'
    assert 'Findings from search-fixture' in json.dumps(summary['input'])
    assert 'Findings from operations-fixture' in json.dumps(summary['input'])
    assert summary['input'][-1]['role'] == 'user'


def test_model_turn_limit_stops_further_delegation(team_api):
    async def publish(value):
        pass

    async def stream(value):
        pass

    async def run():
        async with AsyncOpenAI(base_url=team_api[0], api_key='fixture') as client:
            specialists = [Agent(name=name, model=OpenAIResponsesModel(name+'-fixture', client))
                for name in ('search','operations')]
            await coordinator(model=OpenAIResponsesModel('coordinator-fixture', client),
                model_settings=ModelSettings(), system_prompt='Coordinate.', summary_prompt='Write the final response from the evidence.', task='Inspect.',
                tools=[a.as_tool(tool_name='delegate_'+a.name, tool_description=a.name, on_stream=stream)
                    for a in specialists], on_activity=publish, secrets=[], max_turns=1,
                max_subtasks=20, timeout_seconds=10)

    with pytest.raises(MaxTurnsExceeded):
        asyncio.run(run())
    assert len([r for r in team_api[1] if r['body']['model']=='coordinator-fixture']) == 1
    assert {r['body']['model'] for r in team_api[1]} == {'coordinator-fixture','search-fixture','operations-fixture'}
