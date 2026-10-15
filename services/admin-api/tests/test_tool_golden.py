"""Every tool exactly as the model and the Tools tab see it. Written from the catalog before the decorator conversion;
the conversion must reproduce it byte for byte. Regenerate only for an intended change: WRITE_GOLDEN=1."""

import json
import os
from pathlib import Path

from agents.strict_schema import ensure_strict_json_schema
from knowledge_admin.tools.catalog import CATALOG

GOLDEN = Path(__file__).parent / 'fixtures' / 'tool_schemas.json'


def snapshot() -> list[dict]:
    return [
        {
            'name': spec.name,
            'group': spec.group,
            'label': spec.label,
            'summary': spec.summary,
            'needs': spec.needs,
            'usage': spec.usage,
            'schema': ensure_strict_json_schema(spec.arguments.model_json_schema()),
        }
        for spec in CATALOG.values()
    ]


def test_tools_match_the_golden_snapshot():
    text = json.dumps(snapshot(), indent=2, ensure_ascii=False, sort_keys=True) + '\n'
    if os.environ.get('WRITE_GOLDEN'):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(text)
    assert GOLDEN.read_text() == text
