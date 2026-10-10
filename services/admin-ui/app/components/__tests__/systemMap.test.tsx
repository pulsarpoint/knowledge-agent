import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import SystemMapView from "../SystemMapView";

vi.mock("../Mermaid", () => ({ default: ({ chart }: { chart: string }) => <pre data-testid="mermaid">{chart}</pre> }));

const node = (id: string, name: string, kind: string, extra = {}) => ({ id, parent_id: null, kind, name, description: "", entry_kind: kind,
  entry_id: `${kind}-id`, child_count: 0, expandable: false, internal_edges: 0, documents: [], ...extra });
const top = { view: "infrastructure", depth: 1, root: null, breadcrumb: [], boundary: [], edges: [],
  nodes: [node("environment:p", "production", "environment", { child_count: 1, expandable: true }), node("server:top:s", "spare", "server")] };
const inside = { view: "infrastructure", depth: 1, root: { id: "environment:p", name: "production", kind: "environment", description: "", entry_kind: "environment", entry_id: "p" },
  breadcrumb: [{ id: "environment:p", name: "production", kind: "environment" }], nodes: [node("server:p:s1", "prod-1", "server")],
  boundary: [{ id: "server:top:s", name: "spare", kind: "server", entry_kind: "server", entry_id: "s" }],
  edges: [{ from: "server:p:s1", to: "server:top:s", type: "connects_to", count: 2, link_ids: ["l1", "l2"] }] };
const services = { view: "services", depth: 1, root: null, breadcrumb: [], boundary: [], edges: [], nodes: [] };

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
function Where() { return <output data-testid="where">{useLocation().search}</output>; }
const view = (entry = "/system-map") => render(<MemoryRouter initialEntries={[entry]}>
  <Routes><Route path="/system-map" element={<><SystemMapView /><Where /></>} /></Routes></MemoryRouter>);
const where = () => screen.getByTestId("where").textContent;
const TOP = "/api/map/view/infrastructure?depth=1&format=json";
const TOP_SVG = "/api/map/view/infrastructure?depth=1&format=svg&layout=tala";
const PROD = "/api/map/view/infrastructure?root=environment%3Ap&depth=1&format=json";
const PROD_SVG = "/api/map/view/infrastructure?root=environment%3Ap&depth=1&format=svg&layout=tala";
afterEach(() => vi.unstubAllGlobals());

test("shows the infrastructure map with Inside and Outside lists", async () => {
  api({ [TOP]: { json: top }, [TOP_SVG]: { text: "<svg/>" } });
  view();
  expect(screen.getByRole("tab", { name: "Infrastructure" })).toHaveAttribute("aria-selected", "true");
  expect(await screen.findByRole("img", { name: "Infrastructure map" })).toBeInTheDocument();
  const list = within(screen.getByRole("region", { name: "Inside" }));
  expect(list.getByRole("button", { name: "production" })).toBeInTheDocument();
  expect(list.getByText("environment · +1 inside")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /New node/ })).toBeNull();
});

test("opens an environment, lists what its links reach outside, and goes back with the breadcrumb", async () => {
  const user = userEvent.setup();
  api({ [TOP]: { json: top }, [TOP_SVG]: { text: "<svg/>" }, [PROD]: { json: inside }, [PROD_SVG]: { text: "<svg/>" } });
  view();
  await user.click(await screen.findByRole("button", { name: "Open production" }));
  await waitFor(() => expect(where()).toBe("?map=infrastructure&root=environment%3Ap"));
  const outside = within(await screen.findByRole("region", { name: "Outside" }));
  expect(await outside.findByText("server · 2 links")).toBeInTheDocument();
  await user.click(within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByRole("button", { name: "Infrastructure" }));
  await waitFor(() => expect(where()).toBe("?map=infrastructure"));
});

test("switches to the services map and explains an empty map", async () => {
  const user = userEvent.setup();
  api({ [TOP]: { json: top }, [TOP_SVG]: { text: "<svg/>" }, ["/api/map/view/services?depth=1&format=json"]: { json: services } });
  view();
  await user.click(await screen.findByRole("tab", { name: "Services" }));
  await waitFor(() => expect(where()).toBe("?map=services"));
  expect(await screen.findByText(/Nothing here yet/)).toBeInTheDocument();
});

test("a root that no longer exists shows the error and the breadcrumb still leads back", async () => {
  api({ ["/api/map/view/infrastructure?root=server%3Ax%3Ay&depth=1&format=json"]: { status: 404, error: "Node not found." },
        ["/api/map/view/infrastructure?root=server%3Ax%3Ay&depth=1&format=svg&layout=tala"]: { status: 404, error: "Node not found." } });
  view("/system-map?map=infrastructure&root=server:x:y");
  expect(await screen.findByText("Node not found.")).toBeInTheDocument();
  expect(within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByRole("button", { name: "Infrastructure" })).toBeInTheDocument();
});
