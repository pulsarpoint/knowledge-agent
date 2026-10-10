import { request } from "./api";

export type Layer = "business" | "software" | "data" | "infrastructure";
export const LAYERS: Layer[] = ["business", "software", "data", "infrastructure"];
export const LAYER_LABELS: Record<Layer, string> = { business: "Business", software: "Software", data: "Data", infrastructure: "Infrastructure" };
export const EDGE_TYPES = ["calls", "uses", "depends_on", "reads", "writes", "publishes", "subscribes", "instance_of", "runs_on", "implements", "stores", "includes"] as const;
export type EdgeType = typeof EDGE_TYPES[number];
export const EDGE_LABELS: Record<EdgeType, string> = { calls: "calls", uses: "uses", depends_on: "depends on", reads: "reads",
  writes: "writes", publishes: "publishes", subscribes: "subscribes", instance_of: "instance of", runs_on: "runs on",
  implements: "implements", stores: "stores", includes: "includes" };
export type SourceKind = "server" | "deployment" | "environment" | "service" | "repository";
export const SOURCE_LABELS: Record<SourceKind, string> = { server: "Servers", deployment: "Services", environment: "Environments",
  service: "Services", repository: "Code projects" };
export interface NodeSource { kind: SourceKind; id: string; page: string; label: string }
export const KIND_SUGGESTIONS: Record<Layer, string[]> = {
  business: ["capability", "process", "actor"],
  software: ["system", "service", "module", "component", "library", "job"],
  data: ["database", "schema", "table", "queue", "bucket"],
  infrastructure: ["environment", "cluster", "server", "container", "network"],
};
export type MapView = "diagram" | "mermaid" | "d2" | "json";
export const VIEWS: MapView[] = ["diagram", "mermaid", "d2", "json"];
export const VIEW_LABELS: Record<MapView, string> = { diagram: "Diagram", mermaid: "Mermaid", d2: "D2", json: "JSON" };
export type Layout = "tala" | "elk" | "dagre";
export const LAYOUTS: Layout[] = ["tala", "elk", "dagre"];
export const MAX_DEPTH = 5;

export interface GraphDocument { id: string; slug: string; title: string }
export interface ViewNode { id: string; parent_id: string | null; layer: Layer; kind: string; name: string; description: string;
  child_count: number; expandable: boolean; internal_edges: number; documents: GraphDocument[]; source_kind: SourceKind | null }
export interface BoundaryNode { id: string; name: string; kind: string; layer: Layer; source_kind: SourceKind | null }
export interface ViewEdge { from: string; to: string; type: EdgeType; count: number; edge_ids: string[]; synced: boolean }
export interface Crumb { id: string; name: string; kind: string }
export interface GraphView { layer: Layer; depth: number; root: (Crumb & { description: string }) | null; breadcrumb: Crumb[];
  nodes: ViewNode[]; boundary: BoundaryNode[]; edges: ViewEdge[] }
export interface EdgeRow { id: string; type: EdgeType; description: string; revision: number; synced: boolean;
  node: { id: string; name: string; kind: string; layer: Layer } }
export interface NodeDetail { id: string; layer: Layer; kind: string; name: string; description: string; parent_id: string | null;
  properties: Record<string, unknown>; revision: number; created_at: string; updated_at: string; breadcrumb: Crumb[];
  children: (Crumb & { child_count: number })[]; outgoing: EdgeRow[]; incoming: EdgeRow[]; documents: GraphDocument[]; source: NodeSource | null }
export interface NodeHit { id: string; layer: Layer; kind: string; name: string; path: string[] }
export interface NodeFieldsInput { kind: string; name: string; description: string; properties: Record<string, unknown>; parent_id: string | null }
export interface NewNodeInput extends NodeFieldsInput { layer: Layer }
export interface NodeChangeInput extends NodeFieldsInput { revision: number }
export interface NewEdgeInput { from_id: string; to_id: string; type: EdgeType; description: string }

export interface MapState { layer: Layer; root: string | null; depth: number; view: MapView; layout: Layout }

function pick<T extends string>(value: string | null, allowed: readonly T[], fallback: T): T {
  return allowed.includes(value as T) ? value as T : fallback;
}
/** The System map's state from the page URL; anything unknown falls back to the defaults. */
export function readMapState(params: URLSearchParams): MapState {
  const depth = Number(params.get("depth"));
  return { layer: pick(params.get("layer"), LAYERS, "software"), root: params.get("root") || null,
    depth: Number.isInteger(depth) && depth >= 1 && depth <= MAX_DEPTH ? depth : 1,
    view: pick(params.get("view"), VIEWS, "diagram"), layout: pick(params.get("layout"), LAYOUTS, "tala") };
}
/** The page URL for a state: the layer always, everything else only when it is not the default. */
export function mapQuery(state: MapState): URLSearchParams {
  const query = new URLSearchParams({ layer: state.layer });
  if (state.root) query.set("root", state.root);
  if (state.depth !== 1) query.set("depth", String(state.depth));
  if (state.view !== "diagram") query.set("view", state.view);
  if (state.layout !== "tala") query.set("layout", state.layout);
  return query;
}
export function viewUrl(layer: Layer, root: string | null, depth: number, format: "json" | "mermaid" | "d2" | "svg", layout: Layout = "tala") {
  const query = new URLSearchParams();
  if (root) query.set("root", root);
  query.set("depth", String(depth));
  query.set("format", format);
  if (format === "svg") query.set("layout", layout);
  return `/api/graph/view/${layer}?${query}`;
}
const send = (method: string, body: unknown): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export const getView = (layer: Layer, root: string | null, depth: number, signal?: AbortSignal) =>
  request<GraphView>(viewUrl(layer, root, depth, "json"), { signal });
export const getViewText = (layer: Layer, root: string | null, depth: number, format: "mermaid" | "d2" | "svg", layout: Layout, signal?: AbortSignal) =>
  request<string>(viewUrl(layer, root, depth, format, layout), { signal }, "text");
export const getNode = (id: string, signal?: AbortSignal) => request<{ node: NodeDetail }>(`/api/graph/nodes/${id}`, { signal });
export function searchNodes(q: string, layer?: Layer) {
  const query = new URLSearchParams();
  if (layer) query.set("layer", layer);
  query.set("q", q);
  return request<{ nodes: NodeHit[] }>(`/api/graph/nodes?${query}`);
}
export const createNode = (input: NewNodeInput) => request<{ node: NodeDetail }>("/api/graph/nodes", send("POST", input));
export const updateNode = (id: string, input: NodeChangeInput) => request<{ node: NodeDetail }>(`/api/graph/nodes/${id}`, send("PUT", input));
export const deleteNode = (id: string) => request<void>(`/api/graph/nodes/${id}`, { method: "DELETE" });
export const createEdge = (input: NewEdgeInput) => request<{ edge: unknown }>("/api/graph/edges", send("POST", input));
export const deleteEdge = (id: string) => request<void>(`/api/graph/edges/${id}`, { method: "DELETE" });
export const attachDocument = (nodeId: string, documentId: string) =>
  request<void>(`/api/graph/nodes/${nodeId}/documents/${documentId}`, { method: "PUT" });
export const detachDocument = (nodeId: string, documentId: string) =>
  request<void>(`/api/graph/nodes/${nodeId}/documents/${documentId}`, { method: "DELETE" });
