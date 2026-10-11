import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import ServerPage from "../ServerPage";

const server = { id: "s1", name: "web-1", hostname: "10.0.0.7", ssh_port: 22, ssh_username: "ops", description: "Shop front end",
  tags: [], revision: 2, created_at: "", updated_at: "", access_enabled: true, allow_sudo: false, ssh_allowed: true };
const page = (ready: boolean, reason: string | null = null) => ({ server, environments: [{ id: "e1", name: "Production" }],
  deployments: [{ id: "d1", service_id: "n1", service_name: "nginx", environment: "Production", runtime: "systemd", runtime_name: "nginx", port: 80, deployed_version: "" }],
  discovery: { ready, reason } });
const runs = { runs: [{ id: "r1", title: "Discovery · web-1", status: "waiting", model_name: "GPT", created_at: "2026-10-11T10:00:00Z", last_activity_at: null, pending: 2, accepted: 1, rejected: 0 }] };
const prompts = { prompts: [{ id: "p1", name: "Server discovery", description: "", tags: ["server-discovery", "ssh"], enabled: true, active_version_id: "v1", active_version: 1, version_count: 1, revision: 1 }] };
const prompt = { ...prompts.prompts[0], versions: [{ id: "v1", version: 1, content: "Explore this server.", activated_at: "x", created_at: "x" }] };
const llm = { models: [{ id: "m1", name: "GPT", provider: "openai", model: "gpt-5", api_format: "responses", base_url: "http://model.test/v1",
  aws_region: null, enabled: true, revision: 1, has_api_key: false, created_at: "", updated_at: "" }],
  assignments: { search_model_id: null, code_model_id: null, revision: 1 } };

function api(routes: Record<string, unknown>) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : null });
    const key = `${init?.method ?? "GET"} ${url}`;
    const found = key in routes ? routes[key] : routes[url];
    if (found === undefined) throw new Error(`unexpected ${key}`);
    return new Response(JSON.stringify(found), { status: 200 });
  }));
  return calls;
}
function Where() { const l = useLocation(); return <output data-testid="where">{l.pathname}</output>; }
const show = () => render(<MemoryRouter initialEntries={["/servers/s1"]}><Routes>
  <Route path="/servers/:serverId" element={<ServerPage serverId="s1" />} />
  <Route path="*" element={<Where />} /></Routes></MemoryRouter>);
afterEach(() => vi.unstubAllGlobals());

test("shows the server, its services and runs; the button says why it is off", async () => {
  api({ "/api/servers/s1": page(false, "Set up SSH for this server first (SSH settings)."), "/api/servers/s1/discovery": runs,
        "/api/inventory/documents?entry_kind=server&entry_id=s1": { documents: [{ id: "a1", entry_kind: "server", entry_id: "s1", document_id: "doc1", slug: "web-1-overview", title: "web-1 overview", created_at: "" }] } });
  show();
  expect(await screen.findByRole("heading", { name: "web-1" })).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Services" })).getByText("Production")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "nginx" })).toHaveAttribute("href", "/services");
  expect(screen.getByRole("link", { name: "web-1 overview" })).toHaveAttribute("href", "/documents/doc1");
  const run = screen.getByRole("link", { name: /Discovery · web-1/ });
  expect(run).toHaveAttribute("href", "/servers/s1/discovery/r1");
  expect(within(run.closest("li")!).getByText("2 pending · 1 accepted · 0 rejected")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Ask agent to collect info" })).toBeDisabled();
  expect(screen.getByText("Set up SSH for this server first (SSH settings).")).toBeInTheDocument();
});

test("the sheet fills the discovery prompt, which can be edited, and Start opens the run", async () => {
  const user = userEvent.setup();
  const calls = api({ "/api/servers/s1": page(true), "/api/servers/s1/discovery": runs,
    "/api/inventory/documents?entry_kind=server&entry_id=s1": { documents: [] },
    "/api/documentation/prompts?tag=server-discovery": prompts, "/api/documentation/prompts/p1": prompt,
    "/api/llm": llm, "POST /api/servers/s1/discovery": { id: "r2" } });
  show();
  await user.click(await screen.findByRole("button", { name: "Ask agent to collect info" }));
  const text = await screen.findByRole("textbox", { name: "Prompt text" });
  expect(text).toHaveValue("Explore this server.");
  await user.type(text, " Focus on the databases.");
  await user.click(screen.getByRole("button", { name: "Start" }));
  expect(await screen.findByTestId("where")).toHaveTextContent("/servers/s1/discovery/r2");
  expect(calls.find(c => c.method === "POST")!.body).toEqual({ model_id: "m1", prompt: "Explore this server. Focus on the databases.", prompt_id: "p1" });
});

test("without a discovery prompt the sheet says how to add one", async () => {
  const user = userEvent.setup();
  api({ "/api/servers/s1": page(true), "/api/servers/s1/discovery": { runs: [] },
    "/api/inventory/documents?entry_kind=server&entry_id=s1": { documents: [] },
    "/api/documentation/prompts?tag=server-discovery": { prompts: [] }, "/api/llm": llm });
  show();
  await user.click(await screen.findByRole("button", { name: "Ask agent to collect info" }));
  expect(await screen.findByText("No prompt is tagged server-discovery. Add one in Administration → Prompts, or write the instructions here.")).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Prompt text" })).toHaveValue("");
});

const apiFail = (message: string, status: number) => new Response(JSON.stringify({ error: { message } }), { status });

test("a missing server says so and links back to Servers", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => url.endsWith("/discovery") ? new Response(JSON.stringify({ runs: [] })) :
    url.includes("/api/inventory") ? new Response(JSON.stringify({ documents: [] })) : apiFail("Server not found.", 404)));
  show();
  expect(await screen.findByText("Server not found.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Servers" })).toHaveAttribute("href", "/servers");
});

