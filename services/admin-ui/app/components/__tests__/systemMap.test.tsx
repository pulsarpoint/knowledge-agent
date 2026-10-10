import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import SystemMapView from "../SystemMapView";

vi.mock("../Mermaid", () => ({ default: ({ chart }: { chart: string }) => <pre data-testid="mermaid">{chart}</pre> }));

const node = (id: string, name: string, extra = {}) => ({ id, parent_id: null, layer: "software", kind: "service", name, description: "",
  child_count: 0, expandable: false, internal_edges: 0, documents: [], ...extra });
const top = { layer: "software", depth: 1, root: null, breadcrumb: [],
  nodes: [node("api", "admin-api", { child_count: 2, expandable: true }), node("worker", "repository-worker")], boundary: [],
  edges: [{ from: "api", to: "worker", type: "calls", count: 2, edge_ids: ["e1", "e2"] }] };
const inside = { layer: "software", depth: 1, root: { id: "api", name: "admin-api", kind: "service", description: "" },
  breadcrumb: [{ id: "api", name: "admin-api", kind: "service" }], nodes: [node("events", "Event processor", { kind: "module" })],
  boundary: [{ id: "prod", name: "prod-1", kind: "server", layer: "infrastructure" }],
  edges: [{ from: "prod", to: "events", type: "instance_of", count: 1, edge_ids: ["e3"] }] };
const empty = { layer: "data", depth: 1, root: null, breadcrumb: [], nodes: [], boundary: [], edges: [] };

