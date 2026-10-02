"""Native Python step calls for the existing DSPy RLM benchmark sandbox."""

import json
from datetime import datetime, timezone
from typing import Any

import dspy
from dspy.primitives.repl_types import REPLHistory
from openai import OpenAI

from rlm_protocol import response_shape


def python_call_code(output: list[dict[str, Any]]) -> str:
    """Validate all Python calls, then execute them in response order in the shared REPL."""
    calls = [item for item in output if item.get('type') == 'function_call']
    if not calls:
        raise ValueError('Expected an execute_python function call')
    codes, call_ids = [], set()
    for call in calls:
        if call.get('name') != 'execute_python' or not isinstance(call.get('call_id'), str) or not call['call_id']:
            raise ValueError('Wrong tool name or missing call ID')
        if call['call_id'] in call_ids:
            raise ValueError('Duplicate tool call ID')
        call_ids.add(call['call_id'])
        if not isinstance(call.get('arguments'), str):
            raise ValueError('Tool arguments must be a JSON string')
        try:
            arguments = json.loads(call['arguments'])
        except json.JSONDecodeError:
            raise ValueError('Tool arguments are not valid JSON') from None
        if not isinstance(arguments, dict) or set(arguments) != {'code'}:
            raise ValueError('Tool arguments must contain exactly the code field')
        if not isinstance(arguments['code'], str) or not arguments['code'].strip():
            raise ValueError('Tool code must be a nonempty string')
        codes.append(arguments['code'])
    return '\n\n'.join(codes)


class NativePythonStep(dspy.Module):
    """Produce the same RLM step using a native function call, without text parsing."""

    def __init__(self, client: OpenAI, model: str, reasoning_effort: str, instructions: str):
        super().__init__()
        self.client = client
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.instructions = instructions + (
            '\n\nSend each next Python step through the execute_python function tool. '
            'Its code argument must be a Python source string, without Markdown fences. '
            'The tool executes in the existing persistent REPL. Read its results in repl_history '
            'on the next turn. Complete the task by calling SUBMIT(answers=answers) inside Python.'
        )
        self.tool = {
            'type': 'function', 'name': 'execute_python',
            'description': 'Execute one Python step in the persistent RLM sandbox. Source tools and llm_query are available there. Print relevant results; use SUBMIT(answers=answers) only after validation.',
            'parameters': {'type': 'object', 'properties': {'code': {'type': 'string'}},
                           'required': ['code'], 'additionalProperties': False},
            'strict': True,
        }
        self.calls: list[dict[str, Any]] = []
        self.protocol: list[dict[str, Any]] = []

    def forward(self, variables_info: list[str], repl_history: REPLHistory, iteration: str) -> dspy.Prediction:
        task_input = json.dumps({'variables_info': variables_info,
            'repl_history': repl_history.format(), 'iteration': iteration})
        print(f'Native execute_python call started ({iteration})', flush=True)
        response = self.client.responses.create(
            model=self.model, instructions=self.instructions,
            input=[{'role': 'user', 'content': task_input}],
            # DeepSeek thinking mode rejects required/named tool choices.
            # Only execute_python is offered; validate the returned call below.
            tools=[self.tool], tool_choice='auto',
            parallel_tool_calls=False, reasoning={'effort': self.reasoning_effort}, store=False,
        )
        self.calls.append({'role': 'root', 'timestamp': datetime.now(timezone.utc).isoformat(),
            'usage': response.usage.model_dump() if response.usage is not None else {},
            'input_characters': len(self.instructions) + len(task_input), 'status': response.status})
        output = [item.model_dump() for item in response.output]
        diagnostic = {'iteration': iteration, 'status': response.status,
            'item_types': [item.get('type') for item in output],
            'tool_calls': [{'name': item.get('name'), 'call_id': item.get('call_id'),
                            'arguments_shape': response_shape(item['arguments'])
                                if isinstance(item.get('arguments'), str) else {'type': type(item.get('arguments')).__name__}}
                           for item in output if item.get('type') == 'function_call']}
        self.protocol.append(diagnostic)
        if response.status != 'completed':
            raise ValueError('Native step response did not complete; no code executed')
        try:
            code = python_call_code(output)
        except ValueError as error:
            diagnostic['validation_error'] = str(error)
            raise
        diagnostic['validated'] = True
        print('Native execute_python arguments validated', flush=True)
        # RLM executes this in its existing interpreter and records the result.
        # No host eval/exec or separate sandbox is introduced here.
        return dspy.Prediction(reasoning='', code=code)
