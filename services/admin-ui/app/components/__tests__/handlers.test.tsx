import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import HandlersView from "../HandlersView";

const handler = (extra = {}) => ({ id: "h-1", name: "Triage", enabled: true, type_pattern: "jira.ticket.*",
  conditions: [{ field: "payload.labels", op: "contains", value: "agent" }], action: "emit",
  emit: { type: "triage.requested", subject: "", title: "", payload: "" }, agent_id: null, agent_name: null, prompt: null,
  revision: 1, created_at: "2026-10-09T10:00:00Z", updated_at: "2026-10-09T10:00:00Z", last_run: null, ...extra });
const EVENTS = { events: [{ id: 7, source: "jira", type: "jira.ticket.updated", subject: "DEMO-7", title: "Fix login",
  occurred_at: "2026-10-09T10:00:00Z", created_at: "2026-10-09T10:00:00Z", status: "processed", depth: 0, parent_id: null,
  root_id: 7, handler_id: null }], next_offset: null };

type Route = (body: unknown) => { status?: number; body?: unknown };
function api(routes: Record<string, Route | unknown>) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    const route = routes[key];
    if (route === undefined) throw new Error(`unexpected ${key}`);
    const answer = typeof route === "function" ? (route as Route)(init?.body ? JSON.parse(String(init.body)) : undefined) : { body: route };
    return new Response(answer.status === 204 ? null : JSON.stringify(answer.body), { status: answer.status ?? 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const view = () => render(<MemoryRouter><HandlersView /></MemoryRouter>);
afterEach(() => vi.unstubAllGlobals());
const SHEET_DATA = { "GET /api/event-handlers/agents": { agents: [{ id: "tester", name: "Tester" }] },
  "GET /api/events/facets": { sources: ["jira"], types: ["jira.ticket.updated"], statuses: [], handlers: [] }, "GET /api/events": EVENTS };

test("lists handlers with what they do", async () => {
  api({ "GET /api/event-handlers": { handlers: [handler(), handler({ id: "h-2", name: "Ask", action: "agent", emit: null, agent_id: "tester",
    agent_name: "Tester", prompt: "Do it", last_run: { status: "failed", created_at: "2026-10-09T10:00:00Z" } })] } });
  view();
  expect(await screen.findByText("Triage")).toBeInTheDocument();
  expect(screen.getByText("Emit triage.requested")).toBeInTheDocument();
  expect(screen.getByText("Send to Tester")).toBeInTheDocument();
  expect(screen.getAllByText("jira.ticket.* · 1 condition")).toHaveLength(2);
  expect(screen.getByText("Failed")).toBeInTheDocument();
});

test("empty state", async () => {
  api({ "GET /api/event-handlers": { handlers: [] } });
  view();
  expect(await screen.findByText("No handlers yet. Create one to turn events into new events or agent sessions.")).toBeInTheDocument();
});

test("creating an emit handler with a condition sends the draft", async () => {
  const user = userEvent.setup();
  let sent: unknown;
  api({ "GET /api/event-handlers": { handlers: [] }, ...SHEET_DATA,
    "POST /api/event-handlers": (body: unknown) => { sent = body; return { status: 201, body: { handler: handler() } }; } });
  view();
  await user.click(await screen.findByRole("button", { name: "New handler" }));
  const sheet = await screen.findByRole("dialog");
  await user.type(within(sheet).getByLabelText("Name"), "Triage");
  await user.click(within(sheet).getByRole("button", { name: "Add condition" }));
  await user.type(within(sheet).getByLabelText("Condition 1 field"), "payload.status");
  await user.click(within(sheet).getByRole("combobox", { name: "Condition 1 operator" }));
  await user.click(screen.getByRole("option", { name: "is one of" }));
  await user.type(within(sheet).getByLabelText("Condition 1 value"), "Open, In Progress");
  await user.type(within(sheet).getByLabelText("New event type"), "triage.requested");
  await user.click(within(sheet).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(sent).toEqual({ name: "Triage", enabled: true, type_pattern: "jira.ticket.*",
    conditions: [{ field: "payload.status", op: "in", value: ["Open", "In Progress"] }], action: "emit",
    emit: { type: "triage.requested", subject: "", title: "", payload: "" }, agent_id: null, prompt: null }));
  expect(await screen.findByText("Emit triage.requested")).toBeInTheDocument();
});

test("send to agent picks an agent and a prompt; server errors show in the form", async () => {
  const user = userEvent.setup();
  let sent: Record<string, unknown> = {};
  api({ "GET /api/event-handlers": { handlers: [] }, ...SHEET_DATA,
    "POST /api/event-handlers": (body: unknown) => { sent = body as Record<string, unknown>; return { status: 400, body: { error: { message: "Prompt: unexpected end of template (line 1)." } } }; } });
  view();
  await user.click(await screen.findByRole("button", { name: "New handler" }));
  const sheet = await screen.findByRole("dialog");
  await user.type(within(sheet).getByLabelText("Name"), "Ask");
  await user.click(within(sheet).getByRole("combobox", { name: "Action" }));
  await user.click(screen.getByRole("option", { name: "Send to agent" }));
  await user.click(within(sheet).getByRole("combobox", { name: "Agent" }));
  await user.click(screen.getByRole("option", { name: "Tester" }));
  await user.click(within(sheet).getByLabelText("Prompt"));
  await user.paste("Triage {{ event.subject");
  await user.click(within(sheet).getByRole("button", { name: "Save" }));
  expect(await within(sheet).findByText("Prompt: unexpected end of template (line 1).")).toBeInTheDocument();
  expect([sent.action, sent.agent_id, sent.prompt, sent.emit]).toEqual(["agent", "tester", "Triage {{ event.subject", null]);
});

test("preview shows why an event does not match and the rendered output", async () => {
  const user = userEvent.setup();
  let asked: Record<string, unknown> = {};
  api({ "GET /api/event-handlers": { handlers: [handler()] }, ...SHEET_DATA,
    "POST /api/event-handlers/preview": (body: unknown) => { asked = body as Record<string, unknown>; return { body: { matches: false,
      reason: 'payload.labels is missing; it must contain "agent"', output: { type: "triage.requested", subject: "DEMO-7", title: "Fix login", payload: {} } } }; } });
  view();
  await user.click(await screen.findByRole("button", { name: "Edit Triage" }));
  const sheet = await screen.findByRole("dialog");
  await user.click(within(sheet).getByRole("combobox", { name: "Preview event" }));
  await user.click(screen.getByRole("option", { name: /DEMO-7/ }));
  await user.click(within(sheet).getByRole("button", { name: "Preview" }));
  expect(await within(sheet).findByText('Doesn’t match: payload.labels is missing; it must contain "agent"')).toBeInTheDocument();
  expect(within(sheet).getByText(/"subject": "DEMO-7"/)).toBeInTheDocument();
  expect(asked.event_id).toBe(7);
});

test("the switch saves enabled with the revision; delete asks first", async () => {
  const user = userEvent.setup();
  let saved: Record<string, unknown> = {};
  const fetch = api({ "GET /api/event-handlers": { handlers: [handler()] },
    "PUT /api/event-handlers/h-1": (body: unknown) => { saved = body as Record<string, unknown>; return { body: { handler: handler({ enabled: false, revision: 2 }) } }; },
    "DELETE /api/event-handlers/h-1": () => ({ status: 204 }) });
  view();
  await user.click(await screen.findByRole("switch", { name: "Enabled: Triage" }));
  await waitFor(() => expect([saved.enabled, saved.revision]).toEqual([false, 1]));
  await user.click(screen.getByRole("button", { name: "Delete Triage" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(within(dialog).getByText("Delete handler Triage?")).toBeInTheDocument();
  await user.click(within(dialog).getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(screen.queryByText("Triage")).toBeNull());
  expect(fetch.mock.calls.some(([url, init]) => url === "/api/event-handlers/h-1" && (init as RequestInit)?.method === "DELETE")).toBe(true);
});

test("the form keeps a stored agent that is no longer offered", async () => {
  const user = userEvent.setup();
  let saved: Record<string, unknown> = {};
  api({ "GET /api/event-handlers": { handlers: [handler({ action: "agent", emit: null, agent_id: "gone", agent_name: null, prompt: "Do it" })] },
    ...SHEET_DATA, "PUT /api/event-handlers/h-1": (body: unknown) => { saved = body as Record<string, unknown>; return { body: { handler: handler() } }; } });
  view();
  await user.click(await screen.findByRole("button", { name: "Edit Triage" }));
  const sheet = await screen.findByRole("dialog");
  expect(await within(sheet).findByRole("combobox", { name: "Agent" })).toHaveTextContent("gone (not available)");
  await user.click(within(sheet).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(saved.agent_id).toBe("gone"));
});

test("a broad Send to agent pattern warns that each matching event starts a session", async () => {
  const user = userEvent.setup();
  api({ "GET /api/event-handlers": { handlers: [] }, ...SHEET_DATA });
  view();
  await user.click(await screen.findByRole("button", { name: "New handler" }));
  const sheet = await screen.findByRole("dialog");
  const warning = "Starts one agent session for every matching event.";
  await user.click(within(sheet).getByRole("combobox", { name: "Action" }));
  await user.click(screen.getByRole("option", { name: "Send to agent" }));
  expect(within(sheet).getByText(warning)).toBeInTheDocument();  // jira.ticket.*
  await user.clear(within(sheet).getByLabelText("Event type"));
  await user.type(within(sheet).getByLabelText("Event type"), "jira.ticket.updated");
  expect(within(sheet).queryByText(warning)).toBeNull();
  await user.clear(within(sheet).getByLabelText("Event type"));
  await user.type(within(sheet).getByLabelText("Event type"), "*");
  expect(within(sheet).getByText(warning)).toBeInTheDocument();
});
