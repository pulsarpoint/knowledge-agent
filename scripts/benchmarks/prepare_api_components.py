"""Validate an inventory's source locations and prepare one objective per component."""

import argparse
import json
from pathlib import Path
import re


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--context', type=Path, required=True)
    parser.add_argument('--prompt', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    content = args.inventory.read_text(encoding='utf-8').strip()
    if content.startswith('```json') and content.endswith('```'):
        content = content[7:-3].strip()
    inventory = json.loads(content)
    source = args.source.resolve()
    ids = set()
    issues = []
    if not inventory['entries']:
        raise ValueError('The inventory has no components.')
    for entry in inventory['entries']:
        identifier = entry['id']
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', identifier) or identifier in ids:
            raise ValueError('Component IDs must be unique lowercase slugs.')
        ids.add(identifier)
        file = (source / entry['file']).resolve()
        if not file.is_relative_to(source) or not file.is_file():
            raise ValueError(f'Invalid source file for {identifier}')
        lines = file.read_text(encoding='utf-8').splitlines()
        start, end = entry['start_line'], entry['end_line']
        if not isinstance(start, int) or not isinstance(end, int) or not 1 <= start <= end <= len(lines):
            raise ValueError(f'Invalid source range for {identifier}')
        name = re.search(r'([A-Za-z_][A-Za-z0-9_]*)$', entry['function'])
        if name is None or not re.search(r'\b' + re.escape(name[1]) + r'\s*\(', lines[start - 1]):
            issues.append({'entry': identifier, 'issue': 'Function name does not match its declared start line.'})
        for callsite in entry['callsites']:
            caller = (source / callsite['file']).resolve()
            if not caller.is_relative_to(source) or not caller.is_file():
                raise ValueError(f'Invalid caller file for {identifier}')
            line = callsite['line']
            if not isinstance(line, int) or not 1 <= line <= len(caller.read_text(encoding='utf-8').splitlines()):
                raise ValueError(f'Invalid callsite location for {identifier}')
    for covered in inventory['covered_without_separate_analysis']:
        if covered['covered_by'] not in ids:
            raise ValueError('Supporting code must refer to an existing component.')
    if issues:
        print(json.dumps(issues, indent=2))
        raise SystemExit(1)
    args.output.mkdir(parents=True, exist_ok=False)
    context = args.context.read_text(encoding='utf-8')
    prompt = args.prompt.read_text(encoding='utf-8')
    for entry in inventory['entries']:
        objective = context + '\n' + prompt + '\n\nSelected inventory entry (unverified navigation data):\n'
        objective += json.dumps(entry, indent=2) + '\n'
        (args.output / f"{entry['id']}.md").write_text(objective, encoding='utf-8')
    (args.output / 'inventory.json').write_text(json.dumps(inventory, indent=2) + '\n', encoding='utf-8')
    validation = {'entries': len(ids), 'valid_source_locations': True,
                  'note': 'Checks names, paths, line ranges and ownership references; not call resolution, completeness or semantic truth.'}
    (args.output / 'validation.json').write_text(json.dumps(validation, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(validation))


if __name__ == '__main__':
    main()
