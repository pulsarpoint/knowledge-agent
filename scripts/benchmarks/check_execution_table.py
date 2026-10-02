"""Check table references and source quotations without claiming semantic verification."""

import argparse
import json
from pathlib import Path
import re


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--table', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    content = args.table.read_text(encoding='utf-8').strip()
    if content.startswith('```json') and content.endswith('```'):
        content = content[7:-3].strip()
    table = json.loads(content)
    source = args.source.resolve()
    issues, citations = [], []
    steps = table['steps']
    ids = {step['id'] for step in steps}
    if len(ids) != len(steps):
        issues.append({'reason': 'duplicate step IDs'})
    if not steps or not table['paths'] or not table['invariants']:
        issues.append({'reason': 'steps, paths and invariants must not be empty'})
    for group in ('steps', 'paths', 'invariants'):
        for index, row in enumerate(table[group]):
            owner = f'{group}[{index}]'
            references = row.get('calls', row.get('steps', row.get('step_ids', [])))
            unknown = set(references) - ids
            if unknown:
                issues.append({'row': owner, 'reason': 'unknown step IDs', 'ids': sorted(unknown)})
            if not row.get('evidence'):
                issues.append({'row': owner, 'reason': 'missing source evidence'})
            for evidence in row.get('evidence', []):
                path = (source / evidence['path']).resolve()
                if not path.is_relative_to(source) or not path.is_file():
                    issues.append({'row': owner, 'reason': 'invalid source path', 'evidence': evidence})
                    continue
                lines = path.read_text(encoding='utf-8').splitlines()
                start, end = evidence['start_line'], evidence['end_line']
                if not isinstance(start, int) or not isinstance(end, int) or not 1 <= start <= end <= len(lines):
                    issues.append({'row': owner, 'reason': 'invalid source range', 'evidence': evidence})
                    continue
                quotation = re.sub(r'\s+', ' ', evidence['quote']).strip()
                selected = re.sub(r'\s+', ' ', '\n'.join(lines[start - 1:end])).strip()
                if not quotation or quotation not in selected:
                    issues.append({'row': owner, 'reason': 'quotation not found in cited range', 'evidence': evidence})
                else:
                    citations.append({'row': owner, **evidence})
    result = {'valid_structure_and_quotations': not issues, 'steps': len(steps),
              'paths': len(table['paths']), 'invariants': len(table['invariants']),
              'matching_quotations': len(citations), 'issues': issues,
              'note': 'Checks source locations, quotations (whitespace normalized) and step references. '
                      'Does not verify reachability, execution order, effects, coverage or semantic truth.'}
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))
    if issues:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
