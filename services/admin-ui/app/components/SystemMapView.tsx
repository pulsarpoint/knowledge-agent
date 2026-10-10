import { useEffect, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { ArrowRight, Copy, Plus } from "lucide-react";
import { ApiError } from "../lib/api";
import { LAYERS, LAYER_LABELS, LAYOUTS, MAX_DEPTH, VIEWS, VIEW_LABELS, getView, getViewText, mapQuery, readMapState,
  type GraphView, type Layer, type Layout, type MapState, type MapView } from "../lib/systemGraph";
import Mermaid from "./Mermaid";
import NodeSheet, { type SheetTarget } from "./NodeSheet";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Spinner } from "~/components/ui/spinner";
import { Tabs, TabsList, TabsTrigger } from "~/components/ui/tabs";

const say = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback;
type Source = { text: string } | { error: string } | null;

function SourceText({ text }: { text: string }) {
  return <div className="flex flex-col gap-2">
    <Button variant="outline" size="sm" className="self-end" onClick={() => void navigator.clipboard?.writeText(text)}><Copy />Copy</Button>
    <pre className="overflow-auto text-xs"><code>{text}</code></pre>
  </div>;
}

export default function SystemMapView() {
  const [params, setParams] = useSearchParams();
  const state = readMapState(params);
  const { layer, root, depth, view: mode, layout } = state;
  const [graph, setGraph] = useState<GraphView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [source, setSource] = useState<Source>(null);
  const [sheet, setSheet] = useState<SheetTarget | null>(null);
  const [version, setVersion] = useState(0);
  const go = (change: Partial<MapState>) => setParams(mapQuery({ ...state, ...change }));

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    getView(layer, root, depth, controller.signal).then(setGraph, reason => {
      if (controller.signal.aborted) return;
      setGraph(null); setError(say(reason, "Could not load the map."));
    });
    return () => controller.abort();
  }, [layer, root, depth, version]);

  useEffect(() => {
    if (mode === "json") return;
    const controller = new AbortController();
    setSource(null);
    getViewText(layer, root, depth, mode === "diagram" ? "svg" : mode, layout, controller.signal).then(
      text => setSource({ text }),
      reason => { if (!controller.signal.aborted) setSource({ error: say(reason, "Could not draw the map.") }); });
    return () => controller.abort();
  }, [layer, root, depth, mode, layout, version]);

  const inside = graph?.nodes.filter(node => node.parent_id === null) ?? [];
  const edgeCount = (id: string) => (graph?.edges ?? []).filter(edge => edge.from === id || edge.to === id).reduce((sum, edge) => sum + edge.count, 0);
  const isEmpty = graph !== null && graph.nodes.length === 0 && graph.boundary.length === 0;
  const newNode = () => setSheet({ create: { layer, parentId: root, parentName: graph?.root?.name ?? null } });

  let diagram: ReactNode = null;
  if (!error) {
    if (isEmpty) diagram = <div className="flex flex-col items-start gap-3"><p className="text-muted-foreground text-sm">Nothing here yet. Add a node to start.</p>
      <Button variant="outline" onClick={newNode}><Plus />New node</Button></div>;
    else if (mode === "json") diagram = graph ? <SourceText text={JSON.stringify(graph, null, 2)} /> : <Spinner />;
    else if (source === null) diagram = <Spinner />;
    else if ("error" in source) diagram = <Alert variant="destructive"><AlertDescription>{source.error}</AlertDescription></Alert>;
    else if (mode === "diagram") diagram = <img alt={`${LAYER_LABELS[layer]} map`} className="max-w-full"
      src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(source.text)}`} />;
    else if (mode === "mermaid") diagram = <Mermaid chart={source.text} />;
    else diagram = <SourceText text={source.text} />;
  }

  return <div className="flex flex-col gap-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <Tabs value={layer} onValueChange={value => go({ layer: value as Layer, root: null })}>
        <TabsList aria-label="Layer">{LAYERS.map(item => <TabsTrigger key={item} value={item}>{LAYER_LABELS[item]}</TabsTrigger>)}</TabsList>
      </Tabs>
      <Button onClick={newNode}><Plus />New node</Button>
    </div>
    <nav aria-label="Breadcrumb" className="flex flex-wrap items-center gap-1 text-sm">
      <Button variant="link" className="h-auto p-0" onClick={() => go({ root: null })}>{LAYER_LABELS[layer]}</Button>
      {graph?.breadcrumb.map(crumb => <span key={crumb.id} className="flex items-center gap-1"><span aria-hidden="true">›</span>
        <Button variant="link" className="h-auto p-0" onClick={() => go({ root: crumb.id })}>{crumb.name}</Button></span>)}
    </nav>
    <div className="flex flex-wrap items-center gap-3">
      <Select value={String(depth)} onValueChange={value => go({ depth: Number(value) })}>
        <SelectTrigger aria-label="Depth" className="w-32"><SelectValue /></SelectTrigger>
        <SelectContent><SelectGroup>{Array.from({ length: MAX_DEPTH }, (_, index) => index + 1).map(level =>
          <SelectItem key={level} value={String(level)}>Depth {level}</SelectItem>)}</SelectGroup></SelectContent>
      </Select>
      <Tabs value={mode} onValueChange={value => go({ view: value as MapView })}>
        <TabsList aria-label="View">{VIEWS.map(item => <TabsTrigger key={item} value={item}>{VIEW_LABELS[item]}</TabsTrigger>)}</TabsList>
      </Tabs>
      {mode === "diagram" && <Select value={layout} onValueChange={value => go({ layout: value as Layout })}>
        <SelectTrigger aria-label="Layout" className="w-32"><SelectValue /></SelectTrigger>
        <SelectContent><SelectGroup>{LAYOUTS.map(item => <SelectItem key={item} value={item}>{item}</SelectItem>)}</SelectGroup></SelectContent>
      </Select>}
    </div>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <div className="grid gap-4 lg:grid-cols-[1fr_18rem]">
      <section aria-label="Diagram" className="min-h-64 overflow-auto rounded-md border p-3">{diagram}</section>
      <aside className="flex flex-col gap-4">
        <section aria-label="Inside" className="flex flex-col gap-2">
          <h2 className="text-sm font-medium">Inside</h2>
          {inside.length === 0 ? <p className="text-muted-foreground text-sm">No nodes.</p> : <ul className="flex flex-col gap-1">
            {inside.map(node => <li key={node.id} className="flex items-center justify-between gap-2">
              <div className="flex min-w-0 flex-col">
                <button type="button" className="truncate text-left text-sm underline-offset-4 hover:underline" onClick={() => setSheet({ nodeId: node.id })}>{node.name}</button>
                <span className="text-muted-foreground text-xs">{node.child_count > 0 ? `${node.kind} · +${node.child_count} inside` : node.kind}</span>
              </div>
              <Button size="icon" variant="ghost" aria-label={`Open ${node.name}`} onClick={() => go({ root: node.id })}><ArrowRight /></Button>
            </li>)}
          </ul>}
        </section>
        <section aria-label="Outside" className="flex flex-col gap-2">
          <h2 className="text-sm font-medium">Outside</h2>
          {(graph?.boundary.length ?? 0) === 0 ? <p className="text-muted-foreground text-sm">Nothing outside.</p> : <ul className="flex flex-col gap-1">
            {graph!.boundary.map(node => { const count = edgeCount(node.id); return <li key={node.id} className="flex items-center justify-between gap-2">
              <div className="flex min-w-0 flex-col">
                <button type="button" className="truncate text-left text-sm underline-offset-4 hover:underline" onClick={() => setSheet({ nodeId: node.id })}>{node.name}</button>
                <span className="text-muted-foreground text-xs">{`${LAYER_LABELS[node.layer]} · ${count} ${count === 1 ? "edge" : "edges"}`}</span></div>
              <Button size="icon" variant="ghost" aria-label={`Open ${node.name}`} onClick={() => go({ layer: node.layer, root: node.id })}><ArrowRight /></Button>
            </li>; })}
          </ul>}
        </section>
      </aside>
    </div>
    {sheet && <NodeSheet target={sheet} onClose={() => setSheet(null)} onChanged={() => setVersion(value => value + 1)} />}
  </div>;
}
