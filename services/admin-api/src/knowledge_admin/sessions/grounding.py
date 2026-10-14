"""Turn a grounded run's recorded excerpts into cited sources and a coverage decision."""

import re
from typing import Any

CITATION = re.compile(r'\[(\d{1,4})\]')
FENCE = re.compile(r'```.*?```|~~~.*?~~~|`[^`\n]*`', re.DOTALL)

POLICY = (
    '\nAnswer only from excerpts returned by search_documents or read_document in this conversation. '
    'Search before answering any factual question. Cite every claim with its excerpt number in square brackets, such as [2]. '
    'Never cite a number that a tool did not return. Never use general knowledge or assumptions for factual claims. '
    'When the excerpts do not answer the question, call report_not_covered with what is missing, then tell the user '
    'that the documentation does not cover it and what is missing.'
)


def empty_state() -> dict[str, Any]:
    return {'excerpts': [], 'searches': 0, 'empty_searches': 0, 'reported': None}


def result(answer: str, state: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the cited sources and coverage of a grounded run, or None for other runs."""
    if state is None:
        return None
    by_number = {excerpt['n']: excerpt for excerpt in state['excerpts']}
    cited = sorted({int(number) for number in CITATION.findall(FENCE.sub('', answer))} & by_number.keys())
    sources = [
        {
            'n': n,
            'path': f'{by_number[n]["collection"]}/{by_number[n]["path"]}',
            'heading_path': by_number[n]['collection'],
            'anchor': None,
        }
        for n in cited
    ]
    searched_nothing = state['searches'] > 0 and state['empty_searches'] == state['searches']
    return {
        'sources': sources,
        'not_covered': state['reported'] is not None or searched_nothing,
        'near_misses': [
            {'path': f'{e["collection"]}/{e["path"]}', 'heading_path': e['collection']} for e in state['excerpts'][:3]
        ],
        'results': len(state['excerpts']),
        'reason': state['reported'],
    }
