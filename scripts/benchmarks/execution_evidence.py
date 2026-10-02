"""Export syntax locations and graph candidates for an endpoint source collection.

This Go benchmark adds no resolver or control-flow engine. Tree-sitter provides
syntax; CodeGraph's previously exported SDK contexts provide target candidates.
"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import time

from knowledge_admin.tools.ast_grep import scan_source


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    source = args.source.resolve()
    nodes = json.loads((args.evidence / 'packet-nodes.json').read_text())
    contexts = json.loads((args.evidence / 'source-contexts.json').read_text())
    functions = [node for node in nodes if node['kind'] in ('function', 'method') and node['language'] == 'go']
    files = sorted({node['filePath'] for node in functions})
    manifest = json.loads((args.evidence / 'packet-manifest.json').read_text())
    hashes = {item['file']: item['source_sha256'] for item in manifest['files']}
    for file in files:
        path = (source / file).resolve()
        if not path.is_relative_to(source) or hashlib.sha256(path.read_bytes()).hexdigest() != hashes[file]:
            raise ValueError(f'Source no longer matches the collection: {file}')
    args.output.mkdir(parents=True, exist_ok=False)
    context = {'workspace': args.output.resolve(), 'credentials': {}, 'shell_enabled': False,
               'config': {'enabled': True, 'code': {'enabled': True, 'projects': [{'folder_paths': [str(source)]}]},
                          'documents': {'enabled': False, 'folders': []}}}
    rules = [
        {'id': 'call', 'language': 'Go', 'rule': {'kind': 'call_expression',
            'has': {'field': 'function', 'pattern': '$CALLEE'}}},
        {'id': 'if', 'language': 'Go', 'rule': {'kind': 'if_statement', 'all': [
            {'has': {'field': 'condition', 'pattern': '$CONDITION'}},
            {'has': {'field': 'consequence', 'pattern': '$THEN'}}]}},
        *[{'id': kind, 'language': 'Go', 'rule': {'kind': kind}} for kind in
          ('defer_statement', 'go_statement', 'func_literal', 'for_statement',
           'expression_switch_statement', 'expression_case', 'default_case', 'return_statement')],
    ]
    matches, warning = await scan_source(context, [source / file for file in files], rules)
    records = []
    for node in functions:
        local = [match for match in matches if match['file'] == str(source / node['filePath'])
                 and node['startLine'] <= match['range']['start']['line'] + 1
                 and match['range']['end']['line'] + 1 <= node['endLine']]
        calls = sorted([match for match in local if match['ruleId'] == 'call'],
                       key=lambda match: (match['range']['byteOffset']['start'], match['range']['byteOffset']['end']))
        for call in calls:
            line = call['range']['start']['line'] + 1
            guards = []
            begin, end = call['range']['byteOffset']['start'], call['range']['byteOffset']['end']
            for parent in local:
                if parent['ruleId'] == 'call':
                    continue
                bounds = parent['range']['byteOffset']
                if not (bounds['start'] <= begin and end <= bounds['end']):
                    continue
                guard = {'kind': parent['ruleId'], 'line': parent['range']['start']['line'] + 1}
                if parent['ruleId'] == 'if':
                    variables = parent['metaVariables']['single']
                    condition, then = variables['CONDITION'], variables['THEN']
                    guard['condition'] = condition['text']
                    if condition['range']['byteOffset']['start'] <= begin < condition['range']['byteOffset']['end']:
                        guard['position'] = 'condition evaluation'
                    elif then['range']['byteOffset']['start'] <= begin < then['range']['byteOffset']['end']:
                        guard['position'] = 'consequence'
                    else:
                        guard['position'] = 'initializer or alternative; inspect source'
                else:
                    guard['source_header'] = parent['text'].splitlines()[0]
                guards.append(guard)
            expression = call['metaVariables']['single']['CALLEE']['text']
            candidates = [ref for ref in contexts.get(node['id'], {}).get('outgoingRefs', [])
                          if ref['edge']['kind'] == 'calls' and ref['edge'].get('line') == line
                          and ref['edge'].get('metadata', {}).get('refName') == expression]
            records.append({'caller_id': node['id'], 'caller': node['qualifiedName'],
                'file': node['filePath'], 'line': line, 'column': call['range']['start']['column'] + 1,
                'expression': expression, 'syntax_context': sorted(guards, key=lambda item: (item['line'], item['kind'])),
                'graph_candidates': [{'id': item['node']['id'], 'name': item['node']['qualifiedName'],
                    'file': item['node']['filePath'], 'line': item['node']['startLine'],
                    'metadata': item['edge'].get('metadata')} for item in candidates]})
    index = ['# Call-site syntax and graph candidates',
             'Extracted mechanically; not a runtime trace or a verified execution table.',
             'Entries use lexical source order. Argument/nested calls may evaluate before their outer call. '
             'Deferred calls run later; returned callbacks and goroutines are not eagerly executed in lexical order. '
             'Conditional ancestry is syntax, not proof of reachability. Initializers, alternative branches, '
             'switch cases and loop exits require reading the original source. Graph targets remain candidates.']
    for file in files:
        destination = args.output / (file + '.json')
        destination.parent.mkdir(parents=True, exist_ok=True)
        selected = [record for record in records if record['file'] == file]
        destination.write_text(json.dumps(selected, indent=2), encoding='utf-8')
        index.append(f'- `{file}.json`: {len(selected)} call sites')
    (args.output / 'index.md').write_text('\n\n'.join(index), encoding='utf-8')
    metrics = {'elapsed_seconds': round(time.monotonic() - started, 3), 'functions': len(functions),
               'calls': len(records), 'files': len(files), 'llm_calls': 0, 'tokens': 0, 'warning': warning,
               'source_hashes_checked': True}
    (args.output / 'metrics.json').write_text(json.dumps(metrics, indent=2), encoding='utf-8')
    print(json.dumps(metrics))


if __name__ == '__main__':
    asyncio.run(main())
