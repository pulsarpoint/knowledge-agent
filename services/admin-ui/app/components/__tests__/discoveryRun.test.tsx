import { afterEach, expect, test, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import DiscoveryRunView from "../DiscoveryRunView";

const live = vi.hoisted(() => ({ status: "waiting" }));
vi.mock("~/lib/useSessionEvents", () => ({
  useSessionEvents: () => ({ session: { id: "r1", title: "Discovery · web-1", status: live.status, purpose: "discovery", agent_name: "Ad-hoc agent",
    model_name: "GPT", run_resources: {}, turns: 1 }, setSession: vi.fn(), error: "", setError: vi.fn(), events: [], older: "start", loadOlder: vi.fn(), toolCounts: undefined, reconnecting: false }),
  useSessionNotice: () => ({ notice: null, clear: vi.fn(), stopping: vi.fn(), sent: vi.fn() }),
}));
vi.mock("../RunWorkspace", () => ({ default: ({ heading, sidebar }: { heading: React.ReactNode; sidebar: React.ReactNode }) => <div>{heading}{sidebar}</div> }));

const update = { id: "q1", kind: "service", number: 1, status: "pending", revision: 2, session_id: "r1", server_id: "s1", result_id: null, decided_at: null,
  service_id: "n1", document_id: null, service_proposal_id: null,
  payload: { name: "nginx", service_type: "builtin", service_role: "unclassified", description: "Reverse proxy", owner: "platform", operations_notes: "",
    deployment: { environment_id: "e1", environment: "Production", runtime: "", runtime_name: "nginx", port: null, log_location: "", deployed_version: "1.24" },
    depends_on: [], built_from: [], matched_revision: 3 },
  current: { id: "n1", name: "nginx", revision: 3, service_type: "unclassified", service_role: "proxy", description: "Front proxy", owner: "web team", operations_notes: "" },
  changes: ["service_type", "description", "owner"],
  current_deployment: { id: "d1", environment_id: "e1", environment: "Production", runtime: "systemd", runtime_name: "nginx", port: 80, log_location: "", deployed_version: "" } };
const document = { id: "q2", kind: "document", number: 2, status: "pending", revision: 1, session_id: "r1", server_id: "s1", result_id: null, decided_at: null,
  service_id: null, document_id: null, service_proposal_id: null, payload: { title: "Disk layout", description: "", markdown: "# Disks", about_service: null } };

function api(routes: Record<string, (body: unknown) => [number, unknown]>) {
  const calls: { key: string; body: unknown }[] = [];
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
const show = () => render(<MemoryRouter><DiscoveryRunView serverId="s1" runId="r1" /></MemoryRouter>);
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); live.status = "waiting"; });

test("lists proposals; an update shows current against proposed and accepts only the kept fields", async () => {
  const user = userEvent.setup();
  const calls = api({ "GET /api/discovery/r1/proposals": () => [200, { proposals: [update, document] }],
    "POST /api/discovery/proposals/q1/accept": () => [200, { proposal: { ...update, status: "accepted" }, result_id: "n1", export_error: null }] });
  show();
  const list = await screen.findByRole("list", { name: "Proposals" });
  expect(within(list).getByText("1 · Update service nginx")).toBeInTheDocument();
  expect(within(list).getByText("2 · New document Disk layout")).toBeInTheDocument();
  await user.click(within(list).getAllByRole("button", { name: "Review" })[0]);
  const owner = await screen.findByRole("checkbox", { name: "Keep Owner" });
  expect(screen.getByText("web team")).toBeInTheDocument();
  expect(screen.getByText("platform")).toBeInTheDocument();
  await user.click(owner);
  await user.click(screen.getByRole("button", { name: "Accept" }));
  await waitFor(() => expect(calls.some(c => c.key === "POST /api/discovery/proposals/q1/accept")).toBe(true));
  expect(calls.find(c => c.key === "POST /api/discovery/proposals/q1/accept")!.body).toEqual({ revision: 2, fields: ["service_type", "description"] });
});

