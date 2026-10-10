import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import NodeSheet, { type SheetTarget } from "../NodeSheet";
import { mapQuery, readMapState } from "../../lib/systemGraph";

const events = { id: "events", layer: "software", kind: "module", name: "Event processor", description: "Runs handlers", parent_id: "api",
  properties: { language: "python" }, revision: 3, created_at: "2026-10-10T10:00:00Z", updated_at: "2026-10-10T10:00:00Z",
  breadcrumb: [{ id: "api", name: "admin-api", kind: "service" }, { id: "events", name: "Event processor", kind: "module" }],
  children: [{ id: "match", name: "matching", kind: "module", child_count: 0 }],
  outgoing: [{ id: "e1", type: "calls", description: "", revision: 1, node: { id: "worker", name: "repository-worker", kind: "service", layer: "software" } }],
  incoming: [{ id: "e2", type: "instance_of", description: "", revision: 1, node: { id: "prod", name: "prod-1", kind: "server", layer: "infrastructure" } }],
  documents: [{ id: "d1", slug: "runbook", title: "Runbook" }] };

type Reply = { status?: number; body?: unknown };
interface Call { method: string; url: string; body: unknown }
function api(routes: Record<string, (body: unknown) => Reply>) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, url, body });
    const route = routes[`${method} ${url}`];
    if (!route) throw new Error(`unexpected ${method} ${url}`);
    const { status = 200, body: out = {} } = route(body);
    return new Response(status === 204 ? null : JSON.stringify(out), { status });
  }));
  return calls;
}
const sent = (calls: Call[], method: string) => calls.filter(call => call.method === method);
function open(target: SheetTarget) {
  const onClose = vi.fn(), onChanged = vi.fn();
  render(<MemoryRouter><NodeSheet target={target} onClose={onClose} onChanged={onChanged} /></MemoryRouter>);
  return { onClose, onChanged };
}
const loaded = { "GET /api/graph/nodes/events": () => ({ body: { node: events } }) };
afterEach(() => vi.unstubAllGlobals());

test("the map state reads and writes the URL with defaults left out", () => {
  expect(readMapState(new URLSearchParams("layer=data&root=x&depth=9&view=png&layout=elk"))).toEqual(
    { layer: "data", root: "x", depth: 1, view: "diagram", layout: "elk" });
  expect(readMapState(new URLSearchParams(""))).toEqual({ layer: "software", root: null, depth: 1, view: "diagram", layout: "tala" });
  expect(mapQuery({ layer: "software", root: null, depth: 1, view: "diagram", layout: "tala" }).toString()).toBe("layer=software");
  expect(mapQuery({ layer: "data", root: "x", depth: 2, view: "d2", layout: "elk" }).toString()).toBe("layer=data&root=x&depth=2&view=d2&layout=elk");
});

test("creates a node inside the current root", async () => {
  const user = userEvent.setup();
  const calls = api({ "POST /api/graph/nodes": () => ({ status: 201, body: { node: events } }) });
  const { onClose, onChanged } = open({ create: { layer: "software", parentId: "api", parentName: "admin-api" } });
  expect(screen.getByText("In the Software layer, inside admin-api.")).toBeInTheDocument();
  const create = screen.getByRole("button", { name: "Create" });
  expect(create).toBeDisabled();
  await user.type(screen.getByLabelText("Kind"), "module");
  await user.type(screen.getByLabelText("Name"), "  Event processor ");
  await user.click(screen.getByLabelText("Properties"));
  await user.paste("[1]");
  expect(screen.getByText('Properties must be a JSON object, like {"port": 8766}.')).toBeInTheDocument();
  expect(create).toBeDisabled();
  await user.clear(screen.getByLabelText("Properties"));
  await user.paste('{"language": "python"}');
  await user.click(create);
  await waitFor(() => expect(onClose).toHaveBeenCalled());
  expect(onChanged).toHaveBeenCalled();
  expect(sent(calls, "POST")[0].body).toEqual({ layer: "software", parent_id: "api", kind: "module", name: "Event processor",
    description: "", properties: { language: "python" } });
});

test("shows the API's message when creating fails", async () => {
  const user = userEvent.setup();
  api({ "POST /api/graph/nodes": () => ({ status: 409, body: { error: { message: 'A node named "x" already exists here.' } } }) });
  open({ create: { layer: "data", parentId: null, parentName: null } });
  expect(screen.getByText("In the Data layer, at the top level.")).toBeInTheDocument();
  await user.type(screen.getByLabelText("Kind"), "table");
  await user.type(screen.getByLabelText("Name"), "x");
  await user.click(screen.getByRole("button", { name: "Create" }));
  expect(await screen.findByText('A node named "x" already exists here.')).toBeInTheDocument();
});

test("edits a node and saves it with its revision", async () => {
  const user = userEvent.setup();
  const calls = api({ ...loaded, "PUT /api/graph/nodes/events": () => ({ body: { node: { ...events, name: "Events", revision: 4 } } }) });
  const { onChanged } = open({ nodeId: "events" });
  expect(await screen.findByText("Software · admin-api › Event processor")).toBeInTheDocument();
  const name = screen.getByLabelText("Name");
  expect(name).toHaveValue("Event processor");
  expect(screen.getByLabelText("Properties")).toHaveValue('{\n  "language": "python"\n}');
  await user.clear(name);
  await user.type(name, "Events");
  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(onChanged).toHaveBeenCalled());
  expect(sent(calls, "PUT")[0].body).toEqual({ kind: "module", name: "Events", description: "Runs handlers",
    properties: { language: "python" }, parent_id: "api", revision: 3 });
});

