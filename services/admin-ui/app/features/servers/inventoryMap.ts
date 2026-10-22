import { request } from "~/lib/api";

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
export type LinkKind = "depends_on" | "built_from" | "connects_to";
export const LINK_LABELS: Record<LinkKind, string> = {
  depends_on: "depends on",
  built_from: "built from",
  connects_to: "connects to",
};
/** The kinds a link from this entry can have (as the API allows). */
export const linkKindsFrom = (kind: EntryKind): LinkKind[] =>
  kind === "service" ? ["connects_to", "depends_on", "built_from"] : ["connects_to"];
/** The entry kinds a link of this kind can point at; undefined = any. */
export const targetKinds = (kind: LinkKind): EntryKind[] | undefined =>
  kind === "depends_on" ? ["service"] : kind === "built_from" ? ["code_project"] : undefined;
export type MapView = "diagram" | "mermaid" | "d2" | "json";
export const VIEWS: MapView[] = ["diagram", "mermaid", "d2", "json"];
export const VIEW_LABELS: Record<MapView, string> = { diagram: "Diagram", mermaid: "Mermaid", d2: "D2", json: "JSON" };
export type Layout = "tala" | "elk" | "dagre";
export const LAYOUTS: Layout[] = ["tala", "elk", "dagre"];
export const MAX_DEPTH = 3;
export const MAX_ATTRIBUTES = 20;

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
}
/** A node outside the view: an entry, or an external host a connection reaches (not an entry: nothing to open). */
export interface BoundaryNode {
  id: string;
  name: string;
  kind: EntryKind | "external_host";
  entry_kind: EntryKind | "external_host";
  entry_id: string;
}
/** An edge: connections ("connects_to") or service relations, with the ids of the connections or relations drawn. */
export interface ViewEdge {
  from: string;
  to: string;
  type: LinkKind | "part_of";
  count: number;
  ids: string[];
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
export interface Link {
  id: string;
  kind: LinkKind;
  from: MapEntry;
  to: MapEntry;
  description: string;
  attributes: Record<string, string>;
  revision: number;
}
export interface EntryHit {
  entry_kind: EntryKind;
  entry_id: string;
  name: string;
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
  };
}
/** The page URL for a state: the map always, everything else only when it is not the default. */
export function mapQuery(state: MapState): URLSearchParams {
  const query = new URLSearchParams({ map: state.map });
  if (state.root) query.set("root", state.root);
  if (state.depth !== 1) query.set("depth", String(state.depth));
  if (state.view !== "diagram") query.set("view", state.view);
  if (state.layout !== "tala") query.set("layout", state.layout);
  return query;
}
export function viewUrl(
  map: MapName,
  root: string | null,
  depth: number,
  format: "json" | "mermaid" | "d2" | "svg",
  layout: Layout = "tala",
) {
  const query = new URLSearchParams();
  if (root) query.set("root", root);
  query.set("depth", String(depth));
  query.set("format", format);
  if (format === "svg") query.set("layout", layout);
  return `/api/map/view/${map}?${query}`;
}
const send = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
const entryQuery = (entry: EntryRef) => new URLSearchParams({ entry_kind: entry.kind, entry_id: entry.id });

export const getView = (map: MapName, root: string | null, depth: number, signal?: AbortSignal) =>
  request<MapGraph>(viewUrl(map, root, depth, "json"), { signal });
export const getViewText = (
  map: MapName,
  root: string | null,
  depth: number,
  format: "mermaid" | "d2" | "svg",
  layout: Layout,
  signal?: AbortSignal,
) => request<string>(viewUrl(map, root, depth, format, layout), { signal }, "text");
export function searchEntries(q: string, kind?: EntryKind) {
  const query = new URLSearchParams();
  if (kind) query.set("kind", kind);
  query.set("q", q);
  return request<{ entries: EntryHit[] }>(`/api/map/search?${query}`);
}
export const getLinks = (entry: EntryRef) => request<{ links: Link[] }>(`/api/inventory/links?${entryQuery(entry)}`);
export const createLink = (input: {
  from: EntryRef;
  to: EntryRef;
  kind: LinkKind;
  description: string;
  attributes: Record<string, string>;
}) => request<{ link: Link }>("/api/inventory/links", send("POST", input));
export const updateLink = (
  id: string,
  input: { description: string; attributes: Record<string, string>; revision: number },
) => request<{ link: Link }>(`/api/inventory/links/${id}`, send("PUT", input));
export const deleteLink = (id: string) => request<void>(`/api/inventory/links/${id}`, { method: "DELETE" });
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

/** Reads the Attributes field — one "name: value" per line — into an object, or says what is wrong. */
export function parseAttributes(text: string): { value: Record<string, string> } | { problem: string } {
  const value: Record<string, string> = {};
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    const colon = line.indexOf(":");
    const name = colon < 0 ? "" : line.slice(0, colon).trim();
    if (!name) return { problem: "Write each attribute as name: value." };
    if (name in value) return { problem: `The attribute "${name}" appears twice.` };
    value[name] = line.slice(colon + 1).trim();
  }
  const entries = Object.entries(value);
  if (entries.length > MAX_ATTRIBUTES) return { problem: `Use at most ${MAX_ATTRIBUTES} attributes.` };
  if (entries.some(([name]) => name.length > 64)) return { problem: "Attribute names are 1–64 characters." };
  if (entries.some(([, text]) => text.length > 500)) return { problem: "Attribute values are at most 500 characters." };
  return { value };
}
export const formatAttributes = (attributes: Record<string, string>) =>
  Object.entries(attributes)
    .map(([name, text]) => `${name}: ${text}`)
    .join("\n");
