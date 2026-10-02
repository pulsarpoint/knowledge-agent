"""Native Claude calls for the isolated function benchmark; no agent runtime."""

from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import quote

import httpx


class BedrockAnalysis:
    """Expose Bedrock text and accounting in the staged runner's call format."""

    def __init__(self, model: dict, key: str, *, effort: str, max_tokens: int):
        self.model = model
        self.key = key
        self.effort = effort
        self.max_tokens = max_tokens
        self.history = []

    def __call__(self, *, messages: list[dict]) -> list[dict]:
        system = [{'type': 'text', 'text': m['content']} for m in messages if m['role'] == 'system']
        conversation = [{'role': m['role'], 'content': [{'type': 'text', 'text': m['content']}]}
                        for m in messages if m['role'] != 'system']
        if system:
            system[-1]['cache_control'] = {'type': 'ephemeral'}
        conversation[-1]['content'][-1]['cache_control'] = {'type': 'ephemeral'}
        body = {'anthropic_version': 'bedrock-2023-05-31', 'system': system,
                'messages': conversation, 'max_tokens': self.max_tokens,
                'thinking': {'type': 'adaptive'}, 'output_config': {'effort': self.effort}}
        url = (f"https://bedrock-runtime.{self.model['aws_region']}.amazonaws.com/model/"
               f"{quote(self.model['model'], safe='')}/invoke")
        response = httpx.post(url, headers={'Authorization': f'Bearer {self.key}'}, json=body, timeout=600)
        if response.status_code != 200:
            # Do not expose request headers or provider error bodies containing user data.
            raise RuntimeError(f'Bedrock returned HTTP {response.status_code}')
        payload = response.json()
        raw = payload['usage']
        reads = raw.get('cache_read_input_tokens', 0) or 0
        writes = raw.get('cache_creation_input_tokens', 0) or 0
        usage = {'input_tokens': raw['input_tokens'] + reads + writes,
                 'input_tokens_details': {'cached_tokens': reads},
                 'cache_creation_input_tokens': writes,
                 'output_tokens': raw['output_tokens'],
                 'output_tokens_details': {'reasoning_tokens': None}}
        outputs = [{'text': ''.join(block['text'] for block in payload['content'] if block['type'] == 'text')}]
        self.history.append({'messages': messages, 'outputs': outputs, 'usage': usage,
            'timestamp': datetime.now(UTC).isoformat(),
            'response': SimpleNamespace(status='completed' if payload['stop_reason'] == 'end_turn' else 'incomplete'),
            'provider_usage': raw, 'stop_reason': payload['stop_reason']})
        # Never retain thinking blocks or signatures. Native usage includes thinking
        # in output tokens but does not provide a separate reasoning-token counter.
        return outputs