test("a stale save shows the message", async () => {
  const user = userEvent.setup();
  api({ ...loaded, "PUT /api/graph/nodes/events": () => ({ status: 409,
    body: { error: { message: "This node was changed by someone else. Reload and try again." } } }) });
  open({ nodeId: "events" });
  await user.click(await screen.findByRole("button", { name: "Save" }));
  expect(await screen.findByText("This node was changed by someone else. Reload and try again.")).toBeInTheDocument();
});

test("moves the node under a node found by name, or to the top level", async () => {
  const user = userEvent.setup();
  const calls = api({ ...loaded,
    "GET /api/graph/nodes?layer=software&q=work": () => ({ body: { nodes: [
      { id: "worker", layer: "software", kind: "service", name: "repository-worker", path: [] },
      { id: "events", layer: "software", kind: "module", name: "Event processor", path: ["admin-api"] }] } }),
    "PUT /api/graph/nodes/events": () => ({ body: { node: events } }) });
  open({ nodeId: "events" });
  const move = within(await screen.findByRole("region", { name: "Move" }));
  await user.type(move.getByLabelText("New parent"), "work");
  await user.click(move.getByRole("button", { name: "Find" }));
  expect(await move.findByText("repository-worker")).toBeInTheDocument();
  expect(move.queryByRole("button", { name: "Move here: Event processor" })).toBeNull();  // not into itself
  await user.click(move.getByRole("button", { name: "Move here: repository-worker" }));
  await waitFor(() => expect(sent(calls, "PUT")).toHaveLength(1));
  expect(sent(calls, "PUT")[0].body).toEqual({ kind: "module", name: "Event processor", description: "Runs handlers",
    properties: { language: "python" }, parent_id: "worker", revision: 3 });
  await user.click(move.getByRole("button", { name: "Move to the top level" }));
  await waitFor(() => expect(sent(calls, "PUT")).toHaveLength(2));
  expect((sent(calls, "PUT")[1].body as { parent_id: unknown }).parent_id).toBeNull();
});

test("lists, adds and removes edges", async () => {
  const user = userEvent.setup();
  const calls = api({ ...loaded,
    "GET /api/graph/nodes?q=prod": () => ({ body: { nodes: [{ id: "prod", layer: "infrastructure", kind: "server", name: "prod-1", path: [] }] } }),
    "POST /api/graph/edges": () => ({ status: 201, body: { edge: {} } }),
    "DELETE /api/graph/edges/e1": () => ({ status: 204 }) });
  open({ nodeId: "events" });
  const edges = within(await screen.findByRole("region", { name: "Edges" }));
  expect(edges.getByText("calls → repository-worker")).toBeInTheDocument();
  expect(edges.getByText("prod-1 → instance of")).toBeInTheDocument();
  await user.type(edges.getByLabelText("Target"), "prod");
  await user.click(edges.getByRole("button", { name: "Find" }));
  expect(await edges.findByText("prod-1 · Infrastructure")).toBeInTheDocument();
  await user.click(edges.getByRole("button", { name: "Add edge: prod-1" }));
  await waitFor(() => expect(sent(calls, "POST")).toHaveLength(1));
  expect(sent(calls, "POST")[0].body).toEqual({ from_id: "events", to_id: "prod", type: "uses", description: "" });
  await user.click(edges.getByRole("button", { name: "Remove calls edge to repository-worker" }));
  await waitFor(() => expect(sent(calls, "DELETE").map(call => call.url)).toEqual(["/api/graph/edges/e1"]));
});

test("attaches and detaches documents", async () => {
  const user = userEvent.setup();
  const calls = api({ ...loaded,
    "GET /api/documentation/documents?q=pay": () => ({ body: { documents: [{ id: "d2", slug: "payments", title: "Payments" },
      { id: "d1", slug: "runbook", title: "Runbook" }], total: 2 } }),
    "PUT /api/graph/nodes/events/documents/d2": () => ({ status: 204 }),
    "DELETE /api/graph/nodes/events/documents/d1": () => ({ status: 204 }) });
  open({ nodeId: "events" });
  const documents = within(await screen.findByRole("region", { name: "Documents" }));
  expect(documents.getByRole("link", { name: "Runbook" })).toHaveAttribute("href", "/documents/d1");
  await user.type(documents.getByLabelText("Find a document"), "pay");
  await user.click(documents.getByRole("button", { name: "Find" }));
  await user.click(await documents.findByRole("button", { name: "Attach: Payments" }));
  expect(documents.queryByRole("button", { name: "Attach: Runbook" })).toBeNull();  // already attached
  await user.click(documents.getByRole("button", { name: "Detach Runbook" }));
  await waitFor(() => expect(calls.filter(call => call.url.includes("/documents/d")).map(call => `${call.method} ${call.url}`)).toEqual([
    "PUT /api/graph/nodes/events/documents/d2", "DELETE /api/graph/nodes/events/documents/d1"]));
});

test("deletes after confirming, and shows why it cannot", async () => {
  const user = userEvent.setup();
  let reply: Reply = { status: 409, body: { error: { message: "Move or delete its children first." } } };
  api({ ...loaded, "DELETE /api/graph/nodes/events": () => reply });
  const { onClose, onChanged } = open({ nodeId: "events" });
  await user.click(await screen.findByRole("button", { name: "Delete" }));
  await user.click(screen.getByRole("button", { name: "Delete node" }));
  expect(await screen.findByText("Move or delete its children first.")).toBeInTheDocument();
  expect(onClose).not.toHaveBeenCalled();
  reply = { status: 204 };
  await user.click(screen.getByRole("button", { name: "Delete" }));
  await user.click(screen.getByRole("button", { name: "Delete node" }));
  await waitFor(() => expect(onClose).toHaveBeenCalled());
  expect(onChanged).toHaveBeenCalled();
});
