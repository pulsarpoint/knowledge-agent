import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { renderRoutes } from "~/lib/__tests__/dataRouter";
import JiraSettingsView from "~/features/jira/JiraSettingsView";
import JiraTicketsView from "~/features/jira/JiraTicketsView";
import type { JiraConnection, JiraTickets } from "~/features/jira/api";
import type { Schedule } from "~/features/events/schedules";

const connection: JiraConnection = {
  id: "jira-1",
  base_url: "https://jira.internal/jira",
  username: "integration-user",
  has_token: true,
  revision: 2,
  jql: "ORDER BY updated DESC, key DESC",
  last_tested_at: null,
  last_test_error: null,
  last_successful_sync_at: null,
  last_fetch_limit: null,
  last_fetch_total: null,
};
const snapshot: JiraTickets = {
  connection: {
    ...connection,
    last_successful_sync_at: "2026-09-09T00:00:00Z",
    last_fetch_limit: 10,
    last_fetch_total: 1,
  },
  tickets: [
    {
      id: "ticket-1",
      issue_key: "DEMO-1",
      jira_project_key: "DEMO",
      issue_type: "Task",
      labels: ["payments"],
      summary: "Check payment latency",
      priority: "High",
      status_name: "Open",
      assignee_name: null,
      source_updated_at: "2026-09-08T20:00:00Z",
    },
  ],
};

/** The Users and agents section's requests, answered empty. */
const section = (url: string) =>
  url === "/api/jira/user-agents"
    ? new Response(JSON.stringify({ user_agents: [] }))
    : url === "/api/agents"
      ? new Response(JSON.stringify({ agents: [] }))
      : null;

afterEach(() => vi.unstubAllGlobals());

/** The settings page in a data router (its leave guard needs one), with the tickets page to leave to. */
const showSettings = () =>
  renderRoutes(
    [
      { path: "/jira/settings", element: <JiraSettingsView /> },
      { path: "/jira/tickets", element: <p>Tickets page</p> },
    ],
    "/jira/settings",
  ).router;

test("leaving the settings with unsaved changes asks Stay / Leave; unchanged they leave at once", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => section(url) ?? new Response(JSON.stringify({ connection }))),
  );
  const router = showSettings();
  await screen.findByDisplayValue(connection.username);
  fireEvent.click(screen.getByRole("link", { name: "View tickets" }));
  expect(await screen.findByText("Tickets page")).toBeInTheDocument();
  expect(screen.queryByRole("alertdialog")).toBeNull();
  await router.navigate("/jira/settings");
  fireEvent.change(await screen.findByLabelText("Jira username"), { target: { value: "someone-else" } });
  fireEvent.click(screen.getByRole("link", { name: "View tickets" }));
  const dialog = within(await screen.findByRole("alertdialog"));
  fireEvent.click(dialog.getByRole("button", { name: "Stay" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  expect(router.state.location.pathname).toBe("/jira/settings");
  fireEvent.click(screen.getByRole("link", { name: "View tickets" }));
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Leave" }));
  expect(await screen.findByText("Tickets page")).toBeInTheDocument();
});

test("the settings page shows Users and agents once a connection is saved", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => section(url) ?? new Response(JSON.stringify({ connection }))),
  );
  showSettings();
  expect(await screen.findByText("Users and agents")).toBeInTheDocument();
  expect(await screen.findByText("No Jira users are mapped to agents yet.")).toBeInTheDocument();
});

test("save connection with token, clear password input, and test saved settings", async () => {
  let saved: JiraConnection | null = null;
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const answer = section(url);
    if (answer) return answer;
    if (init?.method === "PUT") saved = connection;
    if (url.endsWith("/test"))
      return new Response(
        JSON.stringify({ ok: true, user: { username: "integration-user", display_name: "Integration User" } }),
      );
    return new Response(JSON.stringify({ connection: saved }));
  });
  vi.stubGlobal("fetch", fetch);
  showSettings();
  fireEvent.change(await screen.findByLabelText("Jira server URL"), { target: { value: connection.base_url } });
  fireEvent.change(screen.getByLabelText("Jira username"), { target: { value: connection.username } });
  fireEvent.change(screen.getByLabelText("Personal access token"), { target: { value: "test-secret-token" } });
  expect(screen.getByRole("button", { name: "Test connection" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
  expect(await screen.findByText("Jira settings saved.")).toBeInTheDocument();
  expect(screen.getByLabelText("Personal access token")).toHaveValue("");
  const save = fetch.mock.calls.find(([, init]) => init?.method === "PUT")!;
  expect(JSON.parse(save[1]!.body as string)).toEqual({
    base_url: connection.base_url,
    username: connection.username,
    token: "test-secret-token",
  });
  fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
  expect(await screen.findByText(/Connected as Integration User/)).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: "Test connection" })).toBeEnabled());
});

