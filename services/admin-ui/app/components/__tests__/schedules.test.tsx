import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import SchedulesView from "~/features/events/SchedulesView";
import type { Schedule } from "~/features/events/schedules";

const schedule: Schedule = {
  id: "s-1",
  name: "Jira auto pull",
  target_kind: "jira.pull",
  target_id: "jira-1",
  cron: "*/15 * * * *",
  description: "Every 15 minutes",
  timezone: "Europe/Belgrade",
  enabled: true,
  next_run_at: "2026-10-09T10:15:00Z",
  running: false,
  last_run_at: "2026-10-09T10:00:00Z",
  last_finished_at: "2026-10-09T10:00:02Z",
  last_status: "succeeded",
  last_message: "Pulled 25 of 140 tickets.",
  revision: 3,
  params: {},
};

const emit = (extra: Partial<Schedule> = {}): Schedule => ({
  ...schedule,
  id: "e-1",
  name: "Morning summary",
  target_kind: "event.emit",
  target_id: "e-1",
  cron: "0 6 * * *",
  description: "Every day at 06:00",
  last_message: "Recorded event 41.",
  revision: 1,
  handlers: [],
  ...extra,
});
const AGENTS = { agents: [{ id: "tester", name: "Tester" }] };
const SHEET = {
  "GET /api/event-handlers/agents": () => [200, AGENTS] as [number, unknown],
  "GET /api/events/facets": () => [200, { sources: [], types: [], statuses: [], handlers: [] }] as [number, unknown],
  "GET /api/events": () => [200, { events: [], next_offset: null }] as [number, unknown],
};

function api(routes: Record<string, (init?: RequestInit) => [number, unknown]>) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    const handler = routes[key];
    if (!handler) throw new Error(`unexpected ${key}`);
    const [status, body] = handler(init);
    return new Response(status === 204 ? null : JSON.stringify(body), { status });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const body = (fetch: ReturnType<typeof api>, key: string) =>
  JSON.parse(fetch.mock.calls.find(([url, init]) => `${init?.method ?? "GET"} ${url}` === key)![1]!.body as string);
const view = () =>
  render(
    <MemoryRouter>
      <SchedulesView />
    </MemoryRouter>,
  );

afterEach(() => vi.unstubAllGlobals());

test("lists schedules with their timetable and last result", async () => {
  api({ "GET /api/schedules": () => [200, { schedules: [schedule] }] });
  view();
  const row = (await screen.findByText("Jira auto pull")).closest("tr")!;
  expect(within(row).getByText("Jira tickets")).toBeInTheDocument();
  expect(within(row).getByText("Every 15 minutes")).toBeInTheDocument();
  expect(within(row).getByText("Europe/Belgrade")).toBeInTheDocument();
  expect(within(row).getByText("Succeeded")).toBeInTheDocument();
  expect(within(row).getByText("Pulled 25 of 140 tickets.")).toBeInTheDocument();
});

test("shows the empty state", async () => {
  api({ "GET /api/schedules": () => [200, { schedules: [] }] });
  view();
  expect(await screen.findByText("No schedules yet. Create one with New schedule.")).toBeInTheDocument();
});

test("new schedule creates a Jira pull", async () => {
  const user = userEvent.setup();
  const fetch = api({
    "GET /api/schedules": () => [200, { schedules: [] }],
    "POST /api/schedules/preview": () => [200, { runs: ["2026-10-12T05:00:00Z"] }],
    "POST /api/schedules": () => [201, { schedule }],
  });
  view();
  await user.click(await screen.findByRole("button", { name: "New schedule" }));
  expect(screen.getByRole("combobox", { name: "What to run" })).toHaveTextContent("Jira tickets");
  await screen.findByText("Next runs");
  await user.click(screen.getByRole("button", { name: "Create" }));
  expect(await screen.findByText("Jira auto pull")).toBeInTheDocument();
  expect(body(fetch, "POST /api/schedules")).toEqual({
    target_kind: "jira.pull",
    cron: "*/15 * * * *",
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    enabled: true,
    params: {},
  });
});

