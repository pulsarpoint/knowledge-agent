"""Check question-1 output structure and source citations, not semantic correctness."""
import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--answer', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    packet = json.loads(args.evidence.read_text())
    sources = {source['id']: source for source in packet['sources']}
    answer = json.loads(args.answer.read_text())
    errors = []
    checked = 0
    if answer.get('endpoint') != packet['endpoint']:
        errors.append('The answer changed the endpoint.')
    required = {'inputs': ('location', 'name', 'source_type', 'local_handling'),
                'rules': ('condition_or_operation', 'effect'),
                'later_questions': ('call', 'arguments', 'question')}
    for section, fields in required.items():
        rows = answer.get(section)
        if not isinstance(rows, list) or not rows:
            errors.append(f'{section}: expected a nonempty list.')
            continue
        for index, row in enumerate(rows):
            label = f'{section}[{index}]'
            if not isinstance(row, dict) or any(not isinstance(row.get(key), str) or not row[key].strip() for key in fields):
                errors.append(f'{label}: missing required text fields.')
                continue
            evidence = row.get('evidence')
            if not isinstance(evidence, list) or not evidence:
                errors.append(f'{label}: missing evidence.')
                continue
            for ref in evidence:
                if not isinstance(ref, dict) or ref.get('source_id') not in sources:
                    errors.append(f'{label}: unknown source reference.')
                    continue
                source = sources[ref['source_id']]
                start, end, quote = ref.get('start_line'), ref.get('end_line'), ref.get('quote')
                if (type(start) is not int or type(end) is not int
                        or not source['start_line'] <= start <= end <= source['end_line']
                        or not isinstance(quote, str) or not quote.strip()):
                    errors.append(f'{label}: invalid source range or quote.')
                    continue
                lines = source['code'].splitlines()
                excerpt = '\n'.join(lines[start - source['start_line']:end - source['start_line'] + 1])
                if quote.replace('\r\n', '\n') not in excerpt:
                    errors.append(f"{label}: quote not present in {ref['source_id']}:{start}-{end}.")
                checked += 1
    result = {'status': 'passed' if not errors else 'failed', 'citations_checked': checked, 'errors': errors,
              'limitation': 'Exact quotes and ranges do not establish that the interpretation is correct. Manual semantic review is separate.'}
    args.output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
