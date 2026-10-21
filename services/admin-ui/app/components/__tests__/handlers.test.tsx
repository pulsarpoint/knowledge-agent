import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import HandlersView from "~/features/events/HandlersView";

const handler = (extra = {}) => ({
  id: "h-1",
  name: "Triage",
  enabled: true,
  type_pattern: "jira.ticket.*",
  conditions: [{ field: "payload.labels", op: "contains", value: "agent" }],
  action: "emit",
  emit: { type: "triage.requested", subject: "", title: "", payload: "" },
  agent_from: "fixed",
  agent_id: null,
  agent_name: null,
  prompt: null,
  reply_to_jira: false,
  give_back: false,
  revision: 1,
  created_at: "2026-10-09T10:00:00Z",
  updated_at: "2026-10-09T10:00:00Z",
  last_run: null,
  ...extra,
});
const EVENTS = {
  events: [
    {
      id: 7,
      source: "jira",
      type: "jira.ticket.updated",
      subject: "DEMO-7",
      title: "Fix login",
      occurred_at: "2026-10-09T10:00:00Z",
      created_at: "2026-10-09T10:00:00Z",
      status: "processed",
      depth: 0,
      parent_id: null,
      root_id: 7,
      handler_id: null,
    },
  ],
  next_offset: null,
};

