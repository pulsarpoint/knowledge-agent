import json
import unittest
from types import SimpleNamespace

import dspy
from dspy.primitives.repl_types import REPLHistory

from rlm_native import NativePythonStep, python_call_code


class NativePythonCallTests(unittest.TestCase):
    def call(self, arguments):
        return {'type': 'function_call', 'name': 'execute_python', 'call_id': 'call_test', 'arguments': arguments}

    def test_valid_code_is_preserved_without_execution(self):
        code = 'data = get_sql_query("DisablePersonalLimit", "queries.sql")\nprint(data)'
        self.assertEqual(python_call_code([{'type': 'reasoning'}, self.call(json.dumps({'code': code}))]), code)

    def test_rejects_missing_duplicate_or_wrong_tools(self):
        valid = self.call('{"code":"print(1)"}')
        for output in ([], [{'type': 'message', 'text': 'print(1)'}], [valid, valid],
                       [{**valid, 'name': 'shell'}], [{**valid, 'call_id': ''}]):
            with self.subTest(output=output), self.assertRaises(ValueError):
                python_call_code(output)

    def test_rejects_invalid_argument_shapes(self):
        for args in ('{', '[]', 'null', '{"code":1}', '{"code":" "}',
                     '{"code":"print(1)","extra":true}', {'code': 'print(1)'}):
            with self.subTest(arguments=args), self.assertRaises(ValueError):
                python_call_code([self.call(args)])

    def test_multiple_calls_preserve_order_and_reject_any_invalid_call(self):
        first = self.call(json.dumps({'code': 'total = 2'}))
        second = {**self.call(json.dumps({'code': 'total *= 3'})), 'call_id': 'call_second'}
        self.assertEqual(python_call_code([first, second]), 'total = 2\n\ntotal *= 3')
        with self.assertRaises(ValueError):
            python_call_code([first, {**second, 'name': 'shell'}])

    def test_incomplete_response_cannot_yield_a_step(self):
        response = SimpleNamespace(status='incomplete', usage=None, output=[])
        client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: response))
        step = NativePythonStep(client, 'fixture', 'max', 'Fixture instructions')
        with self.assertRaisesRegex(ValueError, 'did not complete'):
            step(variables_info=[], repl_history=REPLHistory(), iteration='1/8')

    def test_real_sandbox_preserves_state_and_calls_existing_source_tool(self):
        requests, lookups = [], []
        codes = iter(('total = lookup() + numbers[0]\nprint(total)', 'SUBMIT(total=total)'))

        def lookup() -> int:
            """Read a fixed fixture value."""
            lookups.append(True)
            return 3

        def create(**kwargs):
            requests.append(kwargs)
            item = self.call(json.dumps({'code': next(codes)}))
            output = [SimpleNamespace(model_dump=lambda: item)]
            if len(requests) == 1:
                extra = {**self.call(json.dumps({'code': 'total *= 2'})), 'call_id': 'call_second'}
                output.append(SimpleNamespace(model_dump=lambda: extra))
            return SimpleNamespace(status='completed', usage=None,
                output=output)

        agent = dspy.RLM('numbers -> total: int', max_iters=2, max_llm_calls=0, tools=[lookup])
        agent.generate_action = NativePythonStep(
            SimpleNamespace(responses=SimpleNamespace(create=create)), 'fixture', 'max',
            agent.generate_action.signature.instructions)
        result = agent(numbers=[2])
        self.assertEqual(result.total, 10)
        self.assertEqual(lookups, [True])
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0]['tool_choice'], 'auto')
        second = json.loads(requests[1]['input'][0]['content'])
        self.assertIn('Output (', second['repl_history'])
        self.assertEqual(result.trajectory[0]['output'].strip(), '5')
        self.assertNotEqual(result.final_reasoning, 'Extract forced final output')


if __name__ == '__main__':
    unittest.main()
