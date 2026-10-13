import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider, useLocation } from "react-router";
import ServerPage from "../ServerPage";
import { WidePageContext } from "~/lib/widePage";
import { options } from "~/lib/__tests__/agentOptionsFixture";

const live = vi.hoisted(() => ({ sessions: {} as Record<string, any>, requested: [] as (string | null)[] }));
vi.mock("~/lib/useSessionEvents", async () => {
  const { useState } = await import("react");
  return {
    useSessionEvents: (id: string | null) => {
      live.requested.push(id);
      const [error, setError] = useState("");
      return { session: id ? live.sessions[id] ?? null : null, setSession: vi.fn(), error, setError, events: [], older: "start",
        loadOlder: vi.fn(), toolCounts: undefined, reconnecting: false };
    },
    useSessionNotice: () => ({ notice: null, clear: vi.fn(), stopping: vi.fn(), sent: vi.fn() }),
  };
});
vi.mock("../RunWorkspace", () => ({ default: ({ heading, headerActions, empty, composer }: Record<string, any>) => <div>
  {heading}{headerActions}{empty}
  <label>Message<textarea value={composer.value} onChange={e => composer.onChange(e.target.value)} /></label>
  {composer.blockedReason && <p>{composer.blockedReason}</p>}
  <button type="button" onClick={composer.onSend}>Send message</button></div> }));

const server = { id: "s1", name: "web-1", hostname: "10.0.0.7", ssh_port: 22, ssh_username: "ops", description: "Shop front end",
  tags: [], revision: 2, created_at: "", updated_at: "", access_enabled: true, allow_sudo: false, ssh_allowed: true };
const NOT_READY = "Set up SSH for this server first (SSH settings).";
const page = (ready: boolean, reason: string | null = null) => ({ server, environments: [{ id: "e1", name: "Production" }],
  deployments: [{ id: "d1", service_id: "n1", service_name: "nginx", environment: "Production", runtime: "systemd", runtime_name: "nginx", port: 80, deployed_version: "" }],
  ssh_ready: reason !== NOT_READY, discovery: { ready, reason } });
const at = "2026-10-11T10:00:00Z";
const run = (id: string, status: string, title = `Discovery ${id}`) => ({ id, title, status, model_name: "GPT", started_by: null, created_at: at,
  last_activity_at: at, draft_id: null, pending: 1, accepted: 0, rejected: 0 });
const detail = (id: string, status: string, serverId = "s1") => ({ id, title: `Discovery ${id}`, status, purpose: "discovery", server_id: serverId,
  agent_name: "Ad-hoc agent", model_name: "GPT", turns: 1, config: {}, document_draft: null });
const proposal = { id: "q1", kind: "document", number: 1, status: "pending", revision: 1, session_id: "r1", server_id: "s1", result_id: null,
  decided_at: null, service_id: null, document_id: null, service_proposal_id: null,
  payload: { title: "Disk layout", description: "", markdown: "# Disks", about_service: null } };

const RUNS = "GET /api/sessions?server_id=s1";
const base = (ready = true, reason: string | null = null): Record<string, (body: any) => [number, unknown]> => ({
  "GET /api/servers/s1": () => [200, page(ready, reason)],
  "GET /api/inventory/documents?entry_kind=server&entry_id=s1": () => [200, { documents: [] }],
  "GET /api/sessions/options?server_id=s1": () => [200, options],
  [RUNS]: () => [200, { sessions: [] }],
});
function api(routes: Record<string, (body: any) => [number, unknown]>) {
  const calls: { key: string; body: any }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ key, body });
    if (!(key in routes)) throw new Error(`unexpected ${key}`);
    const [status, value] = routes[key](body);
    return new Response(JSON.stringify(value), { status });
  }));
  return calls;
}
const NativeRequest = globalThis.Request;
beforeEach(() => {
  vi.stubGlobal("Request", class extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) { super(input, { ...init, signal: undefined }); }
  });
  try { localStorage.clear(); } catch { /* none */ }
});
afterEach(() => { vi.unstubAllGlobals(); live.sessions = {}; live.requested = []; });

const wide = { value: false };
/** The location React rendered (the router's state may run ahead of it). */
function Where() { return <output data-testid="where">{useLocation().pathname}</output>; }
function show(url = "/servers/s1") {
  // as in routes.ts: the run link is a child of the server page
  const router = createMemoryRouter([{ path: "/servers/:serverId", element: <><ServerPage serverId="s1" /><Where /></>,
    children: [{ path: "discovery/:runId", element: null }] }], { initialEntries: [url] });
  wide.value = false;
  render(<WidePageContext.Provider value={on => { wide.value = on; }}><RouterProvider router={router} /></WidePageContext.Provider>);
  return router;
}

test("shows the server, its services and documents", async () => {
  api({ ...base(), "GET /api/inventory/documents?entry_kind=server&entry_id=s1": () => [200, { documents: [
    { id: "a1", entry_kind: "server", entry_id: "s1", document_id: "doc1", slug: "web-1-overview", title: "web-1 overview", created_at: "" }] }] });
  show();
  expect(await screen.findByRole("heading", { name: "web-1" })).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Services" })).getByText("Production")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "nginx" })).toHaveAttribute("href", "/services");
  expect(screen.getByRole("link", { name: "web-1 overview" })).toHaveAttribute("href", "/documents/doc1");
  expect(screen.queryByRole("region", { name: "Discovery" })).toBeNull();
  expect(screen.getAllByRole("button").map(item => item.textContent)).toEqual(["Ask agent", "Connections and documents"]);
});

