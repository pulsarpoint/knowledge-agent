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
