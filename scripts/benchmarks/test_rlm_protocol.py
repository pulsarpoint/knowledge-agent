import json
import unittest
from types import SimpleNamespace

from rlm_protocol import protocol_calls, response_shape


class ResponseShapeTests(unittest.TestCase):
    def test_plain_code_is_detected_without_saving_values(self):
        result = response_shape('print("sensitive-source-value")')
        self.assertEqual(result['python_calls'], ['print'])
        self.assertIsNone(result['json_type'])
        self.assertNotIn('sensitive-source-value', json.dumps(result))

    def test_reasoning_and_code_fields_are_not_saved(self):
        result = response_shape(json.dumps({'reasoning': 'private-reasoning-value', 'code': 'print("source-value")'}))
        self.assertEqual(result['json_fields'], {'reasoning': 'str', 'code': 'str'})
        self.assertNotIn('private-reasoning-value', json.dumps(result))
        self.assertNotIn('source-value', json.dumps(result))

    def test_chat_fields_are_detected(self):
        result = response_shape('[[ ## reasoning ## ]]\nprivate-value\n[[ ## code ## ]]\nprint(1)\n[[ ## completed ## ]]')
        self.assertEqual(result['field_markers'], ['reasoning', 'code', 'completed'])
        self.assertNotIn('private-value', json.dumps(result))

    def test_provider_reasoning_is_excluded_and_text_loss_detected(self):
        provider = {'status': 'completed', 'output': [
            {'type': 'reasoning', 'content': [{'type': 'reasoning_text', 'text': 'private-value'}]},
            {'type': 'message', 'content': [{'type': 'output_text', 'text': 'first\nsecond'}]},
        ]}
        history = [{'response': SimpleNamespace(model_dump=lambda: provider),
                    'outputs': [{'text': 'first\nsecond', 'reasoning_content': 'private-value'}],
                    'timestamp': 'test', 'kwargs': {'response_format': {'type': 'json_object'}, 'api_key': 'secret-key'}}]
        record = protocol_calls(history)[0]
        self.assertTrue(record['text_preserved'])
        self.assertNotIn('private-value', json.dumps(record))
        self.assertNotIn('secret-key', json.dumps(record))
        history[0]['outputs'][0]['text'] = 'second'
        self.assertFalse(protocol_calls(history)[0]['text_preserved'])


if __name__ == '__main__':
    unittest.main()