type Route = (body: unknown) => { status?: number; body?: unknown };
function api(routes: Record<string, Route | unknown>) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    const route = routes[key];
    if (route === undefined) throw new Error(`unexpected ${key}`);
    const answer =
      typeof route === "function"
        ? (route as Route)(init?.body ? JSON.parse(String(init.body)) : undefined)
        : { body: route };
    return new Response(answer.status === 204 ? null : JSON.stringify(answer.body), { status: answer.status ?? 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const view = () =>
  render(
    <MemoryRouter>
      <HandlersView />
    </MemoryRouter>,
  );
afterEach(() => vi.unstubAllGlobals());
const SHEET_DATA = {
  "GET /api/event-handlers/agents": { agents: [{ id: "tester", name: "Tester" }] },
  "GET /api/events/facets": { sources: ["jira"], types: ["jira.ticket.updated"], statuses: [], handlers: [] },
  "GET /api/events": EVENTS,
};

test("lists handlers with what they do", async () => {
  api({
    "GET /api/event-handlers": {
      handlers: [
        handler(),
        handler({
          id: "h-2",
          name: "Ask",
          action: "agent",
          emit: null,
          agent_id: "tester",
          agent_name: "Tester",
          prompt: "Do it",
          last_run: { status: "failed", created_at: "2026-10-09T10:00:00Z" },
        }),
      ],
    },
  });
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
  expect(
    await screen.findByText("No handlers yet. Create one to turn events into new events or agent sessions."),
  ).toBeInTheDocument();
});

test("creating an emit handler with a condition sends the draft", async () => {
  const user = userEvent.setup();
  let sent: unknown;
  api({
    "GET /api/event-handlers": { handlers: [] },
    ...SHEET_DATA,
    "POST /api/event-handlers": (body: unknown) => {
      sent = body;
      return { status: 201, body: { handler: handler() } };
    },
  });
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
  expect(within(sheet).queryByRole("checkbox", { name: /Jira ticket/ })).toBeNull();
  await user.click(within(sheet).getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(sent).toEqual({
      name: "Triage",
      enabled: true,
      type_pattern: "jira.ticket.*",
      conditions: [{ field: "payload.status", op: "in", value: ["Open", "In Progress"] }],
      action: "emit",
      emit: { type: "triage.requested", subject: "", title: "", payload: "" },
      agent_from: "fixed",
      agent_id: null,
      prompt: null,
      reply_to_jira: false,
      give_back: false,
    }),
  );
  expect(await screen.findByText("Emit triage.requested")).toBeInTheDocument();
});

test("send to agent picks an agent and a prompt; server errors show in the form", async () => {
  const user = userEvent.setup();
  let sent: Record<string, unknown> = {};
  api({
    "GET /api/event-handlers": { handlers: [] },
    ...SHEET_DATA,
    "POST /api/event-handlers": (body: unknown) => {
      sent = body as Record<string, unknown>;
      return { status: 400, body: { error: { message: "Prompt: unexpected end of template (line 1)." } } };
    },
  });
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
  await user.click(
    within(sheet).getByRole("checkbox", { name: "Also post the answer as a comment on the Jira ticket" }),
  );
  await user.click(within(sheet).getByRole("button", { name: "Save" }));
  expect(await within(sheet).findByText("Prompt: unexpected end of template (line 1).")).toBeInTheDocument();
  expect([sent.action, sent.agent_id, sent.prompt, sent.emit, sent.reply_to_jira]).toEqual([
    "agent",
    "tester",
    "Triage {{ event.subject",
    null,
    true,
  ]);
});

test("preview shows why an event does not match and the rendered output", async () => {
  const user = userEvent.setup();
  let asked: Record<string, unknown> = {};
  api({
    "GET /api/event-handlers": { handlers: [handler()] },
    ...SHEET_DATA,
    "POST /api/event-handlers/preview": (body: unknown) => {
      asked = body as Record<string, unknown>;
      return {
        body: {
          matches: false,
          reason: 'payload.labels is missing; it must contain "agent"',
          output: { type: "triage.requested", subject: "DEMO-7", title: "Fix login", payload: {} },
        },
      };
    },
  });
  view();
  await user.click(await screen.findByRole("button", { name: "Edit Triage" }));
  const sheet = await screen.findByRole("dialog");
  await user.click(within(sheet).getByRole("combobox", { name: "Preview event" }));
  await user.click(screen.getByRole("option", { name: /DEMO-7/ }));
  await user.click(within(sheet).getByRole("button", { name: "Preview" }));
  expect(
    await within(sheet).findByText('Doesn’t match: payload.labels is missing; it must contain "agent"'),
  ).toBeInTheDocument();
  expect(within(sheet).getByText(/"subject": "DEMO-7"/)).toBeInTheDocument();
  expect(asked.event_id).toBe(7);
});

test("the switch saves enabled with the revision; delete asks first", async () => {
  const user = userEvent.setup();
  let saved: Record<string, unknown> = {};
  const fetch = api({
    "GET /api/event-handlers": { handlers: [handler()] },
    "PUT /api/event-handlers/h-1": (body: unknown) => {
      saved = body as Record<string, unknown>;
      return { body: { handler: handler({ enabled: false, revision: 2 }) } };
    },
    "DELETE /api/event-handlers/h-1": () => ({ status: 204 }),
  });
  view();
  await user.click(await screen.findByRole("switch", { name: "Enabled: Triage" }));
  await waitFor(() => expect([saved.enabled, saved.revision]).toEqual([false, 1]));
  await user.click(screen.getByRole("button", { name: "Delete Triage" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(within(dialog).getByText("Delete handler Triage?")).toBeInTheDocument();
  await user.click(within(dialog).getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(screen.queryByText("Triage")).toBeNull());
  expect(
    fetch.mock.calls.some(
      ([url, init]) => url === "/api/event-handlers/h-1" && (init as RequestInit)?.method === "DELETE",
    ),
  ).toBe(true);
});

test("the form keeps a stored agent that is no longer offered", async () => {
  const user = userEvent.setup();
  let saved: Record<string, unknown> = {};
  api({
    "GET /api/event-handlers": {
      handlers: [handler({ action: "agent", emit: null, agent_id: "gone", agent_name: null, prompt: "Do it" })],
    },
    ...SHEET_DATA,
    "PUT /api/event-handlers/h-1": (body: unknown) => {
      saved = body as Record<string, unknown>;
      return { body: { handler: handler() } };
    },
  });
  view();
  await user.click(await screen.findByRole("button", { name: "Edit Triage" }));
  const sheet = await screen.findByRole("dialog");
  expect(await within(sheet).findByRole("combobox", { name: "Agent" })).toHaveTextContent("gone (not available)");
  await user.click(within(sheet).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(saved.agent_id).toBe("gone"));
});

test("a broad Send to agent pattern warns that each matching event runs the agent", async () => {
  const user = userEvent.setup();
  api({ "GET /api/event-handlers": { handlers: [] }, ...SHEET_DATA });
  view();
  await user.click(await screen.findByRole("button", { name: "New handler" }));
  const sheet = await screen.findByRole("dialog");
  const warning = "Runs the agent once for every matching event.";
  await user.click(within(sheet).getByRole("combobox", { name: "Action" }));
  await user.click(screen.getByRole("option", { name: "Send to agent" }));
  expect(
    within(sheet).getByText(
      "Enabled agents with fixed resources. The agent answers once, without a conversation. The answer goes to Notifications.",
    ),
  ).toBeInTheDocument();
  expect(within(sheet).getByText(warning)).toBeInTheDocument(); // jira.ticket.*
  await user.clear(within(sheet).getByLabelText("Event type"));
  await user.type(within(sheet).getByLabelText("Event type"), "jira.ticket.updated");
  expect(within(sheet).queryByText(warning)).toBeNull();
  await user.clear(within(sheet).getByLabelText("Event type"));
  await user.type(within(sheet).getByLabelText("Event type"), "*");
  expect(within(sheet).getByText(warning)).toBeInTheDocument();
});

test("the broad-pattern warning adds that the agent comments on every matching ticket when posting to Jira", async () => {
  const user = userEvent.setup();
  api({ "GET /api/event-handlers": { handlers: [] }, ...SHEET_DATA });
  view();
  await user.click(await screen.findByRole("button", { name: "New handler" }));
  const sheet = await screen.findByRole("dialog");
  await user.click(within(sheet).getByRole("combobox", { name: "Action" }));
  await user.click(screen.getByRole("option", { name: "Send to agent" }));
  const comments = /also comments on every matching Jira ticket/;
  expect(within(sheet).queryByText(comments)).toBeNull();
  await user.click(within(sheet).getByLabelText("Also post the answer as a comment on the Jira ticket"));
  expect(within(sheet).getByText(comments)).toBeInTheDocument();
  await user.clear(within(sheet).getByLabelText("Event type"));
  await user.type(within(sheet).getByLabelText("Event type"), "jira.ticket.updated");
  expect(within(sheet).queryByText(comments)).toBeNull();
});

test("Agent mapped to the assignee saves no agent and offers the for_assignee condition", async () => {
  const user = userEvent.setup();
  let sent: Record<string, unknown> = {};
  api({
    "GET /api/event-handlers": { handlers: [] },
    ...SHEET_DATA,
    "POST /api/event-handlers": (body: unknown) => {
      sent = body as Record<string, unknown>;
      return {
        status: 201,
        body: { handler: handler({ action: "agent", emit: null, agent_from: "assignee", prompt: "Do it" }) },
      };
    },
  });
  view();
  await user.click(await screen.findByRole("button", { name: "New handler" }));
  const sheet = await screen.findByRole("dialog");
  await user.type(within(sheet).getByLabelText("Name"), "Inbox");
  await user.click(within(sheet).getByRole("combobox", { name: "Action" }));
  await user.click(screen.getByRole("option", { name: "Send to agent" }));
  await user.click(within(sheet).getByRole("combobox", { name: "Agent" }));
  await user.click(screen.getByRole("option", { name: "Agent mapped to the assignee" }));
  const add = within(sheet).getByRole("button", { name: "Add payload.for_assignee equals true" });
  await user.click(add);
  expect(within(sheet).getByLabelText("Condition 1 field")).toHaveValue("payload.for_assignee");
  expect(within(sheet).getByLabelText("Condition 1 value")).toHaveValue("true");
  expect(within(sheet).queryByRole("button", { name: "Add payload.for_assignee equals true" })).toBeNull();
  await user.click(within(sheet).getByLabelText("Prompt"));
  await user.paste("Do it");
  await user.click(within(sheet).getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(sent).toMatchObject({
      action: "agent",
      agent_from: "assignee",
      agent_id: null,
      conditions: [{ field: "payload.for_assignee", op: "equals", value: "true" }],
    }),
  );
  expect(await screen.findByText("Send to the assignee’s agent")).toBeInTheDocument();
});

test("a saved assignee handler opens with the assignee target; preview names the agent or why none", async () => {
  const user = userEvent.setup();
  let target: unknown = { agent_id: "ops", agent_name: "Ops agent" };
  api({
    "GET /api/event-handlers": {
      handlers: [handler({ action: "agent", emit: null, agent_from: "assignee", prompt: "Do it" })],
    },
    ...SHEET_DATA,
    "POST /api/event-handlers/preview": () => ({
      body: { matches: true, reason: null, output: { prompt: "Do it" }, target },
    }),
  });
  view();
  await user.click(await screen.findByRole("button", { name: "Edit Triage" }));
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByRole("combobox", { name: "Agent" })).toHaveTextContent("Agent mapped to the assignee");
  await user.click(within(sheet).getByRole("combobox", { name: "Preview event" }));
  await user.click(screen.getByRole("option", { name: /DEMO-7/ }));
  await user.click(within(sheet).getByRole("button", { name: "Preview" }));
  expect(await within(sheet).findByText("Runs Ops agent")).toBeInTheDocument();
  target = { error: "No agent is mapped to Jira user Ana (ana)." };
  await user.click(within(sheet).getByRole("button", { name: "Preview" }));
  expect(await within(sheet).findByText("No run: No agent is mapped to Jira user Ana (ana).")).toBeInTheDocument();
});

const GIVE_BACK = "Give the ticket back after replying";
test("Give the ticket back is offered with Reply in Jira and the assignee's agent, on by default for a new handler", async () => {
  const user = userEvent.setup();
  const sent: Record<string, unknown>[] = [];
  api({
    "GET /api/event-handlers": { handlers: [] },
    ...SHEET_DATA,
    "POST /api/event-handlers": (body: unknown) => {
      sent.push(body as Record<string, unknown>);
      return { status: 400, body: { error: { message: "Not now" } } };
    },
  });
  view();
  await user.click(await screen.findByRole("button", { name: "New handler" }));
  const sheet = await screen.findByRole("dialog");
  await user.click(within(sheet).getByRole("combobox", { name: "Action" }));
  await user.click(screen.getByRole("option", { name: "Send to agent" }));
  await user.click(within(sheet).getByRole("combobox", { name: "Agent" }));
  await user.click(screen.getByRole("option", { name: "Tester" }));
  await user.click(within(sheet).getByLabelText("Also post the answer as a comment on the Jira ticket"));
  expect(within(sheet).queryByLabelText(GIVE_BACK)).toBeNull();
  await user.click(within(sheet).getByRole("combobox", { name: "Agent" }));
  await user.click(screen.getByRole("option", { name: "Agent mapped to the assignee" }));
  expect(within(sheet).getByLabelText(GIVE_BACK)).toBeChecked();
  await user.click(within(sheet).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(sent[0]).toMatchObject({ agent_from: "assignee", reply_to_jira: true, give_back: true }));
  await user.click(within(sheet).getByLabelText("Also post the answer as a comment on the Jira ticket"));
  expect(within(sheet).queryByLabelText(GIVE_BACK)).toBeNull();
  await user.click(within(sheet).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(sent[1]).toMatchObject({ reply_to_jira: false, give_back: false }));
});

test("a saved handler keeps its Give the ticket back setting; turning it off saves false", async () => {
  const user = userEvent.setup();
  let sent: Record<string, unknown> = {};
  const saved = handler({
    action: "agent",
    emit: null,
    agent_from: "assignee",
    prompt: "Do it",
    reply_to_jira: true,
    give_back: true,
  });
  api({
    "GET /api/event-handlers": { handlers: [saved] },
    ...SHEET_DATA,
    "PUT /api/event-handlers/h-1": (body: unknown) => {
      sent = body as Record<string, unknown>;
      return { body: { handler: { ...saved, ...(body as object), revision: 2 } } };
    },
  });
  view();
  await user.click(await screen.findByRole("button", { name: "Edit Triage" }));
  const sheet = await screen.findByRole("dialog");
  const box = within(sheet).getByLabelText(GIVE_BACK);
  expect(box).toBeChecked();
  await user.click(box);
  await user.click(within(sheet).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(sent).toMatchObject({ reply_to_jira: true, give_back: false, revision: 1 }));
});

test("the enabled switch keeps give_back", async () => {
  const user = userEvent.setup();
  let sent: Record<string, unknown> = {};
  const saved = handler({ action: "agent", emit: null, agent_from: "assignee", reply_to_jira: true, give_back: true });
  api({
    "GET /api/event-handlers": { handlers: [saved] },
    "PUT /api/event-handlers/h-1": (body: unknown) => {
      sent = body as Record<string, unknown>;
      return { body: { handler: { ...saved, enabled: false, revision: 2 } } };
    },
  });
  view();
  await user.click(await screen.findByRole("switch", { name: "Enabled: Triage" }));
  await waitFor(() => expect(sent).toMatchObject({ enabled: false, give_back: true }));
});

test("a handler named in the address opens in the sheet", async () => {
  api({ "GET /api/event-handlers": { handlers: [handler()] }, ...SHEET_DATA });
  render(
    <MemoryRouter initialEntries={["/events/handlers?handler=h-1"]}>
      <HandlersView />
    </MemoryRouter>,
  );
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByText("Edit handler")).toBeInTheDocument();
  expect(within(sheet).getByLabelText("Name")).toHaveValue("Triage");
});

const emitSchedule = (id: string, name: string) => ({
  id,
  name,
  target_kind: "event.emit",
  target_id: id,
  cron: "0 6 * * *",
  description: "Every day at 06:00",
  timezone: "UTC",
  enabled: true,
  next_run_at: null,
  running: false,
  last_run_at: null,
  last_finished_at: null,
  last_status: null,
  last_message: null,
  revision: 1,
  params: {},
  handlers: [],
});

test("the Schedule picker sets the type pattern and replaces the schedule condition", async () => {
  const user = userEvent.setup();
  let sent: Record<string, unknown> = {};
  api({
    "GET /api/event-handlers": { handlers: [] },
    ...SHEET_DATA,
    "GET /api/schedules": {
      schedules: [
        emitSchedule("e-1", "Morning summary"),
        emitSchedule("e-2", "Nightly check"),
        { ...emitSchedule("s-1", "Jira auto pull"), target_kind: "jira.pull" },
      ],
    },
    "POST /api/event-handlers": (body: unknown) => {
      sent = body as Record<string, unknown>;
      return { status: 201, body: { handler: handler() } };
    },
  });
  view();
  await user.click(await screen.findByRole("button", { name: "New handler" }));
  const sheet = await screen.findByRole("dialog");
  await user.type(within(sheet).getByLabelText("Name"), "Summary");
  await user.click(within(sheet).getByRole("button", { name: "Add condition" }));
  await user.type(within(sheet).getByLabelText("Condition 1 field"), "payload.trigger");
  await user.type(within(sheet).getByLabelText("Condition 1 value"), "scheduled");
  await user.click(await within(sheet).findByRole("combobox", { name: "Schedule" }));
  expect(screen.queryByRole("option", { name: "Schedule: Jira auto pull" })).toBeNull();
  await user.click(screen.getByRole("option", { name: "Schedule: Morning summary" }));
  expect(within(sheet).getByLabelText("Event type")).toHaveValue("schedule.fired");
  await user.click(within(sheet).getByRole("combobox", { name: "Schedule" }));
  await user.click(screen.getByRole("option", { name: "Schedule: Nightly check" }));
  expect(within(sheet).getByRole("combobox", { name: "Schedule" })).toHaveTextContent("Schedule: Nightly check");
  await user.click(within(sheet).getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect([sent.type_pattern, sent.conditions]).toEqual([
      "schedule.fired",
      [
        { field: "payload.trigger", op: "equals", value: "scheduled" },
        { field: "subject", op: "equals", value: "Nightly check" },
      ],
    ]),
  );
});

test("schedule payload fields are suggested for conditions", async () => {
  const user = userEvent.setup();
  api({ "GET /api/event-handlers": { handlers: [] }, ...SHEET_DATA });
  view();
  await user.click(await screen.findByRole("button", { name: "New handler" }));
  const options = [...document.querySelectorAll("#handler-fields option")].map((o) => o.getAttribute("value"));
  expect(options).toEqual(expect.arrayContaining(["payload.fired_at", "payload.last_run_at", "payload.trigger"]));
});

test("only a handler that takes schedule.fired with the schedule condition gets the schedule delete text", async () => {
  const user = userEvent.setup();
  api({
    "GET /api/event-handlers": {
      handlers: [handler({ conditions: [{ field: "subject", op: "equals", value: "Morning summary" }] })],
    },
  });
  view();
  await user.click(await screen.findByRole("button", { name: "Delete Triage" }));
  expect(screen.getByText("Its run history stays on the events.")).toBeInTheDocument();
});

test("deleting a schedule handler says the schedule keeps recording events", async () => {
  const user = userEvent.setup();
  api({
    "GET /api/event-handlers": {
      handlers: [
        handler({
          type_pattern: "schedule.fired",
          conditions: [{ field: "subject", op: "equals", value: "Morning summary" }],
        }),
      ],
    },
  });
  view();
  await user.click(await screen.findByRole("button", { name: "Delete Triage" }));
  expect(
    screen.getByText("Its run history stays on the events. The schedule keeps recording events."),
  ).toBeInTheDocument();
});

test("the assignee's agent and Reply in Jira are offered only for patterns that can match Jira events", async () => {
  const user = userEvent.setup();
  let sent: Record<string, unknown> = {};
  api({
    "GET /api/event-handlers": { handlers: [] },
    ...SHEET_DATA,
    "POST /api/event-handlers": (body: unknown) => {
      sent = body as Record<string, unknown>;
      return { status: 201, body: { handler: handler() } };
    },
  });
  view();
  await user.click(await screen.findByRole("button", { name: "New handler" }));
  const sheet = await screen.findByRole("dialog");
  await user.type(within(sheet).getByLabelText("Name"), "Ask");
  await user.click(within(sheet).getByRole("combobox", { name: "Action" }));
  await user.click(screen.getByRole("option", { name: "Send to agent" }));
  await user.click(within(sheet).getByRole("combobox", { name: "Agent" }));
  await user.click(screen.getByRole("option", { name: "Agent mapped to the assignee" }));
  await user.click(within(sheet).getByRole("checkbox", { name: /Jira ticket/ }));
  expect(within(sheet).getByRole("checkbox", { name: "Give the ticket back after replying" })).toBeChecked();
  const pattern = within(sheet).getByLabelText("Event type");
  for (const jira of ["*", "jira.*", "jira.*.created"]) {
    await user.clear(pattern);
    await user.type(pattern, jira);
    expect(within(sheet).getByRole("checkbox", { name: /Jira ticket/ })).toBeInTheDocument();
  }
  await user.clear(pattern);
  await user.type(pattern, "schedule.fired");
  expect(within(sheet).queryByRole("checkbox", { name: /Jira ticket/ })).toBeNull();
  expect(within(sheet).queryByRole("checkbox", { name: "Give the ticket back after replying" })).toBeNull();
  await user.click(within(sheet).getByRole("combobox", { name: "Agent" }));
  expect(screen.queryByRole("option", { name: "Agent mapped to the assignee" })).toBeNull();
  await user.click(screen.getByRole("option", { name: "Tester" }));
  await user.click(within(sheet).getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect([sent.agent_from, sent.agent_id, sent.reply_to_jira, sent.give_back]).toEqual([
      "fixed",
      "tester",
      false,
      false,
    ]),
  );
});
