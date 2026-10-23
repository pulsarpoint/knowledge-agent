import { request } from "~/lib/api";
import type { RelationKind } from "./inventory";

export type MapName = "infrastructure" | "services";
export const MAPS: MapName[] = ["infrastructure", "services"];
export const MAP_LABELS: Record<MapName, string> = { infrastructure: "Infrastructure", services: "Services" };
export type EntryKind = "service" | "server" | "environment" | "code_project";
export const ENTRY_LABELS: Record<EntryKind, string> = {
  service: "service",
  server: "server",
  environment: "environment",
  code_project: "code project",
};
export const ENTRY_PAGES: Record<EntryKind, { page: string; label: string }> = {
  service: { page: "/services", label: "Services" },
  server: { page: "/servers", label: "Servers" },
  environment: { page: "/environments", label: "Environments" },
  code_project: { page: "/code-projects", label: "Code projects" },
};
/** An edge's kind: network connections, or a service relation. */
export type EdgeKind = "connects_to" | RelationKind;
export type MapView = "diagram" | "mermaid" | "d2" | "json";
export const VIEWS: MapView[] = ["diagram", "mermaid", "d2", "json"];
export const VIEW_LABELS: Record<MapView, string> = { diagram: "Diagram", mermaid: "Mermaid", d2: "D2", json: "JSON" };
export type Layout = "tala" | "elk" | "dagre";
export const LAYOUTS: Layout[] = ["tala", "elk", "dagre"];
export const MAX_DEPTH = 3;

export interface GraphDocument {
  id: string;
  slug: string;
  title: string;
}
export interface EntryRef {
  kind: EntryKind;
  id: string;
}
export interface MapEntry extends EntryRef {
  name: string;
}
export interface ViewNode {
  id: string;
  parent_id: string | null;
  kind: EntryKind;
  name: string;
  description: string;
  entry_kind: EntryKind;
  entry_id: string;
  child_count: number;
  expandable: boolean;
  internal_edges: number;
  documents: GraphDocument[];
  /** Present (true) when its entry is a draft; drawn dashed. */
  draft?: true;
}
/** A node outside the view: an entry, an external service a connection reaches (kind "external_service", opened as
 * the service it is: entry_kind "service"), or an external host (not an entry: nothing to open). */
export interface BoundaryNode {
  id: string;
  name: string;
  kind: EntryKind | "external_service" | "external_host";
  entry_kind: EntryKind | "external_host";
  entry_id: string;
  draft?: true;
}
/** An edge: connections ("connects_to") or service relations, with the ids of the connections or relations drawn. */
export interface ViewEdge {
  from: string;
  to: string;
  type: EdgeKind;
  count: number;
  ids: string[];
  /** A connection's `protocol:port`; a rolled-up line lists the distinct ones (at most 3, then `+N`). */
  label?: string;
  /** Present (true) when every connection or relation drawn is a draft. */
  draft?: true;
}
export interface Crumb {
  id: string;
  name: string;
  kind: EntryKind;
}
export interface MapGraph {
  view: MapName;
  depth: number;
  root: (Crumb & { description: string; entry_kind: EntryKind; entry_id: string }) | null;
  breadcrumb: Crumb[];
  nodes: ViewNode[];
  boundary: BoundaryNode[];
  edges: ViewEdge[];
}
/** A resource's inventory document: a live document or a brand-new draft (version null, not published). */
export interface InventoryDocument {
  id: string;
  title: string;
  markdown: string;
  version: number | null;
  is_draft: boolean;
  published: boolean;
}
/** One document of a resource's document list: a live document linked to the resource or to one of its direct
 * children (`resource`, with the link's `connection`); an inventory document is tagged `inventory`. */
export interface EntryDocument {
  id: string;
  title: string;
  tags: string[];
  description_short: string;
  connection: "built_for" | "linked";
  resource: { type: EntryKind; id: string; name: string };
  /** The draft of a new document (not published yet). */
  is_draft?: boolean;
  /** A live document with a draft waiting. */
  draft_waiting?: boolean;
}
/** An entry's documents, from the documents' links: its inventory document (a code project has none; the live document
 * tagged `inventory` built for it, the most recently updated if several, else a brand-new draft so tagged) and the
 * document list of it and its direct children. */