test("new Jira schedule saves its own query and number of tickets", async () => {
  const user = userEvent.setup();
  const fetch = api({
    "GET /api/schedules": () => [200, { schedules: [] }],
    "POST /api/schedules/preview": () => [200, { runs: ["2026-10-12T05:00:00Z"] }],
    "POST /api/schedules": () => [201, { schedule }],
  });
  view();
  await user.click(await screen.findByRole("button", { name: "New schedule" }));
  const jql = screen.getByLabelText("Jira query (JQL)");
  expect(jql).toHaveValue("");
  expect(screen.getByText(/Leave empty to use the query of the last fetch on Jira → Tickets/)).toBeInTheDocument();
  await user.type(jql, "  project = AUTO ");
  await user.type(screen.getByLabelText("Number of tickets"), "40");
  await screen.findByText("Next runs");
  await user.click(screen.getByRole("button", { name: "Create" }));
  await waitFor(() => expect(body(fetch, "POST /api/schedules").params).toEqual({ jql: "project = AUTO", limit: 40 }));
});

test("a taken Jira pull leaves Emit event, which can have many schedules", async () => {
  const user = userEvent.setup();
  api({
    "GET /api/schedules": () => [200, { schedules: [schedule, emit()] }],
    "POST /api/schedules/preview": () => [200, { runs: ["2026-10-12T05:00:00Z"] }],
  });
  view();
  await user.click(await screen.findByRole("button", { name: "New schedule" }));
  expect(screen.getByRole("combobox", { name: "What to run" })).toHaveTextContent("Emit event");
});

test("delete asks first and removes the row", async () => {
  const user = userEvent.setup();
  const fetch = api({
    "GET /api/schedules": () => [200, { schedules: [schedule] }],
    "DELETE /api/schedules/s-1": () => [204, null],
  });
  view();
  await user.click(await screen.findByRole("button", { name: "Delete" }));
  expect(screen.getByText("Delete schedule Jira auto pull?")).toBeInTheDocument();
  expect(screen.getByText("Its history is deleted too.")).toBeInTheDocument();
  await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete" }));
  expect(await screen.findByText("No schedules yet. Create one with New schedule.")).toBeInTheDocument();
  expect(fetch.mock.calls.some(([url, init]) => url === "/api/schedules/s-1" && init?.method === "DELETE")).toBe(true);
});

test("a double click on Delete sends one request and keeps the title", async () => {
  const user = userEvent.setup();
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "DELETE") {
      await gate;
      return new Response(null, { status: 204 });
    }
    return new Response(JSON.stringify({ schedules: [schedule] }), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  view();
  await user.click(await screen.findByRole("button", { name: "Delete" }));
  const confirm = within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete" });
  await user.dblClick(confirm);
  expect(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" })).toBeDisabled();
  release();
  expect(await screen.findByText("No schedules yet. Create one with New schedule.")).toBeInTheDocument();
  expect(fetch.mock.calls.filter(([, init]) => init?.method === "DELETE")).toHaveLength(1);
  expect(screen.queryByText("Delete schedule ?")).not.toBeInTheDocument();
});

test("pausing sends the revision and shows the paused row", async () => {
  const user = userEvent.setup();
  const fetch = api({
    "GET /api/schedules": () => [200, { schedules: [schedule] }],
    "PUT /api/schedules/s-1": () => [
      200,
      { schedule: { ...schedule, enabled: false, next_run_at: null, revision: 4 } },
    ],
  });
  view();
  await user.click(await screen.findByRole("switch", { name: "Enabled: Jira auto pull" }));
  expect(await screen.findByText("Paused")).toBeInTheDocument();
  expect(body(fetch, "PUT /api/schedules/s-1")).toEqual({
    cron: "*/15 * * * *",
    timezone: "Europe/Belgrade",
    enabled: false,
    revision: 3,
  });
});

test("run now while running shows the server message", async () => {
  const user = userEvent.setup();
  api({
    "GET /api/schedules": () => [200, { schedules: [schedule] }],
    "POST /api/schedules/s-1/run": () => [409, { error: { message: "This schedule is already running." } }],
  });
  view();
  await user.click(await screen.findByRole("button", { name: "Run now" }));
  expect(await screen.findByText("This schedule is already running.")).toBeInTheDocument();
});

test("history lists the newest runs", async () => {
  const user = userEvent.setup();
  api({
    "GET /api/schedules": () => [200, { schedules: [schedule] }],
    "GET /api/schedules/s-1/runs": () => [
      200,
      {
        runs: [
          {
            id: 2,
            trigger: "manual",
            started_at: "2026-10-09T10:00:00Z",
            finished_at: "2026-10-09T10:00:02Z",
            status: "failed",
            message: "Could not reach Jira.",
          },
        ],
      },
    ],
  });
  view();
  await user.click(await screen.findByRole("button", { name: "History" }));
  expect(await screen.findByText(/Run now · Failed · Could not reach Jira\./)).toBeInTheDocument();
});

test("editor shows and saves the Jira query and number of tickets", async () => {
  const user = userEvent.setup();
  const stored = { ...schedule, params: { jql: "project = DEMO", limit: 30 } };
  const fetch = api({
    "GET /api/schedules": () => [200, { schedules: [stored] }],
    "POST /api/schedules/preview": () => [200, { runs: ["2026-10-12T05:00:00Z"] }],
    "PUT /api/schedules/s-1": () => [200, { schedule: { ...stored, revision: 4 } }],
  });
  view();
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  const jql = screen.getByLabelText("Jira query (JQL)");
  const limit = screen.getByLabelText("Number of tickets");
  expect(jql).toHaveValue("project = DEMO");
  expect(limit).toHaveValue(30);
  await user.clear(jql);
  await user.type(jql, "project = AUTO");
  await user.clear(limit);
  await user.type(limit, "50");
  await screen.findByText("Next runs");
  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(body(fetch, "PUT /api/schedules/s-1").params).toEqual({ jql: "project = AUTO", limit: 50 }),
  );
});

