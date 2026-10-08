"""Extract one handler and explicitly selected syntax nodes, without following callees."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from agents.tool_context import ToolContext

from knowledge_admin.capabilities.ast_grep import scan_source
from knowledge_admin.capabilities.code_graph import code_graph
from knowledge_admin.capabilities.code_search import code_search


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--selection', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    selection = json.loads(args.selection.read_text())
    graph = next(item for item in config['code']['graphs'] if item['id'] == selection['project_id'])
    root = Path(graph['code_root'])
    args.output.mkdir(parents=True, exist_ok=False)
    context = {'workspace': args.output, 'credentials': {}, 'shell_enabled': False, 'config': config}
    calls = []

    async def invoke(tool, **values) -> dict:
        payload = json.dumps(values)
        wrapped = ToolContext(context=context, tool_name=tool.name,
                              tool_call_id=f'extract-{len(calls)}', tool_arguments=payload)
        calls.append({'tool': tool.name, 'arguments': values})
        return json.loads(await tool.on_invoke_tool(wrapped, payload))

    handler = selection['handler']
    nodes = await invoke(code_graph, project_id=graph['id'], action='search', query=handler['name'], limit=100)
    if nodes.get('next_offset') is not None:
        raise ValueError('Ambiguous handler search; narrow the selection before analysis.')
    matches = [node for node in nodes['entries'] if node['filePath'] == handler['path']]
    if len(matches) != 1:
        raise ValueError('Select one exact handler in the graph.')
    indexed = await invoke(code_graph, project_id=graph['id'], action='code', node_id=matches[0]['id'])
    if indexed['entries'][0]['source_status'] != 'matches_index':
        raise ValueError('The handler source no longer matches the graph.')
    parsed = await invoke(code_search, path=str(root / handler['path']), language='Go', name=handler['name'])
    if parsed['total_matches'] != 1 or parsed.get('warning'):
        raise ValueError('Handler extraction must identify one complete declaration without warnings.')
    definition = parsed['matches'][0]
    if (definition['start_line'], definition['end_line']) != (matches[0]['startLine'], matches[0]['endLine']):
        raise ValueError('Graph and parser disagree on the handler boundaries.')
    sources = [{'id': 'S1', 'path': handler['path'], 'start_line': definition['start_line'],
                'end_line': definition['end_line'], 'kind': 'handler', 'code': definition['code']}]
    for fragment in selection['fragments']:
        path = (root / fragment['path']).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError('Selected fragments must be inside the source project.')
        rule = {'id': 'fragment', 'language': 'Go', 'rule': fragment['rule']}
        found, warning = await scan_source(context, [path], [rule])
        if len(found) != 1 or warning:
            raise ValueError(f"Expected exactly one syntax fragment: {fragment['label']}")
        node = found[0]
        sources.append({'id': f'S{len(sources) + 1}', 'path': fragment['path'],
                        'start_line': node['range']['start']['line'] + 1,
                        'end_line': node['range']['end']['line'] + 1,
                        'kind': fragment['label'], 'code': node['text']})
    for source in sources:
        source['sha256'] = hashlib.sha256(source['code'].encode()).hexdigest()
    evidence = {'endpoint': selection['endpoint'], 'handler': matches[0]['qualifiedName'],
                'scope': 'Handler-local input processing only. Called function bodies are intentionally absent.',
                'sources': sources}
    (args.output / 'evidence.json').write_text(json.dumps(evidence, indent=2))
    (args.output / 'extraction.json').write_text(json.dumps({
        'graph_node': matches[0], 'source_files': {
            path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in sorted({s['path'] for s in sources})},
        'calls': calls, 'fragments': len(sources), 'source_characters': sum(len(s['code']) for s in sources),
        'llm_calls': 0, 'callee_bodies_included': False}, indent=2))
    print(f"Extracted {len(sources)} fragments; no callee traversal or model calls.")


if __name__ == '__main__':
    asyncio.run(main())
