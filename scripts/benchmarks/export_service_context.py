"""Export one service's code and graphs inside the existing admin-api container.

Read-only database access; CodeGraph opens a temporary copy of each saved index.
Run with the admin-api Python environment and pass a service UUID as the argument.
The JSON output contains source code, so callers should save it rather than log it.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from knowledge_admin.code_paths import code_root
from knowledge_admin.capabilities.code_graph import load_code_graph
from knowledge_admin.code_processing import index_directory


# All graph reads use the installed public SDK. Opening the temporary copy avoids
# CodeGraph's startup migrations/checkpointing touching an immutable saved graph.
EXPORT_GRAPH = r'''
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {CodeGraph, DatabaseConnection, QueryBuilder} = require('@colbymchenry/codegraph');
(async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'rlm-graph-'));
  let graph, connection;
  try {
    const database = path.join(directory, '.codegraph', 'codegraph.db');
    fs.mkdirSync(path.dirname(database));
    fs.copyFileSync(process.argv[1], database);
    graph = await CodeGraph.open(directory, {sync: false});
    connection = DatabaseConnection.open(database);
    const queries = new QueryBuilder(connection.getDb());
    const nodes = queries.getAllNodes();
    const edges = nodes.flatMap(node => graph.getOutgoingEdges(node.id));
    const stats = graph.getStats();
    if (nodes.length !== stats.nodeCount || edges.length !== stats.edgeCount)
      throw new Error('Graph export is incomplete');
    process.stdout.write(JSON.stringify({nodes, edges, stats, files: graph.getFiles(),
      unresolved_references: queries.getUnresolvedReferences()}));
  } finally {
    connection?.close();
    graph?.close();
    fs.rmSync(directory, {recursive: true, force: true});
  }
})().catch(() => {console.error('Graph export failed'); process.exitCode = 1;});
'''


def main() -> None:
    service_id = UUID(sys.argv[1])
    with psycopg.connect('', row_factory=dict_row) as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        service = conn.execute('''SELECT id,name,description,service_type,service_role,
            environments,dependencies,dependents FROM ka.service_assets WHERE id=%s''', (service_id,)).fetchone()
        if service is None:
            raise ValueError('Service not found')
        projects = conn.execute('''SELECT r.id,r.name,r.description,r.current_commit,
            ARRAY(SELECT path::text FROM ka.code_project_folders f WHERE f.code_project_id=r.id ORDER BY path) AS folder_paths
            FROM ka.repositories r JOIN ka.service_repositories sr ON sr.repository_id=r.id
            WHERE sr.service_id=%s AND r.archived_at IS NULL ORDER BY r.id''', (service_id,)).fetchall()
        deployments = conn.execute('''SELECT s.name AS server_name,s.hostname,d.runtime,d.runtime_name,
            d.environment_id,COALESCE(e.name,d.environment) AS environment,d.port,d.deployed_version
            FROM ka.service_deployments d JOIN ka.servers s ON s.id=d.server_id
            LEFT JOIN ka.environments e ON e.id=d.environment_id
            WHERE d.service_id=%s ORDER BY d.id''', (service_id,)).fetchall()
    context = {'schema_version': 1, 'service': service, 'recorded_deployments': deployments,
               'projects': [], 'files': {}, 'file_manifest': {}, 'graphs': {}, 'omissions': []}
    root = code_root()
    for project in projects:
        directory = index_directory(project['id'])
        manifest_path = directory / 'manifest.json'
        if not manifest_path.is_file():
            raise ValueError(f"Generate the graph for {project['name']} first")
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        graph = load_code_graph(project, manifest['source_hash'])
        if graph['missing_folders']:
            raise ValueError(f"Mapped folders are missing for {project['name']}")
        prefix = str(project['id'])
        graph_root = Path(graph['code_root']).resolve()
        context['projects'].append({**project, 'file_prefix': prefix,
                                    'source_hash': graph['source_hash'],
                                    'source_exclusions': manifest['excluded']})
        for relative, digest in sorted(manifest['files'].items()):
            file = (root / relative).resolve()
            if not file.is_relative_to(graph_root) or not file.is_relative_to(root):
                raise ValueError('Source file outside the project root')
            raw = file.read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError('Source changed during export; regenerate the graph')
            key = f'{prefix}/{file.relative_to(graph_root).as_posix()}'
            try:
                content = raw.decode('utf-8')
            except UnicodeDecodeError:
                context['omissions'].append({'path': key, 'reason': 'not UTF-8 text'})
                continue
            context['files'][key] = content
            context['file_manifest'][key] = {'sha256': digest, 'bytes': len(raw)}
        before = hashlib.sha256(Path(graph['graph_path']).read_bytes()).hexdigest()
        result = subprocess.run(['node', '-e', EXPORT_GRAPH, graph['graph_path']],
            cwd=Path(os.environ.get('CODEGRAPH_BRIDGE', '/services/code-graph/index.cjs')).parent,
            capture_output=True, text=True, check=False, timeout=120)
        if result.returncode != 0:
            raise RuntimeError('CodeGraph SDK export failed; source output suppressed')
        exported = json.loads(result.stdout)
        if hashlib.sha256(Path(graph['graph_path']).read_bytes()).hexdigest() != before:
            raise ValueError('Graph changed during export')
        for item in exported['files']:
            key = f"{prefix}/{item['path']}"
            item['file_key'] = key
            if key in context['file_manifest'] and item['contentHash'] != context['file_manifest'][key]['sha256']:
                raise ValueError('Indexed file hash differs from exported source')
        for node in exported['nodes']:
            node['file_key'] = f"{prefix}/{node['filePath']}"
            node['source_available'] = node['file_key'] in context['files']
        for reference in exported['unresolved_references']:
            reference['file_key'] = f"{prefix}/{reference['filePath']}"
        context['graphs'][prefix] = {**exported, 'source_hash': graph['source_hash'],
                                     'graph_sha256': before, 'limitations': graph['limitations']}
    if not context['files']:
        raise ValueError('This service has no available source files in linked code projects')
    json.dump(context, sys.stdout, default=str)


if __name__ == '__main__':
    main()