test("editor saves empty query and number of tickets as the fallback", async () => {
  const user = userEvent.setup();
  const stored = { ...schedule, params: { jql: "project = DEMO", limit: 30 } };
  const fetch = api({
    "GET /api/schedules": () => [200, { schedules: [stored] }],
    "POST /api/schedules/preview": () => [200, { runs: ["2026-10-12T05:00:00Z"] }],
    "PUT /api/schedules/s-1": () => [200, { schedule: { ...stored, params: {}, revision: 4 } }],
  });
  view();
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  await user.clear(screen.getByLabelText("Jira query (JQL)"));
  await user.clear(screen.getByLabelText("Number of tickets"));
  expect(screen.getByText(/Leave empty to use the query of the last fetch on Jira → Tickets/)).toBeInTheDocument();
  await screen.findByText("Next runs");
  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(body(fetch, "PUT /api/schedules/s-1").params).toEqual({}));
});

test("editor previews a custom cron, shows errors under the field and saves", async () => {
  const user = userEvent.setup();
  const fetch = api({
    "GET /api/schedules": () => [200, { schedules: [schedule] }],
    "POST /api/schedules/preview": (init) =>
      JSON.parse(init!.body as string).cron === "*/2 * * * *"
        ? [400, { error: { message: "Schedules may run at most every 5 minutes." } }]
        : [200, { runs: ["2026-10-12T05:00:00Z", "2026-10-13T05:00:00Z", "2026-10-14T05:00:00Z"] }],
    "PUT /api/schedules/s-1": () => [
      200,
      { schedule: { ...schedule, cron: "0 7 * * 1-5", description: "0 7 * * 1-5", revision: 4 } },
    ],
  });
  view();
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  await user.click(screen.getByRole("combobox", { name: "How often" }));
  await user.click(screen.getByRole("option", { name: "Custom" }));
  const cron = screen.getByLabelText("Cron expression");
  await user.clear(cron);
  await user.type(cron, "*/2 * * * *");
  expect(await screen.findByText("Schedules may run at most every 5 minutes.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  await user.clear(cron);
  await user.type(cron, "0 7 * * 1-5");
  expect(await screen.findByText("Next runs")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(body(fetch, "PUT /api/schedules/s-1")).toEqual({
      cron: "0 7 * * 1-5",
      timezone: "Europe/Belgrade",
      enabled: true,
      revision: 3,
      params: {},
    }),
  );
  expect(await screen.findByText("0 7 * * 1-5")).toBeInTheDocument();
});

test("a successful refresh clears an earlier refresh error", async () => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  try {
    let calls = 0;
    api({
      "GET /api/schedules": () =>
        ++calls === 2 ? [500, { error: { message: "Backend down." } }] : [200, { schedules: [schedule] }],
    });
    view();
    await screen.findByText("Jira auto pull");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(await screen.findByText("Backend down.")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    await waitFor(() => expect(screen.queryByText("Backend down.")).not.toBeInTheDocument());
  } finally {
    vi.useRealTimers();
  }
});

test("a slow refresh that resolves after a pause does not revert the row", async () => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  try {
    let release: () => void = () => {};
    let calls = 0;
    const fetch = vi.fn(async (url: string, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${url}`;
      if (key === "GET /api/schedules") {
        if (++calls === 1) return new Response(JSON.stringify({ schedules: [schedule] }));
        await new Promise<void>((resolve) => {
          release = resolve;
        });
        return new Response(JSON.stringify({ schedules: [schedule] }));
      }
      if (key === "PUT /api/schedules/s-1")
        return new Response(
          JSON.stringify({ schedule: { ...schedule, enabled: false, next_run_at: null, revision: 4 } }),
        );
      throw new Error(`unexpected ${key}`);
    });
    vi.stubGlobal("fetch", fetch);
    view();
    const toggle = await screen.findByRole("switch", { name: "Enabled: Jira auto pull" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    await userEvent.setup({ advanceTimers: (ms) => vi.advanceTimersByTimeAsync(ms) }).click(toggle);
    expect(await screen.findByText("Paused")).toBeInTheDocument();
    await act(async () => {
      release();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText("Paused")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Enabled: Jira auto pull" })).not.toBeChecked();
  } finally {
    vi.useRealTimers();
  }
});

test("new schedule with Emit event sends its name and timing only", async () => {
  const user = userEvent.setup();
  const fetch = api({
    "GET /api/schedules": () => [200, { schedules: [] }],
    "POST /api/schedules/preview": () => [200, { runs: ["2026-10-12T05:00:00Z"] }],
    "POST /api/schedules": () => [201, { schedule: { ...emit(), handlers: undefined } }],
  });
  view();
  await user.click(await screen.findByRole("button", { name: "New schedule" }));
  await user.click(screen.getByRole("combobox", { name: "What to run" }));
  await user.click(screen.getByRole("option", { name: "Emit event" }));
  expect(screen.queryByLabelText("Jira query (JQL)")).toBeNull();
  const create = screen.getByRole("button", { name: "Create" });
  await screen.findByText("Next runs");
  expect(create).toBeDisabled();
  await user.type(screen.getByLabelText("Name"), "  Morning summary ");
  await user.click(create);
  const row = (await screen.findByText("Morning summary")).closest("tr")!;
  expect(within(row).getByText("Emit event")).toBeInTheDocument();
  expect(within(row).getByText("No handler runs this schedule.")).toBeInTheDocument();
  expect(body(fetch, "POST /api/schedules")).toEqual({
    target_kind: "event.emit",
    name: "Morning summary",
    cron: "*/15 * * * *",
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    enabled: true,
  });
});

test("an emit schedule shows the handlers that run it", async () => {
  api({
    "GET /api/schedules": () => [
      200,
      {
        schedules: [
          emit(),
          emit({ id: "e-2", name: "Nightly check", handlers: [{ id: "h-1", name: "Summary", enabled: true }] }),
          emit({
            id: "e-3",
            name: "Weekly report",
            handlers: [
              { id: "h-2", name: "A", enabled: true },
              { id: "h-3", name: "B", enabled: false },
              { id: "h-4", name: "C", enabled: true },
            ],
          }),
          schedule,
        ],
      },
    ],
  });
  view();
  const none = (await screen.findByText("Morning summary")).closest("tr")!;
  expect(within(none).getByText("No handler runs this schedule.")).toBeInTheDocument();
  expect(within(none).getByRole("button", { name: "Add handler" })).toBeInTheDocument();
  const one = screen.getByText("Nightly check").closest("tr")!;
  expect(within(one).getByRole("link", { name: "Handled by: Summary" })).toHaveAttribute(
    "href",
    "/events/handlers?handler=h-1",
  );
  expect(within(one).queryByRole("button", { name: "Add handler" })).toBeNull();
  const several = screen.getByText("Weekly report").closest("tr")!;
  expect(within(several).getByRole("link", { name: "Handled by: 3 handlers" })).toHaveAttribute(
    "href",
    "/events/handlers",
  );
  const jira = screen.getByText("Jira auto pull").closest("tr")!;
  expect(within(jira).queryByText(/Handled by|No handler/)).toBeNull();
});

test("Add handler opens the handler sheet prefilled for the schedule and saves it", async () => {
  const user = userEvent.setup();
  let listed = [emit()];
  const fetch = api({
    "GET /api/schedules": () => [200, { schedules: listed }],
    ...SHEET,
    "POST /api/event-handlers": () => {
      listed = [emit({ handlers: [{ id: "h-9", name: "Morning summary", enabled: true }] })];
      return [201, { handler: { id: "h-9" } }];
    },
  });
  view();
  await user.click(await screen.findByRole("button", { name: "Add handler" }));
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByLabelText("Name")).toHaveValue("Morning summary");
  expect(within(sheet).getByLabelText("Event type")).toHaveValue("schedule.fired");
  expect(within(sheet).getByLabelText("Condition 1 field")).toHaveValue("payload.schedule_id");
  expect(within(sheet).getByLabelText("Condition 1 value")).toHaveValue("e-1");
  expect(within(sheet).getByRole("combobox", { name: "Action" })).toHaveTextContent("Send to agent");
  await user.click(within(sheet).getByRole("combobox", { name: "Agent" }));
  await user.click(await screen.findByRole("option", { name: "Tester" }));
  await user.click(within(sheet).getByLabelText("Prompt"));
  await user.paste("Summarise yesterday.");
  await user.click(within(sheet).getByRole("button", { name: "Save" }));
  expect(await screen.findByRole("link", { name: "Handled by: Morning summary" })).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(body(fetch, "POST /api/event-handlers")).toEqual({
    name: "Morning summary",
    enabled: true,
    type_pattern: "schedule.fired",
    conditions: [{ field: "payload.schedule_id", op: "equals", value: "e-1" }],
    action: "agent",
    emit: null,
    agent_from: "fixed",
    agent_id: "tester",
    prompt: "Summarise yesterday.",
    reply_to_jira: false,
    give_back: false,
  });
});

test("deleting an emit schedule says its handlers stay", async () => {
  const user = userEvent.setup();
  api({ "GET /api/schedules": () => [200, { schedules: [emit()] }] });
  view();
  await user.click(await screen.findByRole("button", { name: "Delete" }));
  expect(
    screen.getByText("Its history is deleted too. Its handlers stay; they no longer match any event."),
  ).toBeInTheDocument();
});

test("editing an emit schedule saves its name; edits keep the handlers line", async () => {
  const user = userEvent.setup();
  const bound = emit({ handlers: [{ id: "h-1", name: "Summary", enabled: true }] });
  const fetch = api({
    "GET /api/schedules": () => [200, { schedules: [bound] }],
    "POST /api/schedules/preview": () => [200, { runs: ["2026-10-12T05:00:00Z"] }],
    "PUT /api/schedules/e-1": () => [
      200,
      { schedule: { ...bound, name: "Daily digest", revision: 2, handlers: undefined } },
    ],
  });
  view();
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  const name = screen.getByLabelText("Name");
  expect(name).toHaveValue("Morning summary");
  await user.clear(name);
  await user.type(name, "Daily digest");
  await screen.findByText("Next runs");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Daily digest")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Handled by: Summary" })).toBeInTheDocument();
  expect(body(fetch, "PUT /api/schedules/e-1")).toEqual({
    name: "Daily digest",
    cron: "0 6 * * *",
    timezone: "Europe/Belgrade",
    enabled: true,
    revision: 1,
  });
});