test("blank token is omitted on edit and conflicts preserve input", async () => {
  const fetch = vi.fn(
    async (url: string, init?: RequestInit) =>
      section(url) ??
      new Response(
        JSON.stringify(
          init?.method === "PUT"
            ? { error: { message: "Jira settings changed. Reload settings before saving" } }
            : { connection },
        ),
        { status: init?.method === "PUT" ? 409 : 200 },
      ),
  );
  vi.stubGlobal("fetch", fetch);
  showSettings();
  fireEvent.change(await screen.findByLabelText("Jira username"), { target: { value: "new-user" } });
  fireEvent.submit(screen.getByRole("button", { name: "Save settings" }).closest("form")!);
  expect(await screen.findByText("Jira settings changed. Reload settings before saving")).toBeInTheDocument();
  const save = fetch.mock.calls.find(([, init]) => init?.method === "PUT")!;
  expect(JSON.parse(save[1]!.body as string)).toEqual({
    base_url: connection.base_url,
    username: "new-user",
    revision: 2,
  });
  expect(screen.getByLabelText("Jira username")).toHaveValue("new-user");
});

test("removing token sends explicit null", async () => {
  const fetch = vi.fn(
    async (url: string, init?: RequestInit) =>
      section(url) ??
      new Response(
        JSON.stringify({ connection: init?.method ? { ...connection, has_token: false, revision: 3 } : connection }),
      ),
  );
  vi.stubGlobal("fetch", fetch);
  showSettings();
  fireEvent.click(await screen.findByRole("checkbox", { name: "Remove saved token" }));
  fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
  await screen.findByText("Jira settings saved.");
  expect(JSON.parse(fetch.mock.calls.find(([, init]) => init?.method === "PUT")![1]!.body as string).token).toBeNull();
  expect(screen.getByRole("button", { name: "Test connection" })).toBeDisabled();
});

test("fetch uses selected ticket count and renders returned snapshot", async () => {
  const fetch = vi.fn(
    async (_url: string, init?: RequestInit) =>
      new Response(JSON.stringify(init?.method ? snapshot : { connection, tickets: [] })),
  );
  vi.stubGlobal("fetch", fetch);
  render(
    <MemoryRouter>
      <JiraTicketsView />
    </MemoryRouter>,
  );
  await screen.findByText("Ready to fetch tickets");
  fireEvent.change(screen.getByLabelText("Number of tickets"), { target: { value: "10" } });
  fireEvent.click(screen.getByRole("button", { name: "Fetch tickets" }));
  expect(await screen.findByText("Check payment latency")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "DEMO-1" })).toHaveAttribute(
    "href",
    "https://jira.internal/jira/browse/DEMO-1",
  );
  expect(screen.getByText("Unassigned")).toBeInTheDocument();
  const call = fetch.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(call[0]).toBe("/api/jira/tickets/fetch");
  expect(JSON.parse(call[1]!.body as string)).toEqual({ limit: 10, jql: connection.jql });
  await waitFor(() => expect(screen.getByRole("button", { name: "Fetch tickets" })).toBeEnabled());
});

test("failed fetch leaves previous results visible", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async (_url: string, init?: RequestInit) =>
        new Response(JSON.stringify(init?.method ? { error: { message: "Jira rejected access" } } : snapshot), {
          status: init?.method ? 502 : 200,
        }),
    ),
  );
  render(
    <MemoryRouter>
      <JiraTicketsView />
    </MemoryRouter>,
  );
  await screen.findByText("Check payment latency");
  fireEvent.click(screen.getByRole("button", { name: "Fetch tickets" }));
  expect(await screen.findByText(/Jira rejected access/)).toBeInTheDocument();
  expect(screen.getByText("Check payment latency")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: "Fetch tickets" })).toBeEnabled());
});

test("unconfigured Jira links to settings and disables network fetch", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ connection: null, tickets: [] }))),
  );
  render(
    <MemoryRouter>
      <JiraTicketsView />
    </MemoryRouter>,
  );
  expect(await screen.findByText("Connect your Jira server")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open Jira settings" })).toHaveAttribute("href", "/jira/settings");
  expect(screen.getByRole("button", { name: "Fetch tickets" })).toBeDisabled();
});

