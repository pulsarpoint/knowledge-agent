"""Export an endpoint's indexed functions through the actual SDK-backed agent tool."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import time

from agents.tool_context import ToolContext

from knowledge_admin.tools.code_graph import code_graph
from knowledge_admin.tools.code_search import code_search


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--graph', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--source-folder', type=Path, required=True)
    parser.add_argument('--handler', required=True)
    parser.add_argument('--handler-file', required=True, help='File path relative to the graph code root.')
    parser.add_argument('--inspect-name', action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--support-function', action='append', default=[], metavar='FILE:NAME',
                        help='Explicitly selected registration middleware; include its native call closure.')
    parser.add_argument('--source-range', action='append', default=[], metavar='FILE:START:END',
                        help='Explicit registration/build context outside the handler call graph.')
    parser.add_argument('--packet', action='store_true',
                        help='Export source by file plus directly referenced data declarations and visible gaps.')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    digest = hashlib.sha256(args.graph.read_bytes()).hexdigest()
    graph = {'id': 'benchmark', 'source_hash': digest, 'graph_path': str(args.graph.resolve()),
             'code_root': str(args.source_root.resolve()), 'folder_paths': [str(args.source_folder.resolve())]}
    context = {'workspace': args.output.resolve(), 'credentials': {}, 'shell_enabled': False,
               'config': {'enabled': True, 'code': {'enabled': True, 'graphs': [graph],
                   'projects': [{'folder_paths': graph['folder_paths']}]},
                   'documents': {'enabled': False, 'folders': []}}}
    calls = []

    async def invoke(tool, **values):
        payload = json.dumps(values)
        wrapped = ToolContext(context=context, tool_name=tool.name, tool_call_id=f'query-{len(calls)}', tool_arguments=payload)
        result = await tool.on_invoke_tool(wrapped, payload)
        calls.append({'tool': tool.name, 'arguments': values})
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            raise RuntimeError(result) from None

    async def pages(**values):
        entries, edges, offset = [], [], 0
        while offset is not None:
            result = await invoke(code_graph, project_id='benchmark', offset=offset, limit=100, **values)
            entries.extend(result['entries'])
            edges.extend(result.get('edges', []))
            offset = result.get('next_offset')
        return {'entries': entries, 'edges': edges}

    def save(name, value):
        (args.output / name).write_text(json.dumps(value, indent=2), encoding='utf-8')

    matches = await pages(action='search', query=args.handler)
    handlers = [node for node in matches['entries'] if node['filePath'] == args.handler_file]
    if len(handlers) != 1:
        raise ValueError('Choose a handler name and file identifying exactly one graph node.')
    handler = handlers[0]
    save('handler.json', handler)
    save('routes.json', await pages(action='routes'))
    save('context.json', await invoke(code_graph, project_id='benchmark', action='context', node_id=handler['id']))
    save('direct-callees.json', await pages(action='callees', node_id=handler['id']))
    save('handler-unresolved.json', await pages(action='unresolved', node_id=handler['id']))
    closure = await pages(action='traverse', node_id=handler['id'], include_code=True)
    save('reachable-functions.json', closure)
    inspections = {}
    for name in args.inspect_name:
        inspections[name] = await pages(action='search', query=name, include_code=True)
    save('inspected-symbols.json', inspections)
    syntax = await invoke(code_search, path=str(args.source_root / args.handler_file), language='Go', name=args.handler)
    save('handler-syntax.json', syntax)
    blocks = ['# Reachable source from CodeGraph\n',
              'Indexed outgoing call closure, not a proof of execution or completeness. '
              'Heuristic/missing edges are preserved in reachable-functions.json.\n']
    for node in closure['entries']:
        blocks.append(f"## {node['qualifiedName']}\n\n{node['filePath']}:{node['startLine']}–{node['endLine']}\n")
        blocks.append(f"```{node['language']}\n{node.get('code') or '[Source unavailable: ' + node.get('source_status', 'unknown') + ']'}\n```\n")
    if args.packet:
        nodes = {node['id']: node for node in closure['entries']}
        support_seeds = []
        for selected in args.support_function:
            file_path, name = selected.rsplit(':', 1)
            matches = await pages(action='search', query=name)
            seeds = [node for node in matches['entries'] if node['filePath'] == file_path]
            if len(seeds) != 1:
                raise ValueError(f'Support function must match one node: {selected}')
            support_seeds.append(seeds[0])
            support = await pages(action='traverse', node_id=seeds[0]['id'], include_code=True)
            nodes.update({node['id']: node for node in support['entries']})
        contexts, gaps, references = {}, {}, {}
        # Public SDK context supplies value/type edges omitted by calls-only traversal.
        # Keep these declarations distinct from the executable call closure.
        for node in list(nodes.values()):
            details = (await invoke(code_graph, project_id='benchmark', action='context', node_id=node['id']))['context']
            contexts[node['id']] = details
            gaps[node['id']] = (await pages(action='unresolved', node_id=node['id']))['entries']
            for reference in details['outgoingRefs']:
                target = reference['node']
                if target['kind'] in ('constant', 'variable', 'struct', 'interface', 'type_alias') and target['id'] not in nodes:
                    references[target['id']] = target
        for node_id in references:
            result = await invoke(code_graph, project_id='benchmark', action='code', node_id=node_id)
            nodes[node_id] = result['entries'][0]
        save('support-seeds.json', support_seeds)
        save('source-contexts.json', contexts)
        save('unresolved-references.json', gaps)
        save('packet-nodes.json', list(nodes.values()))
        ranges = {}
        for node in nodes.values():
            if node.get('source_status') != 'matches_index':
                raise ValueError(f'Indexed source is unavailable: {node["filePath"]}')
            ranges.setdefault(node['filePath'], []).append((node['startLine'], node['endLine']))
        explicit_ranges = []
        for value in args.source_range:
            file_path, start, end = value.rsplit(':', 2)
            ranges.setdefault(file_path, []).append((int(start), int(end)))
            explicit_ranges.append({'file': file_path, 'start': int(start), 'end': int(end)})
        packet = args.output / 'packet'
        packet.mkdir()
        index = ['# Endpoint source collection',
                 'This is extracted source, not generated documentation or a factual review.',
                 'Read focused files below. Source line numbers retain their original values.',
                 'The handler call closure and explicitly selected middleware seed this collection. '
                 'Direct data/type references are included. Registration ranges are explicit selections. '
                 'There is no claim of complete dynamic dispatch, middleware discovery, type closure or database schema discovery. '
                 'Use the configured source and CodeGraph tools to fill relevant gaps.',
                 '## Selected sources']
        manifest = []
        for file_path, sections in sorted(ranges.items()):
            file = (args.source_root / file_path).resolve()
            if not file.is_relative_to(args.source_folder.resolve()):
                raise ValueError('Packet source outside the selected folder.')
            lines = file.read_text(encoding='utf-8').splitlines()
            merged = []
            for start, end in sorted(sections):
                if not 1 <= start <= end <= len(lines):
                    raise ValueError(f'Invalid source range: {file_path}:{start}:{end}')
                if merged and start <= merged[-1][1] + 1:
                    merged[-1][1] = max(merged[-1][1], end)
                else:
                    merged.append([start, end])
            destination = packet / (file_path + '.md')
            destination.parent.mkdir(parents=True, exist_ok=True)
            content = [f'# {file_path}']
            for start, end in merged:
                content.append(f'\n## Lines {start}–{end}\n\n```text')
                content.extend(f'{number}: {lines[number - 1]}' for number in range(start, end + 1))
                content.append('```')
            destination.write_text('\n'.join(content) + '\n', encoding='utf-8')
            index.append(f'- `{file_path}.md`: source ranges ' + ', '.join(f'{a}–{b}' for a, b in merged))
            manifest.append({'file': file_path, 'source_sha256': hashlib.sha256(file.read_bytes()).hexdigest(), 'ranges': merged})
        (packet / 'index.md').write_text('\n\n'.join(index) + '\n', encoding='utf-8')
        save('packet-manifest.json', {'files': manifest, 'explicit_ranges': explicit_ranges,
            'support_functions': args.support_function, 'handler_nodes': len(closure['entries']),
            'total_nodes': len(nodes), 'direct_data_declarations': len(references),
            'unresolved_reference_count': sum(len(items) for items in gaps.values())})
    source = '\n'.join(blocks)
    (args.output / 'source.md').write_text(source, encoding='utf-8')
    assert hashlib.sha256(args.graph.read_bytes()).hexdigest() == digest, 'Graph snapshot changed.'
    save('tool-calls.json', calls)
    metrics = {'elapsed_seconds': round(time.monotonic() - started, 3), 'model_calls': 0, 'tokens_used': 0,
               'tool_calls': len(calls), 'reachable_nodes': len(closure['entries']),
               'functions': sum(node['kind'] in ('function', 'method') for node in closure['entries']), 'edges': len(closure['edges']),
               'source_characters': sum(len(node.get('code') or '') for node in closure['entries']),
               'unavailable_source': sum(node.get('code') is None for node in closure['entries']),
               'graph_sha256': digest, 'graph_unchanged': True,
               'source_root': str(args.source_root.resolve()), 'source_folder': str(args.source_folder.resolve()),
               'handler': handler['id'], 'extra_inspected_names': args.inspect_name,
               'note': 'Source is read only after its hash matches CodeGraph. Extra symbol lookups are explicit inspection seeds, not repaired graph edges.'}
    save('metrics.json', metrics)
    print(json.dumps(metrics, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
