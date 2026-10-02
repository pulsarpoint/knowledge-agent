// One-shot indexing through CodeGraph's public API. No daemon or agent installation.
const fs = require('node:fs');
const { CodeGraph, isInitialized } = require('@colbymchenry/codegraph');

async function main() {
  const [root, output] = process.argv.slice(2);
  if (!root || !output) throw new Error('Supply a source directory and result file.');
  const exists = isInitialized(root);
  const graph = exists ? await CodeGraph.open(root) : await CodeGraph.init(root);
  try {
    const result = exists ? await graph.sync() : await graph.indexAll();
    fs.writeFileSync(output, JSON.stringify({ result, stats: graph.getStats() }));
  } finally {
    graph.close();
  }
}

main().catch(() => {
  console.error('CodeGraph indexing failed.');
  process.exitCode = 1;
});