test("a document shows its Markdown; Reject rejects it", async () => {
  const user = userEvent.setup();
  const calls = api({ "GET /api/discovery/r1/proposals": () => [200, { proposals: [document] }],
    "POST /api/discovery/proposals/q2/reject": () => [200, { proposal: { ...document, status: "rejected" }, rejected_documents: 0 }] });
  show();
  await user.click(await screen.findByRole("button", { name: "Review" }));
  expect(await screen.findByRole("heading", { name: "Disks" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Reject" }));
  await waitFor(() => expect(calls.find(c => c.key === "POST /api/discovery/proposals/q2/reject")!.body).toEqual({ revision: 1 }));
});

test("a refused decision shows the reason and reloads the proposals", async () => {
  const user = userEvent.setup();
  let loads = 0;
  api({ "GET /api/discovery/r1/proposals": () => { loads += 1; return [200, { proposals: [document] }]; },
    "POST /api/discovery/proposals/q2/accept": () => [409, { error: { message: "A document titled Disk layout was added since this proposal was made. It is now shown as a new version. Review it again." } }] });
  show();
  await user.click(await screen.findByRole("button", { name: "Review" }));
  await user.click(screen.getByRole("button", { name: "Accept" }));
  expect((await screen.findAllByText(/It is now shown as a new version/)).length).toBeGreaterThan(0);
  await waitFor(() => expect(loads).toBeGreaterThanOrEqual(2));
});

test("a refused decision whose reload brings a new revision keeps its reason, shown once", async () => {
  const user = userEvent.setup();
  let loads = 0;
  api({ "GET /api/discovery/r1/proposals": () => { loads += 1; return [200, { proposals: [loads === 1 ? document : { ...document, revision: 2, document_id: "d1" }] }]; },
    "POST /api/discovery/proposals/q2/accept": () => [409, { error: { message: "A document titled Disk layout was added. Review it again." } }] });
  show();
  await user.click(await screen.findByRole("button", { name: "Review" }));
  await user.click(screen.getByRole("button", { name: "Accept" }));
  expect(await screen.findByText("A document titled Disk layout was added. Review it again.")).toBeInTheDocument();
  await waitFor(() => expect(loads).toBe(2));
  expect(screen.getByText("A document titled Disk layout was added. Review it again.")).toBeInTheDocument();
});

const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

test("polling never overlaps a pending load, and nothing loads or resolves after unmount", async () => {
  vi.useFakeTimers();
  live.status = "working";
  let release: (value: Response) => void = () => {};
  const fetchMock = vi.fn(() => new Promise<Response>(resolve => { release = resolve; }));
  vi.stubGlobal("fetch", fetchMock);
  const view = show();
  await tick(10000);
  expect(fetchMock).toHaveBeenCalledTimes(1);  // the first load is still pending: every tick was skipped
  release(new Response(JSON.stringify({ proposals: [document] })));
  await tick(0);
  expect(screen.getByText("2 · New document Disk layout")).toBeInTheDocument();
  await tick(3000);
  expect(fetchMock).toHaveBeenCalledTimes(2);
  view.unmount();
  release(new Response(JSON.stringify({ proposals: [] })));
  await tick(10000);
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

test("a refused decision's reason is shown beside the buttons, after the proposal's text", async () => {
  const user = userEvent.setup();
  api({ "GET /api/discovery/r1/proposals": () => [200, { proposals: [document] }],
    "POST /api/discovery/proposals/q2/accept": () => [400, { error: { message: "The document could not be saved." } }] });
  show();
  await user.click(await screen.findByRole("button", { name: "Review" }));
  await user.click(screen.getByRole("button", { name: "Accept" }));
  const reason = await screen.findByText("The document could not be saved.");
  const text = screen.getByRole("heading", { name: "Disks" });
  expect(text.compareDocumentPosition(reason) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(within(screen.getByRole("group", { name: "Decision" })).getByText("The document could not be saved.")).toBeInTheDocument();
});

test("a document about a service still pending in this run cannot be accepted until the service is", async () => {
  const user = userEvent.setup();
  const about = { ...document, service_proposal_id: "q1", service_name: "nginx", service_proposal_status: "pending",
    payload: { ...document.payload, about_service: "nginx" } };
  api({ "GET /api/discovery/r1/proposals": () => [200, { proposals: [about] }] });
  show();
  await user.click(await screen.findByRole("button", { name: "Review" }));
  expect(await screen.findByRole("button", { name: "Accept" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Reject" })).toBeEnabled();
  expect(screen.getByText("Accept the service proposal nginx first.")).toBeInTheDocument();
});

test("accepting a new version of a document asks first", async () => {
  const user = userEvent.setup();
  const version = { ...document, id: "q3", number: 3, document_id: "doc1", current: { id: "doc1", title: "web-1 overview", version: 3 } };
  const calls = api({ "GET /api/discovery/r1/proposals": () => [200, { proposals: [version] }],
    "POST /api/discovery/proposals/q3/accept": () => [200, { proposal: { ...version, status: "accepted", result_id: "doc1" }, result_id: "doc1", export_error: null }] });
  show();
  await user.click(await screen.findByRole("button", { name: "Review" }));
  await user.click(screen.getByRole("button", { name: "Accept" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent("This replaces the live version 3 of “web-1 overview”. Continue?");
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(calls.some(c => c.key === "POST /api/discovery/proposals/q3/accept")).toBe(false);
  await user.click(screen.getByRole("button", { name: "Accept" }));
  await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Continue" }));
  await waitFor(() => expect(calls.find(c => c.key === "POST /api/discovery/proposals/q3/accept")!.body).toEqual({ revision: 1 }));
});