type Page = { status?: number; json?: unknown; text?: string; error?: string };
function api(pages: Record<string, Page>) {
  const fetch = vi.fn(async (url: string) => {
    const page = pages[url];
    if (!page) throw new Error(`unexpected ${url}`);
    if (page.error) return new Response(JSON.stringify({ error: { message: page.error } }), { status: page.status ?? 500 });
    return new Response(page.text ?? JSON.stringify(page.json), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const urls = (fetch: ReturnType<typeof api>) => fetch.mock.calls.map(call => call[0]);
function Where() { return <output data-testid="where">{useLocation().search}</output>; }
const view = (entry = "/system-map") => render(<MemoryRouter initialEntries={[entry]}>
  <Routes><Route path="/system-map" element={<><SystemMapView /><Where /></>} /></Routes></MemoryRouter>);
const where = () => screen.getByTestId("where").textContent;
const TOP = "/api/graph/view/software?depth=1&format=json";
const TOP_SVG = "/api/graph/view/software?depth=1&format=svg&layout=tala";
const API = "/api/graph/view/software?root=api&depth=1&format=json";
const API_SVG = "/api/graph/view/software?root=api&depth=1&format=svg&layout=tala";
afterEach(() => vi.unstubAllGlobals());

test("shows the top of a layer as a diagram with Inside and Outside lists", async () => {
  api({ [TOP]: { json: top }, [TOP_SVG]: { text: "<svg><text>admin-api</text></svg>" } });
  view();
  expect(screen.getByRole("tab", { name: "Software" })).toHaveAttribute("aria-selected", "true");
  const diagram = await screen.findByRole("img", { name: "Software map" });
  expect(diagram.getAttribute("src")).toBe(`data:image/svg+xml;charset=utf-8,${encodeURIComponent("<svg><text>admin-api</text></svg>")}`);
  const insideList = within(screen.getByRole("region", { name: "Inside" }));
  expect(insideList.getByRole("button", { name: "admin-api" })).toBeInTheDocument();
  expect(insideList.getByText("service · +2 inside")).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Outside" })).getByText("Nothing outside.")).toBeInTheDocument();
});

test("opens a node, shows what is outside it, and goes back with the breadcrumb", async () => {
  const user = userEvent.setup();
  const fetch = api({ [TOP]: { json: top }, [TOP_SVG]: { text: "<svg/>" }, [API]: { json: inside }, [API_SVG]: { text: "<svg/>" } });
  view();
  await user.click(await screen.findByRole("button", { name: "Open admin-api" }));
  await waitFor(() => expect(where()).toBe("?layer=software&root=api"));
  expect(urls(fetch)).toContain(API);
  const breadcrumb = within(screen.getByRole("navigation", { name: "Breadcrumb" }));
  expect(await breadcrumb.findByRole("button", { name: "admin-api" })).toBeInTheDocument();
  const outside = within(screen.getByRole("region", { name: "Outside" }));
  expect(outside.getByText("prod-1")).toBeInTheDocument();
  expect(outside.getByText("Infrastructure · 1 edge")).toBeInTheDocument();
  await user.click(breadcrumb.getByRole("button", { name: "Software" }));
  await waitFor(() => expect(where()).toBe("?layer=software"));
});

test("an outside node opens in its own layer", async () => {
  const user = userEvent.setup();
  api({ [API]: { json: inside }, [API_SVG]: { text: "<svg/>" },
    "/api/graph/view/infrastructure?root=prod&depth=1&format=json": { json: { ...empty, layer: "infrastructure" } },
    "/api/graph/view/infrastructure?root=prod&depth=1&format=svg&layout=tala": { text: "<svg/>" } });
  view("/system-map?layer=software&root=api");
  await user.click(await screen.findByRole("button", { name: "Open prod-1" }));
  await waitFor(() => expect(where()).toBe("?layer=infrastructure&root=prod"));
});

test("keeps layer, root, depth and view in the URL", async () => {
  const user = userEvent.setup();
  const fetch = api({
    "/api/graph/view/infrastructure?root=prod&depth=2&format=json": { json: { ...inside, layer: "infrastructure", boundary: [], edges: [] } },
    "/api/graph/view/infrastructure?root=prod&depth=2&format=d2": { text: 'n1: "admin-api @ prod-1" {shape: rectangle}\n' },
    "/api/graph/view/business?depth=2&format=json": { json: { ...empty, layer: "business" } },
    "/api/graph/view/business?depth=2&format=d2": { text: "" } });
  view("/system-map?layer=infrastructure&root=prod&depth=2&view=d2");
  expect(await screen.findByText('n1: "admin-api @ prod-1" {shape: rectangle}')).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "D2" })).toHaveAttribute("aria-selected", "true");
  expect(screen.queryByLabelText("Layout")).toBeNull();  // layouts only apply to the diagram
  await user.click(screen.getByRole("tab", { name: "Business" }));
  await waitFor(() => expect(where()).toBe("?layer=business&depth=2&view=d2"));
  expect(urls(fetch)).toContain("/api/graph/view/business?depth=2&format=json");
});

test("shows the diagram error but keeps the lists and the other views", async () => {
  const user = userEvent.setup();
  api({ [TOP]: { json: top }, [TOP_SVG]: { status: 503, error: "d2 is not installed." },
    "/api/graph/view/software?depth=1&format=mermaid": { text: "flowchart LR\n" } });
  view();
  expect(await screen.findByText("d2 is not installed.")).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Inside" })).getByRole("button", { name: "admin-api" })).toBeInTheDocument();
  await user.click(screen.getByRole("tab", { name: "Mermaid" }));
  expect((await screen.findByTestId("mermaid")).textContent).toBe("flowchart LR\n");
});

test("shows an error when the root is gone", async () => {
  const user = userEvent.setup();
  api({ "/api/graph/view/software?root=gone&depth=1&format=json": { status: 404, error: "Node not found." },
    "/api/graph/view/software?root=gone&depth=1&format=svg&layout=tala": { status: 404, error: "Node not found." },
    [TOP]: { json: top }, [TOP_SVG]: { text: "<svg/>" } });
  view("/system-map?layer=software&root=gone");
  expect((await screen.findAllByText("Node not found.")).length).toBeGreaterThan(0);  // the diagram may say it too, briefly
  await user.click(within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByRole("button", { name: "Software" }));
  expect(await screen.findByRole("img", { name: "Software map" })).toBeInTheDocument();
});

test("the JSON view shows the view with Copy, and an empty layer offers New node", async () => {
  api({ "/api/graph/view/data?depth=1&format=json": { json: empty } });
  view("/system-map?layer=data&view=json");
  expect(await screen.findByText("Nothing here yet. Add a node to start.")).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "New node" }).length).toBeGreaterThan(0);
});

test("New node opens the sheet in the current layer and root", async () => {
  const user = userEvent.setup();
  api({ [API]: { json: inside }, [API_SVG]: { text: "<svg/>" } });
  view("/system-map?layer=software&root=api");
  await screen.findByRole("button", { name: "Open Event processor" });
  await user.click(screen.getAllByRole("button", { name: "New node" })[0]);
  expect(await screen.findByText("In the Software layer, inside admin-api.")).toBeInTheDocument();
});
