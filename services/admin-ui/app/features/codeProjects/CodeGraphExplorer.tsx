import { useEffect, useMemo, useState } from 'react';
import dagre from '@dagrejs/dagre';
import { Background, Controls, MarkerType, Position, ReactFlow, type Node } from '@xyflow/react';
import { request } from '~/lib/api';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Skeleton } from '~/components/ui/skeleton';
import { Field, FieldLabel } from '~/components/ui/field';
import { Input } from '~/components/ui/input';

type Graph = {
  source_hash: string; file: string; total: number; next_offset: number | null; edges_truncated: boolean;
  nodes: { id: string; label: string; kind: string; file_path: string; start_line: number; end_line: number }[];
  edges: { source: string; target: string; label: string; count: number }[];
};

export default function CodeGraphExplorer({ projectId, sourceHash }: { projectId: string; sourceHash: string }) {
  const [file, setFile] = useState('');
  const [offset, setOffset] = useState(0);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  useEffect(() => { setFile(''); setOffset(0); setSelected(null); setFilter(''); }, [projectId]);
  useEffect(() => {
    const controller = new AbortController();
    setGraph(null); setError('');
    request<Graph>(`/api/code-projects/${encodeURIComponent(projectId)}/graph?source_hash=${encodeURIComponent(sourceHash)}&file=${encodeURIComponent(file)}&offset=${offset}`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setGraph(value); })
      .catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not load graph'); });
    return () => controller.abort();
  }, [projectId, sourceHash, file, offset]);
  const layout = useMemo(() => {
    if (!graph) return { nodes: [], edges: [] };
    const positions = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
    positions.setGraph({ rankdir: 'LR', ranksep: 100, nodesep: 35 });
    const visible = graph.nodes.filter(node => node.label.toLowerCase().includes(filter.toLowerCase()));
    const ids = new Set(visible.map(node => node.id));
    const edges = graph.edges.filter(edge => ids.has(edge.source) && ids.has(edge.target));
    visible.forEach(node => positions.setNode(node.id, { width: 250, height: 78 }));
    edges.forEach(edge => positions.setEdge(edge.source, edge.target));
    dagre.layout(positions);
    const nodes: Node[] = visible.map(node => ({ id: node.id,
      position: { x: positions.node(node.id).x - 125, y: positions.node(node.id).y - 39 },
      data: { label: <div title={node.label} className="flex flex-col gap-1"><strong className="truncate">{node.kind === 'file' ? node.label.split('/').slice(-2).join('/') : node.label}</strong><span className="text-xs">{node.kind}{node.kind !== 'file' && ` · line ${node.start_line}`}</span></div> },
      sourcePosition: Position.Right, targetPosition: Position.Left,
      style: { width: 250, height: 78, background: 'var(--accent)', color: 'var(--accent-foreground)', borderColor: 'var(--primary)', borderWidth: 2, borderRadius: 8 },
    }));
    return { nodes, edges: edges.map((edge, i) => ({ ...edge, id: String(i), label: edge.label + (edge.count > 1 ? ` (${edge.count})` : ''), markerEnd: { type: MarkerType.ArrowClosed } })) };
  }, [graph, filter]);
  return <div className="flex min-w-0 flex-col gap-3" aria-label="Generated code graph">
    <div className="flex flex-wrap items-center gap-3"><h4 className="font-medium">{file || 'Files and their relationships'}</h4>{file && <Button type="button" variant="outline" size="sm" onClick={() => { setFile(''); setOffset(0); setSelected(null); }}>Back to files</Button>}</div>
    <p className="text-sm text-muted-foreground">Click a file to inspect its symbols. Arrows show extracted relationships; numbers count repeated connections. Unresolved or runtime-only connections may be absent.</p>
    {error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : !graph ? <Skeleton className="h-80 w-full" /> : <>
      <Field><FieldLabel htmlFor="code-graph-filter">Filter this graph</FieldLabel><Input id="code-graph-filter" value={filter} onChange={e => setFilter(e.target.value)} placeholder="File, package or symbol name…" /></Field>
      {graph.nodes.length ? <div className="h-[32rem] overflow-hidden rounded-md border"><ReactFlow key={`${projectId}:${sourceHash}:${file}:${offset}:${filter}`} nodes={layout.nodes} edges={layout.edges} fitView minZoom={0.03} maxZoom={2} nodesDraggable={false} nodesConnectable={false}
        onNodeClick={(_, node) => { const entry = graph.nodes.find(item => item.id === node.id)!; if (entry.kind === 'file') { setFile(entry.file_path); setOffset(0); setSelected(null); setFilter(''); } else setSelected(`${entry.label} · ${entry.file_path}:${entry.start_line}–${entry.end_line}`); }}><Background /><Controls showInteractive={false} /></ReactFlow></div>
        : <p className="text-sm text-muted-foreground">No symbols were extracted in this view. Source may be missing or unsupported by CodeGraph.</p>}
      {selected && <p className="break-all text-sm">{selected}</p>}
      <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-muted-foreground">Showing {layout.nodes.length} of {graph.total} {file ? 'symbols' : 'files'}. Connections are shown between visible items on this page.{graph.edges_truncated && ' Edge display limited to 1,500 connections.'}</p><div className="flex gap-2">{offset > 0 && <Button type="button" variant="outline" size="sm" onClick={() => setOffset(Math.max(0, offset - 200))}>Previous</Button>}{graph.next_offset !== null && <Button type="button" variant="outline" size="sm" onClick={() => setOffset(graph.next_offset!)}>Next</Button>}</div></div>
    </>}
  </div>;
}
