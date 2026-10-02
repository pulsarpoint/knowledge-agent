"""Inspect DSPy response structure without saving prompts, reasoning, or secrets."""

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from dspy.utils.callback import BaseCallback


def response_shape(text: str) -> dict[str, Any]:
    """Describe serialization only; never return text or field values."""
    shape = {
        'characters': len(text), 'sha256': hashlib.sha256(text.encode()).hexdigest(),
        'field_markers': re.findall(r'\[\[ ## (\w+) ## \]\]', text),
        'fences': re.findall(r'^```(\w*)', text, re.MULTILINE),
    }
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        shape['json_type'] = None
    else:
        shape['json_type'] = type(parsed).__name__
        if isinstance(parsed, dict):
            shape['json_fields'] = {k: type(v).__name__ for k, v in parsed.items()}
    try:
        program = ast.parse(text)
    except SyntaxError:
        shape['python_statements'] = None
    else:
        shape['python_statements'] = [type(node).__name__ for node in program.body]
        shape['python_calls'] = sorted({
            node.func.id if isinstance(node.func, ast.Name) else node.func.attr
            for node in ast.walk(program) if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Name, ast.Attribute))
        })
    return shape


class ProtocolLog(BaseCallback):
    """Record both adapter attempts, including the one hidden by fallback."""

    def __init__(self, path: Path):
        self.path = path
        self.pending: dict[str, dict[str, Any]] = {}

    def on_adapter_parse_start(self, call_id, instance, inputs):
        self.pending[call_id] = {
            'adapter': type(instance).__name__,
            'expected_fields': list(inputs['signature'].output_fields),
            'response': response_shape(inputs['completion']),
        }

    def on_adapter_parse_end(self, call_id, outputs, exception):
        event = self.pending.pop(call_id)
        event['parsed_fields'] = list(outputs or {})
        event['error_type'] = type(exception).__name__ if exception is not None else None
        with self.path.open('a', encoding='utf-8') as file:
            file.write(json.dumps(event) + '\n')


def protocol_calls(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare provider message text with DSPy's extracted text, excluding reasoning."""
    calls = []
    for item in history:
        provider = item['response'].model_dump()
        text = ''.join(
            part.get('text', '')
            for output in provider.get('output', []) if output.get('type') == 'message'
            for part in output.get('content', []) if part.get('type') == 'output_text'
        )
        extracted = [o.get('text', '') if isinstance(o, dict) else o for o in item['outputs']]
        response_format = item.get('kwargs', {}).get('response_format')
        calls.append({
            'timestamp': item['timestamp'], 'provider_status': provider.get('status'),
            'provider_item_types': [o.get('type') for o in provider.get('output', [])],
            'requested_format': response_format.get('type') if isinstance(response_format, dict)
                else type(response_format).__name__ if response_format is not None else None,
            'provider_text': response_shape(text),
            'extracted_text': [response_shape(o) for o in extracted if isinstance(o, str)],
            'text_preserved': extracted == [text],
        })
    return calls