export interface EntryDocuments {
  inventory_document: InventoryDocument | null;
  documents: EntryDocument[];
}
export interface MapState {
  map: MapName;
  root: string | null;
  depth: number;
  view: MapView;
  layout: Layout;
  /** Show drafts (dashed); off is `drafts=0`. */
  drafts: boolean;
}

function pick<T extends string>(value: string | null, allowed: readonly T[], fallback: T): T {
  return allowed.includes(value as T) ? (value as T) : fallback;
}
/** The System map's state from the page URL; anything unknown falls back to the defaults. */
export function readMapState(params: URLSearchParams): MapState {
  const depth = Number(params.get("depth"));
  return {
    map: pick(params.get("map"), MAPS, "infrastructure"),
    root: params.get("root") || null,
    depth: Number.isInteger(depth) && depth >= 1 && depth <= MAX_DEPTH ? depth : 1,
    view: pick(params.get("view"), VIEWS, "diagram"),
    layout: pick(params.get("layout"), LAYOUTS, "tala"),
    drafts: params.get("drafts") !== "0",
  };
}
/** The System map rooted at an environment, showing its servers and the services deployed on them. */
export const environmentMapPath = (environmentId: string) =>
  `/system-map?${mapQuery({ map: "infrastructure", root: `environment:${environmentId}`, depth: 2, view: "diagram", layout: "tala", drafts: true })}`;
/** The page URL for a state: the map always, everything else only when it is not the default. */
export function mapQuery(state: MapState): URLSearchParams {
  const query = new URLSearchParams({ map: state.map });
  if (state.root) query.set("root", state.root);
  if (state.depth !== 1) query.set("depth", String(state.depth));
  if (state.view !== "diagram") query.set("view", state.view);
  if (state.layout !== "tala") query.set("layout", state.layout);
  if (!state.drafts) query.set("drafts", "0");
  return query;
}
export function viewUrl(
  map: MapName,
  root: string | null,
  depth: number,
  format: "json" | "mermaid" | "d2" | "svg",
  layout: Layout = "tala",
  drafts = true,
) {
  const query = new URLSearchParams();
  if (root) query.set("root", root);
  query.set("depth", String(depth));
  query.set("format", format);
  if (format === "svg") query.set("layout", layout);
  if (!drafts) query.set("drafts", "0");
  return `/api/map/view/${map}?${query}`;
}
const send = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const getView = (map: MapName, root: string | null, depth: number, drafts: boolean, signal?: AbortSignal) =>
  request<MapGraph>(viewUrl(map, root, depth, "json", "tala", drafts), { signal });
export const getViewText = (
  map: MapName,
  root: string | null,
  depth: number,
  format: "mermaid" | "d2" | "svg",
  layout: Layout,
  drafts: boolean,
  signal?: AbortSignal,
) => request<string>(viewUrl(map, root, depth, format, layout, drafts), { signal }, "text");
const documentsUrl = (entry: EntryRef) => `/api/inventory/${entry.kind}/${encodeURIComponent(entry.id)}/documents`;
export const getEntryDocuments = (entry: EntryRef, signal?: AbortSignal) =>
  request<EntryDocuments>(documentsUrl(entry), { signal });
/** Links a live document to the entry (a `linked` link); answers the entry's documents. */
export const linkDocument = (entry: EntryRef, documentId: string) =>
  request<EntryDocuments>(documentsUrl(entry), send("POST", { document_id: documentId }));
/** Removes the document's `linked` link to the entry (a built-for link can't be removed). */
export const unlinkDocument = (entry: EntryRef, documentId: string) =>
  request<EntryDocuments>(`${documentsUrl(entry)}/${encodeURIComponent(documentId)}`, { method: "DELETE" });
/** The New page for the entry's inventory document: built for it, tagged `inventory`. */
export const newInventoryDocumentPath = (entry: EntryRef) =>
  `/documents/new?${new URLSearchParams({ inventory: entry.kind, resource_id: entry.id })}`;
