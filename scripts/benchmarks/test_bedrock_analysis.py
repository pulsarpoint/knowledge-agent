import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from bedrock_analysis import BedrockAnalysis
from rlm_evidence import SourceEvidence, sql_inventory
from rlm_handler import public_calls
from rlm_staged import run_staged


class BedrockAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.model = {'model': 'eu.anthropic.claude-opus-5', 'aws_region': 'eu-central-1'}
        self.answer = {'answers': [{'id': 'q', 'value': 'yes', 'explanation': 'Selects IDs.',
            'observation_ids': [], 'evidence': [{'fragment_id': 'p/q.sql:1-2', 'start_line': 2, 'end_line': 2}]}]}
        self.payload = {'stop_reason': 'end_turn', 'content': [
            {'type': 'thinking', 'thinking': 'PRIVATE_THINKING', 'signature': 'PRIVATE_SIGNATURE'},
            {'type': 'text', 'text': json.dumps(self.answer)}],
            'usage': {'input_tokens': 10, 'cache_read_input_tokens': 40,
                      'cache_creation_input_tokens': 20, 'output_tokens': 50}}
        files = {'p/q.sql': '-- name: Q :many\nSELECT id FROM items;'}
        self.evidence = SourceEvidence({'files': files, 'graphs': {}}, sql_inventory(files))
        self.task = {'id': 'q', 'resources': [{'query': 'Q', 'path_suffix': 'q.sql'}],
                     'questions': [{'id': 'q', 'allowed_values': ['yes', 'no']}]}

    def test_native_accounting_and_validated_text_without_thinking_or_key(self):
        child = BedrockAnalysis(self.model, 'SECRET_KEY', effort='xhigh', max_tokens=128000)
        with patch('bedrock_analysis.httpx.post', return_value=httpx.Response(200, json=self.payload)) as post:
            with TemporaryDirectory() as temporary:
                result = run_staged(self.evidence, self.task, child, 'Exact prompt.', Path(temporary))
                self.assertEqual(result['status'], 'completed')
                artifacts = ''.join(p.read_text(encoding='utf-8') for p in Path(temporary).iterdir())
                self.assertNotIn('PRIVATE_THINKING', artifacts)
                self.assertNotIn('PRIVATE_SIGNATURE', artifacts)
                self.assertNotIn('SECRET_KEY', artifacts)
        body = post.call_args.kwargs['json']
        self.assertEqual(body['system'][0]['text'], 'Exact prompt.')
        self.assertEqual(body['thinking'], {'type': 'adaptive'})
        self.assertEqual(body['output_config'], {'effort': 'xhigh'})
        self.assertEqual(body['max_tokens'], 128000)
        self.assertEqual(body['system'][-1]['cache_control'], {'type': 'ephemeral'})
        self.assertEqual(body['messages'][-1]['content'][-1]['cache_control'], {'type': 'ephemeral'})
        calls = public_calls(child, 'analysis')
        self.assertEqual(calls[0]['usage']['input_tokens'], 70)
        self.assertEqual(calls[0]['usage']['input_tokens_details']['cached_tokens'], 40)
        self.assertEqual(calls[0]['usage']['cache_creation_input_tokens'], 20)
        self.assertIsNone(calls[0]['usage']['output_tokens_details']['reasoning_tokens'])
        self.assertEqual(calls[0]['provider_usage'], self.payload['usage'])
        self.assertNotIn('PRIVATE_', json.dumps(calls))

    def test_incomplete_response_preserves_usage_but_never_submits(self):
        child = BedrockAnalysis(self.model, 'SECRET_KEY', effort='xhigh', max_tokens=128000)
        payload = {**self.payload, 'stop_reason': 'max_tokens'}
        with patch('bedrock_analysis.httpx.post', return_value=httpx.Response(200, json=payload)):
            with TemporaryDirectory() as temporary:
                with self.assertRaisesRegex(ValueError, 'did not complete'):
                    run_staged(self.evidence, self.task, child, 'Prompt.', Path(temporary))
                self.assertFalse((Path(temporary) / 'submitted.json').exists())
        self.assertEqual(public_calls(child, 'analysis')[0]['usage']['output_tokens'], 50)
        self.assertEqual(public_calls(child, 'analysis')[0]['stop_reason'], 'max_tokens')

    def test_http_failure_does_not_leak_body_or_retry(self):
        child = BedrockAnalysis(self.model, 'SECRET_KEY', effort='xhigh', max_tokens=128000)
        with patch('bedrock_analysis.httpx.post', return_value=httpx.Response(403, text='SECRET_KEY')) as post:
            with self.assertRaisesRegex(RuntimeError, '^Bedrock returned HTTP 403$'):
                child(messages=[{'role': 'user', 'content': 'Question'}])
        self.assertEqual(post.call_count, 1)
        self.assertEqual(child.history, [])


if __name__ == '__main__':
    unittest.main()
