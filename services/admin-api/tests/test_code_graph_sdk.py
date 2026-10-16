"""Exercise the installed CodeGraph SDK, using a real index rather than SQL fixtures."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from contextlib import nullcontext

from access_helpers import of
from knowledge_admin.capabilities import code_graph as code_graph_module
from tool_helpers import tool
from test_function_tools import context, invoke

code_graph = tool('code_graph')


@pytest.fixture
def indexed_graph(context, tmp_path, monkeypatch):
    root = tmp_path / 'code'
    (root / 'source.go').write_text(
        """package example
func Handle() { helper() }
func helper() { leaf(); Handle() }
func leaf() { println("test-tool-secret") }
func unrelated() {}
""",
        encoding='utf-8',
    )
    bridge = Path(__file__).resolve().parents[2] / 'code-graph'
    script = """const { CodeGraph } = require('@colbymchenry/codegraph');
        (async () => { const graph = await CodeGraph.init(process.argv[1]);
          try { await graph.indexAll(); } finally { graph.close(); } })()
          .catch(error => { console.error(error); process.exitCode = 1; });"""
    subprocess.run(
        ['node', '-e', script, str(root)],
        cwd=bridge,
        check=True,
        capture_output=True,
        env=os.environ | {'CODEGRAPH_TELEMETRY': '0', 'DO_NOT_TRACK': '1', 'CODEGRAPH_NO_DAEMON': '1'},
        timeout=60,
    )
    snapshot = tmp_path / 'snapshot'
    snapshot.mkdir()
    database = snapshot / 'graph.sqlite'
    shutil.copyfile(root / '.codegraph/codegraph.db', database)
    graph = {
        'id': 'project',
        'source_hash': 'fixture',
        'code_root': str(root),
        'folder_paths': [str(root)],
        'graph_path': str(database),
    }
    of(context['config'], 'code_project')[0]['id'] = 'project'  # the graph's code project, in the run's access
    monkeypatch.setattr(
        code_graph_module, 'load_graphs', lambda conn, project, ids: [graph] if 'project' in ids else []
    )
    monkeypatch.setattr(code_graph_module, 'connect', nullcontext)
    return graph


def test_native_lookup_traversal_source_and_readonly_snapshot(context, indexed_graph):
    database = Path(indexed_graph['graph_path'])
    digest = hashlib.sha256(database.read_bytes()).hexdigest()
    database.chmod(0o444)
    database.parent.chmod(0o555)
    try:
        search = json.loads(
            asyncio.run(invoke(code_graph, context, project_id='project', action='search', query='Handle'))
        )
        handler = search['entries'][0]
        assert handler['name'] == 'Handle' and handler['filePath'] == 'source.go'
        node_id = handler['id']
        source = json.loads(
            asyncio.run(invoke(code_graph, context, project_id='project', action='code', node_id=node_id))
        )
        assert source['entries'][0]['code'] == 'func Handle() { helper() }'
        assert source['entries'][0]['source_status'] == 'matches_index'
        direct = json.loads(
            asyncio.run(invoke(code_graph, context, project_id='project', action='callees', node_id=node_id))
        )
        assert [entry['node']['name'] for entry in direct['entries']] == ['helper']
        offset, names = 0, []
        while offset is not None:
            page = json.loads(
                asyncio.run(
                    invoke(
                        code_graph,
                        context,
                        project_id='project',
                        action='traverse',
                        node_id=node_id,
                        include_code=True,
                        offset=offset,
                        limit=2,
                    )
                )
            )
            names.extend(node['name'] for node in page['entries'])
            assert 'test-tool-secret' not in json.dumps(page)
            offset = page['next_offset']
        assert sorted(names) == ['Handle', 'helper', 'leaf']  # terminates the recursive cycle
        details = json.loads(
            asyncio.run(invoke(code_graph, context, project_id='project', action='context', node_id=node_id))
        )
        assert details['context']['focal']['id'] == node_id
        unresolved = json.loads(
            asyncio.run(invoke(code_graph, context, project_id='project', action='unresolved', node_id=node_id))
        )
        assert all(ref['fromNodeId'] == node_id for ref in unresolved['entries'])
        assert hashlib.sha256(database.read_bytes()).hexdigest() == digest
        assert set(database.parent.iterdir()) == {database}
    finally:
        database.parent.chmod(0o755)
        database.chmod(0o644)


def test_changed_missing_and_escaped_source_are_not_returned(context, indexed_graph, tmp_path):
    result = json.loads(asyncio.run(invoke(code_graph, context, project_id='project', action='search', query='Handle')))
    node_id = result['entries'][0]['id']
    source = Path(indexed_graph['code_root']) / 'source.go'
    original = source.read_text(encoding='utf-8')
    source.write_text('// shifted source\n' + original, encoding='utf-8')
    changed = json.loads(asyncio.run(invoke(code_graph, context, project_id='project', action='code', node_id=node_id)))
    assert changed['entries'][0]['code'] is None
    assert changed['entries'][0]['source_status'] == 'changed_since_indexing'
    source.unlink()
    missing = json.loads(asyncio.run(invoke(code_graph, context, project_id='project', action='code', node_id=node_id)))
    assert missing['entries'][0]['source_status'] == 'missing'
    outside = tmp_path / 'outside.go'
    outside.write_text(original, encoding='utf-8')
    source.symlink_to(outside)
    error = asyncio.run(invoke(code_graph, context, project_id='project', action='code', node_id=node_id))
    assert 'outside' in error


def test_invalid_graph_node_and_disabled_access(context, indexed_graph):
    other = asyncio.run(invoke(code_graph, context, project_id='other'))
    assert other == 'This code project is not available in this run.'
    assert 'node ID' in asyncio.run(
        invoke(code_graph, context, project_id='project', action='code', node_id='not-found')
    )
    context['config']['access']['resources'] = []  # no code project in the run's access object
    off = asyncio.run(invoke(code_graph, context, project_id='project', action='search', query='Handle'))
    assert off == 'This run has no code projects.'
