import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
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
  export_folder: "exports/jira",
};

/** The Users and agents section's requests, answered empty. */
const section = (url: string) =>
  url === "/api/jira/user-agents"
    ? new Response(JSON.stringify({ user_agents: [] }))
    : url === "/api/agents"
      ? new Response(JSON.stringify({ agents: [] }))
      : null;

test("the settings page shows Users and agents once a connection is saved", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => section(url) ?? new Response(JSON.stringify({ connection }))),
  );
  render(
    <MemoryRouter>
      <JiraSettingsView />
    </MemoryRouter>,
  );
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
  render(
    <MemoryRouter>
      <JiraSettingsView />
    </MemoryRouter>,
  );
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
  render(
    <MemoryRouter>
      <JiraSettingsView />
    </MemoryRouter>,
  );
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
  render(
    <MemoryRouter>
      <JiraSettingsView />
    </MemoryRouter>,
  );
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
  const fetch = vi.fn(
    async (url: string) =>
      new Response(JSON.stringify(url.startsWith("/api/rag/collections") ? { collections } : twoTickets)),
  );
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
    (fetch.mock.calls as unknown as [string, RequestInit][]).filter(
      ([url]) => !url.includes("/api/rag/") && url !== "/api/schedules",
    );
  await waitFor(() => expect(jiraCalls()).toHaveLength(2));
  expect(JSON.parse(jiraCalls()[1][1].body as string).jql).toBe("project = DEMO");
  await waitFor(() => expect(screen.getByLabelText("Filter saved tickets")).toBeEnabled());
  fireEvent.change(screen.getByLabelText("Filter saved tickets"), { target: { value: "payments" } });
  expect(screen.queryByText("Worker retries")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all filtered tickets" }));
  expect(screen.getByRole("button", { name: "Export selected (1)" })).toBeEnabled();
  expect(screen.getByRole("checkbox", { name: "Select DEMO-1" })).toBeChecked();
  fireEvent.change(screen.getByLabelText("Filter saved tickets"), { target: { value: "" } });
  expect(screen.getByRole("button", { name: "Export selected (0)" })).toBeDisabled();
});

const collections = [
  { id: "c1", name: "jira", writable: true },
  { id: "c2", name: "docs-code", writable: false },
];

test("export sends only the selected ticket and links to RAG collections", async () => {
  const fetch = vi.fn(
    async (url: string) =>
      new Response(
        JSON.stringify(
          url.endsWith("/export")
            ? { issue_key: "DEMO-1", path: "DEMO-1.md", status: "unchanged", hash: "hash", comments: 5 }
            : url.startsWith("/api/rag/collections")
              ? { collections: collections }
              : twoTickets,
        ),
      ),
  );
  vi.stubGlobal("fetch", fetch);
  render(
    <MemoryRouter>
      <JiraTicketsView />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByRole("checkbox", { name: "Select DEMO-1" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Export selected (1)" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Export selected (1)" }));
  expect(await screen.findByText("Export complete")).toBeInTheDocument();
  expect(screen.getByText("0 files saved, 1 unchanged in exports/jira. 5 comments included.")).toBeInTheDocument();
  expect(screen.getByText(/Add exports\/jira to a collection in/)).toBeInTheDocument();
  const call = fetch.mock.calls.find(([url]) => url.endsWith("/export")) as unknown as [string, RequestInit];
  expect(call[0]).toBe("/api/jira/tickets/ticket-1/export");
  expect(JSON.parse(call[1].body as string)).toEqual({ connection_revision: 2 });
  expect(screen.getByRole("link", { name: "RAG → Collections" })).toHaveAttribute("href", "/rag/collections");
  expect(fetch.mock.calls.some(([url]) => String(url).startsWith("/api/rag/"))).toBe(false);
  expect(fetch.mock.calls.some(([url]) => url.includes("ticket-2/export"))).toBe(false);
});

test("a failed ticket remains selected while successful exports are reported", async () => {
  const fetch = vi.fn(async (url: string) => {
    if (url.includes("ticket-1/export"))
      return new Response(JSON.stringify({ issue_key: "DEMO-1", status: "created", comments: 5 }));
    if (url.includes("ticket-2/export"))
      return new Response(JSON.stringify({ error: { message: "Jira rejected access" } }), { status: 502 });
    if (url.startsWith("/api/rag/collections")) return new Response(JSON.stringify({ collections }));
    return new Response(JSON.stringify(twoTickets));
  });
  vi.stubGlobal("fetch", fetch);
  render(
    <MemoryRouter>
      <JiraTicketsView />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByRole("checkbox", { name: "Select all filtered tickets" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Export selected (2)" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Export selected (2)" }));
  expect(await screen.findByText("Export finished with errors")).toBeInTheDocument();
  expect(screen.getByText("OPS-2: Jira rejected access")).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "Select OPS-2" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Select DEMO-1" })).not.toBeChecked();
  expect(screen.getByRole("button", { name: "Export selected (1)" })).toBeEnabled();
});

test("leaving the page aborts the current export and does not start another ticket", async () => {
  let activeSignal: AbortSignal | undefined;
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/export")) {
      activeSignal = init?.signal ?? undefined;
      return new Promise<Response>(() => {});
    }
    if (url.startsWith("/api/rag/collections")) return new Response(JSON.stringify({ collections }));
    return new Response(JSON.stringify(twoTickets));
  });
  vi.stubGlobal("fetch", fetch);
  const view = render(
    <MemoryRouter>
      <JiraTicketsView />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByRole("checkbox", { name: "Select all filtered tickets" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Export selected (2)" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Export selected (2)" }));
  expect(await screen.findByText(/Exporting 0 of 2/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Fetch tickets" })).toBeDisabled();
  view.unmount();
  expect(activeSignal?.aborted).toBe(true);
  expect(fetch.mock.calls.filter(([url]) => url.endsWith("/export"))).toHaveLength(1);
});

test("the export has no collection field", async () => {
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
  expect(screen.queryByLabelText("Target collection")).toBeNull();
  expect(screen.getByText(/Each selected ticket becomes exports\/jira\/<KEY>\.md/)).toBeInTheDocument();
  expect(screen.getByText(/Collections that hold exports\/jira index the tickets/)).toBeInTheDocument();
  expect(screen.queryByText(/rag-data/)).toBeNull();
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