test("empty services, documents and runs are explained", async () => {
  api({ "/api/servers/s1": { ...page(true), deployments: [] }, "/api/servers/s1/discovery": { runs: [] },
    "/api/inventory/documents?entry_kind=server&entry_id=s1": { documents: [] } });
  show();
  expect(await screen.findByText("No services are recorded on this server.")).toBeInTheDocument();
  expect(screen.getByText("No documents are attached to this server.")).toBeInTheDocument();
  expect(screen.getByText("No discovery runs yet.")).toBeInTheDocument();
});

const sheetRoutes = (extra: Record<string, unknown>) => ({ "/api/servers/s1": page(true), "/api/servers/s1/discovery": runs,
  "/api/inventory/documents?entry_kind=server&entry_id=s1": { documents: [] }, "/api/llm": llm, ...extra });

test("a start that the API refuses shows its message and stays on the page", async () => {
  const user = userEvent.setup();
  api(sheetRoutes({ "/api/documentation/prompts?tag=server-discovery": prompts, "/api/documentation/prompts/p1": prompt }));
  const base = (fetch as unknown as ReturnType<typeof vi.fn>).getMockImplementation()!;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) =>
    init?.method === "POST" ? apiFail("Set up SSH for this server first.", 409) : base(url, init)));
  show();
  await user.click(await screen.findByRole("button", { name: "Ask agent to collect info" }));
  await screen.findByDisplayValue("Explore this server.");
  await user.click(screen.getByRole("button", { name: "Start" }));
  expect(await screen.findByText("Set up SSH for this server first.")).toBeInTheDocument();
  expect(screen.queryByTestId("where")).toBeNull();
});

test("Start is off when the prompt text is cleared", async () => {
  const user = userEvent.setup();
  api(sheetRoutes({ "/api/documentation/prompts?tag=server-discovery": prompts, "/api/documentation/prompts/p1": prompt }));
  show();
  await user.click(await screen.findByRole("button", { name: "Ask agent to collect info" }));
  const text = await screen.findByDisplayValue("Explore this server.");
  expect(screen.getByRole("button", { name: "Start" })).toBeEnabled();
  await user.clear(text);
  expect(screen.getByRole("button", { name: "Start" })).toBeDisabled();
});

test("switching prompts replaces the text; a failed load keeps the previous choice", async () => {
  const user = userEvent.setup();
  const second = { ...prompts.prompts[0], id: "p2", name: "Deep dive", active_version_id: "v2" };
  const third = { ...prompts.prompts[0], id: "p3", name: "Broken", active_version_id: "v3" };
  const calls = api(sheetRoutes({ "/api/documentation/prompts?tag=server-discovery": { prompts: [prompts.prompts[0], second, third] },
    "/api/documentation/prompts/p1": prompt,
    "/api/documentation/prompts/p2": { ...second, versions: [{ id: "v2", version: 1, content: "Dig deep.", activated_at: "x", created_at: "x" }] },
    "POST /api/servers/s1/discovery": { id: "r2" } }));
  const base = (fetch as unknown as ReturnType<typeof vi.fn>).getMockImplementation()!;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) =>
    url.endsWith("/prompts/p3") ? apiFail("boom", 500) : base(url, init)));
  show();
  await user.click(await screen.findByRole("button", { name: "Ask agent to collect info" }));
  await screen.findByDisplayValue("Explore this server.");
  await user.click(screen.getByRole("combobox", { name: "Prompt" }));
  await user.click(await screen.findByRole("option", { name: "Deep dive" }));
  expect(await screen.findByDisplayValue("Dig deep.")).toBeInTheDocument();
  await user.click(screen.getByRole("combobox", { name: "Prompt" }));
  await user.click(await screen.findByRole("option", { name: "Broken" }));
  expect(await screen.findByText("Could not load the prompt.")).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Prompt text" })).toHaveValue("Dig deep.");
  await user.click(screen.getByRole("button", { name: "Start" }));
  await screen.findByTestId("where");
  expect(calls.find(c => c.method === "POST")!.body).toMatchObject({ prompt: "Dig deep.", prompt_id: "p2" });
});