test("Ask agent opens the chat and the page goes wide; the first message starts a run from the server", async () => {
  const calls = api({ ...base(), "POST /api/sessions": () => { live.sessions.r1 = detail("r1", "starting"); return [201, detail("r1", "starting")]; },
    "GET /api/discovery/r1/proposals": () => [200, { proposals: [] }] });
  show();
  const toggle = await screen.findByRole("button", { name: "Ask agent" });
  expect(toggle).toHaveAttribute("aria-pressed", "false");
  expect(screen.queryByRole("region", { name: "Ask agent" })).toBeNull();
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-pressed", "true");
  const pane = screen.getByRole("region", { name: "Ask agent" });
  expect(wide.value).toBe(true);
  expect(screen.getByTestId("server-split")).toHaveClass("lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]");
  expect(within(pane).getByText("Ask the agent to explore this server.")).toBeInTheDocument();
  await waitFor(() => expect(within(pane).getByRole("button", { name: "Settings" })).toBeEnabled());
  fireEvent.change(within(pane).getByLabelText("Message"), { target: { value: "Explore it." } });
  fireEvent.click(within(pane).getByRole("button", { name: "Send message" }));
  await waitFor(() => expect(calls.some(c => c.key === "POST /api/sessions")).toBe(true));
  const body = calls.find(c => c.key === "POST /api/sessions")!.body;
  expect(body.started_from).toEqual({ server_id: "s1" });
  expect(body.message).toBe("Explore it.");
  expect(body.resources.server_ids).toContain("s1");
  // the URL follows the chat's run, and the proposals panel shows above the details
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/servers/s1/discovery/r1"));
  expect(await screen.findByRole("region", { name: "Proposals panel" })).toBeInTheDocument();
  fireEvent.click(toggle);
  expect(wide.value).toBe(false);
  expect(screen.queryByRole("region", { name: "Ask agent" })).toBeNull();
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/servers\/s1$/));
});

test("the run link opens the page with the chat on that run and its proposals above the details", async () => {
  live.sessions.r1 = detail("r1", "waiting");
  const calls = api({ ...base(), [RUNS]: () => [200, { sessions: [run("r2", "waiting"), run("r1", "waiting")] }],
    "GET /api/discovery/r1/proposals": () => [200, { proposals: [proposal] }] });
  show("/servers/s1/discovery/r1");
  const pane = await screen.findByRole("region", { name: "Ask agent" });
  expect(screen.getByRole("button", { name: "Ask agent" })).toHaveAttribute("aria-pressed", "true");
  expect(await within(pane).findByRole("heading", { name: "Discovery r1" })).toBeInTheDocument();
  const panel = await screen.findByRole("region", { name: "Proposals panel" });
  expect(await within(panel).findByText("1 · New document Disk layout")).toBeInTheDocument();
  expect(within(panel).getByRole("heading", { name: "Proposals · 1 pending" })).toBeInTheDocument();
  // above the server's details
  expect(panel.compareDocumentPosition(screen.getByRole("heading", { name: "web-1" })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(live.requested).not.toContain("r2");
  expect(calls.some(c => c.key === "GET /api/discovery/r1/proposals")).toBe(true);
});

test("Earlier runs lists the server's runs and opens one", async () => {
  live.sessions.r2 = detail("r2", "waiting"); live.sessions.r1 = detail("r1", "closed");
  api({ ...base(), [RUNS]: () => [200, { sessions: [run("r2", "waiting"), run("r1", "closed", "Look at the cron jobs")] }],
    "GET /api/discovery/r1/proposals": () => [200, { proposals: [] }], "GET /api/discovery/r2/proposals": () => [200, { proposals: [] }] });
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Ask agent" }));
  const pane = screen.getByRole("region", { name: "Ask agent" });
  await waitFor(() => expect(live.requested.at(-1)).toBe("r2"));
  fireEvent.keyDown(within(pane).getByRole("button", { name: "Earlier runs" }), { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: /Look at the cron jobs/ }));
  await waitFor(() => expect(live.requested.at(-1)).toBe("r1"));
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/servers/s1/discovery/r1"));
});

test("a server whose SSH is not ready shows its reason and blocks the first message", async () => {
  const calls = api(base(false, NOT_READY));
  show();
  expect(await screen.findByText(NOT_READY)).toBeInTheDocument();
  expect(screen.getByText("SSH not set up")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Ask agent" }));
  const pane = screen.getByRole("region", { name: "Ask agent" });
  expect(within(pane).getByText(NOT_READY)).toBeInTheDocument();
  await waitFor(() => expect(within(pane).getByRole("button", { name: "Settings" })).toBeEnabled());
  fireEvent.change(within(pane).getByLabelText("Message"), { target: { value: "Explore it." } });
  fireEvent.click(within(pane).getByRole("button", { name: "Send message" }));
  await new Promise(resolve => setTimeout(resolve, 20));
  expect(calls.some(c => c.key === "POST /api/sessions")).toBe(false);
});

test("a missing server says so and links back to Servers", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => url.includes("/api/inventory") ? new Response(JSON.stringify({ documents: [] }))
    : new Response(JSON.stringify({ error: { message: "Server not found." } }), { status: 404 })));
  show();
  expect(await screen.findByText("Server not found.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Servers" })).toHaveAttribute("href", "/servers");
});

test("empty services and documents are explained", async () => {
  api({ ...base(), "GET /api/servers/s1": () => [200, { ...page(true), deployments: [] }] });
  show();
  expect(await screen.findByText("No services are recorded on this server.")).toBeInTheDocument();
  expect(screen.getByText("No documents are attached to this server.")).toBeInTheDocument();
});