const twoTickets: JiraTickets = {
  ...snapshot,
  tickets: [
    ...snapshot.tickets,
    {
      ...snapshot.tickets[0],
      id: "ticket-2",
      issue_key: "OPS-2",
      jira_project_key: "OPS",
      summary: "Worker retries",
      status_name: "Done",
      labels: ["workers"],
    },
  ],
};

test("JQL is submitted and filtering the saved list limits bulk selection", async () => {
  const fetch = vi.fn(async (_url: string) => new Response(JSON.stringify(twoTickets)));
  vi.stubGlobal("fetch", fetch);
  render(
    <MemoryRouter>
      <JiraTicketsView />
    </MemoryRouter>,
  );
  await screen.findByText("Worker retries");
  fireEvent.change(screen.getByLabelText("Jira query (JQL)"), { target: { value: "project = DEMO" } });
  fireEvent.click(screen.getByRole("button", { name: "Fetch tickets" }));
  const jiraCalls = () =>
    (fetch.mock.calls as unknown as [string, RequestInit][]).filter(([url]) => url !== "/api/schedules");
  await waitFor(() => expect(jiraCalls()).toHaveLength(2));
  expect(JSON.parse(jiraCalls()[1][1].body as string).jql).toBe("project = DEMO");
  await waitFor(() => expect(screen.getByLabelText("Filter saved tickets")).toBeEnabled());
  fireEvent.change(screen.getByLabelText("Filter saved tickets"), { target: { value: "payments" } });
  expect(screen.queryByText("Worker retries")).not.toBeInTheDocument();
  expect(screen.getByText("Showing 1 of 2 saved tickets", { exact: false })).toBeInTheDocument();
});

test("the saved tickets can be listed and filtered but not exported", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(twoTickets))),
  );
  render(
    <MemoryRouter>
      <JiraTicketsView />
    </MemoryRouter>,
  );
  await screen.findByText("Worker retries");
  expect(screen.queryByRole("button", { name: /Export/ })).toBeNull();
  expect(screen.queryByRole("checkbox")).toBeNull();
});

const pullSchedule: Schedule = {
  id: "s-1",
  name: "Jira auto pull",
  target_kind: "jira.pull",
  target_id: "jira-1",
  cron: "*/15 * * * *",
  run_at: null,
  description: "Every 15 minutes",
  timezone: "UTC",
  enabled: true,
  next_run_at: "2026-10-09T10:15:00Z",
  running: false,
  last_run_at: "2026-10-09T10:00:00Z",
  last_finished_at: "2026-10-09T10:00:02Z",
  last_status: "succeeded",
  last_message: "Pulled 10 of 12 tickets.",
  revision: 1,
  params: {},
};

function ticketsApi(schedules: unknown) {
  const fetch = vi.fn(async (url: string) => {
    if (url === "/api/schedules") return new Response(JSON.stringify(schedules));
    return new Response(JSON.stringify(snapshot));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

test("tickets show the auto pull status and link to the Schedules page", async () => {
  const fetch = ticketsApi({ schedules: [pullSchedule] });
  render(
    <MemoryRouter>
      <JiraTicketsView />
    </MemoryRouter>,
  );
  expect(await screen.findByText(/Auto pull: Every 15 minutes/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Edit in Schedules" })).toHaveAttribute("href", "/schedules");
  expect(fetch.mock.calls.some(([url]) => url === "/api/jira/auto-pull")).toBe(false);
});

test("tickets say when auto pull is off", async () => {
  for (const schedules of [[], [{ ...pullSchedule, enabled: false }]]) {
    ticketsApi({ schedules });
    const { unmount } = render(
      <MemoryRouter>
        <JiraTicketsView />
      </MemoryRouter>,
    );
    expect(await screen.findByText(/Auto pull is off/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Edit in Schedules" })).toHaveAttribute("href", "/schedules");
    unmount();
  }
});

test("auto pull status stays hidden on an unexpected response", async () => {
  ticketsApi({ connection, tickets: [] });
  render(
    <MemoryRouter>
      <JiraTicketsView />
    </MemoryRouter>,
  );
  await screen.findByText("Check payment latency");
  expect(screen.queryByText(/Auto pull/)).toBeNull();
});
