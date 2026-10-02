'use strict';

// The Python backend supplies a saved snapshot and an allowlisted operation.
// All graph lookups and traversal use CodeGraph's public SDK, without SQL.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { CodeGraph, DatabaseConnection, QueryBuilder } = require('@colbymchenry/codegraph');

const byLocation = (a, b) => a.filePath.localeCompare(b.filePath) || a.startLine - b.startLine || a.id.localeCompare(b.id);
const within = (file, root) => file === root || file.startsWith(root + path.sep);

async function query(request, directory) {
  const project = path.join(directory, 'project');
  const database = path.join(project, '.codegraph', 'codegraph.db');
  fs.mkdirSync(path.dirname(database), { recursive: true });
  // v1.6.0's open() ignores readOnly and may migrate/checkpoint its database.
  // Never point it at the application's immutable snapshot or working index.
  fs.copyFileSync(request.graph.graph_path, database);
  const graph = await CodeGraph.open(project, { sync: false });
  try {
    const { action, node_id, query: text, offset, limit } = request;
    const paginate = entries => ({ entries: entries.slice(offset, offset + limit),
      total_entries: entries.length, next_offset: offset + limit < entries.length ? offset + limit : null });
    let result;
    if (action === 'search') {
      if (!text.trim()) {
        result = paginate(graph.getFiles().flatMap(file => graph.getNodesInFile(file.path)).sort(byLocation));
      } else {
      // Exact lookup enumerates all overloads. FTS handles a broader query if no exact name matches.
      const exact = graph.getNodesByName(text).sort(byLocation);
      if (exact.length) result = { ...paginate(exact), search_mode: 'exact' };
      else {
        const hits = graph.searchNodes(text, { offset, limit: limit + 1 });
        result = { entries: hits.slice(0, limit).map(hit => hit.node),
          next_offset: hits.length > limit ? offset + limit : null, search_mode: 'text' };
      }
      }
    } else if (action === 'routes') {
      result = paginate(graph.getNodesByKind('route').sort(byLocation));
    } else if (action === 'unresolved') {
      // These are also public library APIs; CodeGraph itself exposes only the count.
      const connection = DatabaseConnection.open(database);
      try {
        const queries = new QueryBuilder(connection.getDb());
        const focal = node_id ? graph.getNode(node_id) : null;
        if (node_id && !focal) throw new Error('Choose a node ID returned by search.');
        const refs = focal ? queries.getUnresolvedReferencesByFiles([focal.filePath])
          .filter(ref => ref.fromNodeId === node_id) : queries.getUnresolvedReferences();
        refs.sort((a, b) => a.filePath.localeCompare(b.filePath) || a.line - b.line || a.referenceName.localeCompare(b.referenceName));
        result = paginate(refs);
      } finally { connection.close(); }
    } else {
      const focal = graph.getNode(node_id);
      if (!focal) throw new Error('Choose a node ID returned by search.');
      if (action === 'code') result = { entries: [focal], next_offset: null };
      else if (action === 'context') result = { context: graph.getContext(node_id) };
      else if (action === 'callers' || action === 'callees') {
        const entries = action === 'callers' ? graph.getCallers(node_id, 1) : graph.getCallees(node_id, 1);
        entries.sort((a, b) => (a.edge.line || 0) - (b.edge.line || 0) || byLocation(a.node, b.node));
        result = paginate(entries);
      } else if (action === 'traverse') {
        const subgraph = graph.traverse(node_id, { direction: 'outgoing', edgeKinds: ['calls'],
          maxDepth: Infinity, limit: graph.getStats().nodeCount + 1 });
        const nodes = [...subgraph.nodes.values()].sort(byLocation);
        result = { ...paginate(nodes), roots: subgraph.roots, traversal: 'all reachable outgoing call edges' };
        const page = new Set(result.entries.map(node => node.id));
        result.edges = subgraph.edges.filter(edge => page.has(edge.source));
      } else throw new Error('Unsupported CodeGraph query action.');
    }
    if (action === 'code' || request.include_code) {
      const entries = result.entries || [result.context.focal];
      for (const entry of entries) {
        const node = entry.node || entry;
        if (!node.filePath || !node.id) continue;
        const sourceRoot = fs.realpathSync(request.graph.code_root);
        const lexical = path.resolve(sourceRoot, node.filePath);
        if (!within(lexical, sourceRoot) || path.isAbsolute(node.filePath)) throw new Error('Source path is outside the code root.');
        if (!fs.existsSync(lexical)) { entry.code = null; entry.source_status = 'missing'; continue; }
        const source = fs.realpathSync(lexical);
        const roots = request.graph.folder_paths.map(folder => fs.realpathSync(folder));
        if (!within(source, sourceRoot) || !roots.some(root => within(source, root))) throw new Error('Source path is outside the selected project folders.');
        const content = fs.readFileSync(source);
        const digest = crypto.createHash('sha256').update(content).digest('hex');
        if (graph.getFile(node.filePath)?.contentHash !== digest) {
          entry.code = null; entry.source_status = 'changed_since_indexing'; continue;
        }
        const destination = path.resolve(project, node.filePath);
        if (!within(destination, project) || within(destination, path.join(project, '.codegraph'))) throw new Error('Invalid indexed source path.');
        fs.mkdirSync(path.dirname(destination), { recursive: true });
        fs.writeFileSync(destination, content);
        entry.code = await graph.getCode(node.id);
        entry.source_status = entry.code === null ? 'unavailable' : 'matches_index';
      }
    }
    return result;
  } finally { graph.close(); }
}

if (require.main === module) {
  const [requestPath, outputPath] = process.argv.slice(2);
  query(JSON.parse(fs.readFileSync(requestPath, 'utf8')), path.dirname(requestPath))
    .then(result => fs.writeFileSync(outputPath, JSON.stringify(result)))
    .catch(error => { console.error(error.message); process.exitCode = 1; });
}

module.exports = { query };
