"""Select API source ranges from route registrations and their graph dependencies."""

import hashlib
import json
import re
import sqlite3
from collections import defaultdict, deque
from contextlib import closing
from pathlib import Path

from knowledge_admin.code_processing import CodeProcessingError

API_EVIDENCE_VERSION = 5
IDENTIFIER = re.compile(r'[A-Za-z_$][\w$]*')
QUOTED = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`[^`]*`')
SYMBOL_KINDS = {'function', 'method', 'struct', 'class', 'interface', 'type_alias', 'constant', 'variable', 'enum'}
DATA_KINDS = {'struct', 'class', 'interface', 'type_alias', 'constant', 'variable', 'enum'}


def api_batches(graphs: list[dict]) -> tuple[list[dict], list[dict]]:
    """Collect complete related symbols, without character, symbol-count or traversal-depth cutoffs."""
    batches, coverage = [], []
    for graph in graphs:
        root = Path(graph['code_root']).resolve()
        folders = [Path(folder).resolve() for folder in graph['folder_paths']]
        with closing(sqlite3.connect(Path(graph['graph_path']).as_uri() + '?mode=ro', uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            nodes = {row['id']: dict(row) for row in conn.execute(
                'SELECT id,kind,name,file_path,start_line,end_line FROM nodes ORDER BY file_path,start_line,id')}
            edges = defaultdict(list)
            for row in conn.execute("SELECT source,target,kind FROM edges WHERE kind IN ('calls','references','instantiates') ORDER BY id"):
                if row['target'] in nodes and nodes[row['target']]['kind'] in SYMBOL_KINDS:
                    edges[row['source']].append(row['target'])
        names = defaultdict(list)
        for node in nodes.values():
            if node['kind'] in SYMBOL_KINDS:
                names[node['name']].append(node)
        routes = [node for node in nodes.values() if node['kind'] == 'route']
        coverage.append({key: graph[key] for key in ('id', 'name', 'source_hash', 'parse_error_files',
            'missing_folders', 'unresolved_references', 'excluded')})
        coverage[-1].update(route_count=len(routes), limitations=(
            'Inventory includes graph route nodes only. Dynamic routes, generated APIs, GraphQL/gRPC and '
            'unsupported frameworks may be absent. A missing route or edge does not prove absence of behavior.'))
        files = {}

        def source_lines(relative):
            if relative not in files:
                path = (root / relative).resolve()
                if not path.is_relative_to(root) or not any(path.is_relative_to(folder) for folder in folders):
                    raise CodeProcessingError('An API graph source path is outside the selected code folders. Regenerate the graph.')
                if path.name.startswith('.env') or path.name in ('credentials', 'id_rsa', 'id_ed25519') or path.suffix in ('.key', '.pem', '.p12', '.pfx'):
                    raise CodeProcessingError('The API graph references a credential file. Regenerate the graph.')
                try:
                    files[relative] = path.read_text(encoding='utf-8').splitlines()
                except (OSError, UnicodeError):
                    raise CodeProcessingError('An API source file is unavailable. Synchronize the source and regenerate the graph.') from None
            return files[relative]

        for route in routes:
            excerpts, candidates, gaps, ranges = [], [], [], set()

            def excerpt(node, reason, start=None, end=None):
                first = max(1, start if start is not None else node['start_line'] or 1)
                last = max(first, end if end is not None else node['end_line'] or first)
                selected = source_lines(node['file_path'])[first - 1:last]
                key = (node['file_path'], first, last)
                text = '\n'.join(selected)
                if key not in ranges:
                    ranges.add(key)
                    excerpts.append({'path': node['file_path'], 'start_line': first, 'end_line': last,
                        'symbol': node['name'], 'reason': reason,
                        'source': '\n'.join(f'{line}: {content}' for line, content in enumerate(selected, first))})
                return text

            # Registration context contains group prefixes and middleware. Do not include the whole entry-point function.
            exact = excerpt(route, 'API registration')
            excerpt(route, 'Surrounding router configuration', max(1, route['start_line'] - 25), route['end_line'] + 5)
            excerpt(route, 'Imports and build conditions', 1, min(60, len(source_lines(route['file_path']))))
            queue = deque((node_id, 'route graph dependency') for node_id in edges[route['id']])
            for name in dict.fromkeys(IDENTIFIER.findall(QUOTED.sub('', exact))):
                for node in names[name]:
                    if Path(node['file_path']).suffix != Path(route['file_path']).suffix:
                        continue
                    candidates.append({**node, 'basis': 'registration identifier; verify receiver/import'})
                    queue.append((node['id'], 'registration symbol candidate'))
            # Decorated handlers can contain the route's own source range without an explicit edge.
            for node in nodes.values():
                if exact.lstrip().startswith('@') and node['kind'] in ('function', 'method') and node['file_path'] == route['file_path'] and node['start_line'] <= route['start_line'] <= node['start_line'] + 3:
                    queue.append((node['id'], 'decorated handler candidate'))
            if not queue:
                gaps.append('No handler symbol was resolved from this registration; use a targeted source search.')
            visited = set()
            while queue:
                node_id, reason = queue.popleft()
                if node_id in visited:
                    continue
                visited.add(node_id)
                node = nodes[node_id]
                text = excerpt(node, reason)
                queue.extend((target, 'graph dependency of ' + node['name']) for target in edges[node_id])
                # Calls, types and SQL constants can have missing edges. Keep unique source-language matches as candidates.
                identifiers = QUOTED.sub('', text)
                called = set(re.findall(r'([A-Za-z_$][\w$]*)\s*\(', identifiers))
                for name in dict.fromkeys(IDENTIFIER.findall(identifiers)):
                    matches = [n for n in names[name] if Path(n['file_path']).suffix == Path(node['file_path']).suffix]
                    if len(matches) == 1 and (matches[0]['kind'] in DATA_KINDS or name in called):
                        queue.append((matches[0]['id'], 'identifier candidate from ' + node['name']))
            # Import aliases establish which definitions a handler actually references.
            selected_files = {e['path'] for e in excerpts}
            for node in nodes.values():
                if node['kind'] == 'import' and node['file_path'] in selected_files:
                    excerpt(node, 'Import and receiver resolution')
            batch = {'project_id': graph['id'], 'project_name': graph['name'], 'source_hash': graph['source_hash'],
                'path_base': str(root), 'routes': [route], 'handler_candidates': candidates,
                'excerpts': excerpts, 'gaps': gaps, 'source_characters': sum(len(e['source']) for e in excerpts)}
            batch['key'] = hashlib.sha256(json.dumps(batch, sort_keys=True).encode()).hexdigest()
            batches.append(batch)
    return batches, coverage
