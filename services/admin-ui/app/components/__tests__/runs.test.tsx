import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import RunsView from "~/features/sessions/RunsView";
import RunRecordView from "~/features/sessions/RunRecordView";

afterEach(() => vi.unstubAllGlobals());
/** Picks `option` in the toolbar Select named `label`. */
async function choose(label: string, option: string) {
  await userEvent.click(screen.getByRole("combobox", { name: label }));
  await userEvent.click(await screen.findByRole("option", { name: option }));
}
/** The options of the toolbar Select named `label` (it is opened, then closed). */
async function optionsOf(label: string) {
  await userEvent.click(screen.getByRole("combobox", { name: label }));
  const names = screen.getAllByRole("option").map((option) => option.textContent);
  await userEvent.keyboard("{Escape}");
  return names;
}
const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

const runs = {
  total: 1,
  page: 1,
  page_size: 50,
  runs: [
    {
      run_key: "run:1",
      agent_id: "search",
      agent: "Search agent",
      kind: "test",
      title: "How many retries?",
      user: "alice@example.test",
      status: "completed",
      total_tokens: 15,
      started_at: "2026-10-04T10:00:00Z",
      tool_counts: [
        { name: "read_file", calls: 31 },
        { name: "code_graph", calls: 12 },
        { name: "shell_run", calls: 4 },
        { name: "ssh_run", calls: 1 },
      ],
    },
  ],
};

test("runs list shows each run and filters by kind", async () => {
  const fetchMock = vi.fn(async (_url: string) => json(runs));
  vi.stubGlobal("fetch", fetchMock);
  render(
    <MemoryRouter>
      <RunsView />
    </MemoryRouter>,
  );
  expect(await screen.findByText("How many retries?")).toBeInTheDocument();
  expect(screen.getByText("alice@example.test")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "How many retries?" })).toHaveAttribute("href", "/activity/runs/run%3A1");
  await choose("Run type", "Native run");
  await waitFor(() => expect(fetchMock.mock.calls.at(-1)?.[0]).toContain("kind=native_session"));
});

test("runs list and record show the tools each run used", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url.startsWith("/api/agent-runs/")
        ? json({
            run_key: "run:1",
            agent_id: "search",
            user: null,
            tool_counts: [
              { name: "code_graph", calls: 12 },
              { name: "read_file", calls: 31 },
            ],
            events: [
              {
                seq: 1,
                kind: "start",
                agent_id: "search",
                payload: { kind: "test", title: "Graph run" },
                created_at: "2026-10-04T10:00:00Z",
              },
            ],
          })
        : json({
            total: 2,
            runs: [runs.runs[0], { ...runs.runs[0], run_key: "run:2", title: "Quiet run", tool_counts: [] }],
          }),
    ),
  );
  render(
    <MemoryRouter>
      <RunsView />
    </MemoryRouter>,
  );
  expect(await screen.findByRole("columnheader", { name: "Tools" })).toBeVisible();
  const busy = screen.getByText("How many retries?").closest("tr")!;
  const cell = within(busy).getByText("read_file 31 · code_graph 12 · shell_run 4 +1");
  expect(cell).toHaveAttribute("title", "read_file 31 · code_graph 12 · shell_run 4 · ssh_run 1");
  expect(within(screen.getByText("Quiet run").closest("tr")!).getByText("No tools used")).toBeVisible();
  render(
    <MemoryRouter>
      <RunRecordView runKey="run:1" />
    </MemoryRouter>,
  );
  expect(await screen.findByLabelText("Tools used")).toHaveTextContent("code_graph 12 · read_file 31");
});

test("a run record without tool counts has no tools line; an empty list says No tools used", async () => {
  const record = (counts?: unknown[]) =>
    json({
      run_key: "run:1",
      agent_id: "search",
      user: null,
      events: [
        {
          seq: 1,
          kind: "start",
          agent_id: "search",
          payload: { kind: "test", title: "Graph run" },
          created_at: "2026-10-04T10:00:00Z",
        },
      ],
      ...(counts ? { tool_counts: counts } : {}),
    });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => record()),
  );
  const { unmount } = render(
    <MemoryRouter>
      <RunRecordView runKey="run:1" />
    </MemoryRouter>,
  );
  expect(await screen.findByText("Graph run")).toBeVisible();
  expect(screen.queryByLabelText("Tools used")).toBeNull();
  unmount();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => record([])),
  );
  render(
    <MemoryRouter>
      <RunRecordView runKey="run:1" />
    </MemoryRouter>,
  );
  expect(await screen.findByLabelText("Tools used")).toHaveTextContent("No tools used");
});

test("run types have readable names in the filter and the list", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string) =>
      json({
        total: 4,
        runs: [
          { ...runs.runs[0], run_key: "cc:s1:1", kind: "native_session", title: "Payments API reference" },
          { ...runs.runs[0], run_key: "cc:s3:1", kind: "review_session", title: "Review: Check disk" },
          { ...runs.runs[0], run_key: "cc:s1:2", kind: "retired_kind", title: "Old session" },
          { ...runs.runs[0], run_key: "cc:s2:1", kind: "claude_code", title: "Claude Code session" },
        ],
      }),
    ),
  );
  render(
    <MemoryRouter>
      <RunsView />
    </MemoryRouter>,
  );
  const row = (await screen.findByText("Payments API reference")).closest("tr")!;
  expect(within(row).getByText("Native run")).toBeInTheDocument();
  expect(
    // a review is labelled with the reviewed agent (its id while the agents are unknown)
    within((await screen.findByText("Review: Check disk")).closest("tr")!).getByText("Review of search"),
  ).toBeInTheDocument();
  // a run kind without a name shows as is
  expect(within((await screen.findByText("Old session")).closest("tr")!).getByText("retired_kind")).toBeInTheDocument();
  // a retired kind keeps its label in rows but is not offered in the filter
  expect(
    within((await screen.findByText("Claude Code session")).closest("tr")!).getByText("Claude Code"),
  ).toBeInTheDocument();
  expect(await optionsOf("Run type")).toEqual([
    "All run types",
    "Agent test",
    "Event run",
    "Chat",
    "Event task",
    "Native run",
    "Session review",
  ]);
});

test("runs list passes a key prefix from the URL", async () => {
  const fetchMock = vi.fn(async (_url: string) => json(runs));
  vi.stubGlobal("fetch", fetchMock);
  render(
    <MemoryRouter initialEntries={["/activity/runs?key_prefix=cc%3As1%3A"]}>
      <RunsView />
    </MemoryRouter>,
  );
  await screen.findByText("How many retries?");
  expect(runCalls(fetchMock).at(-1)).toContain("key_prefix=cc%3As1%3A");
});

test("run record shows the full timeline", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      json({
        run_key: "run:1",
        agent_id: "search",
        user: "alice@example.test",
        events: [
          {
            seq: 1,
            kind: "start",
            agent_id: "search",
            payload: { kind: "test", title: "How many retries?", agent: "Search agent" },
            created_at: "2026-10-04T10:00:00Z",
          },
          {
            seq: 2,
            kind: "instructions",
            agent_id: "search",
            payload: { agent: "Search agent", text: "SYSTEM PROMPT TEXT" },
            created_at: "2026-10-04T10:00:00Z",
          },
          {
            seq: 3,
            kind: "reasoning",
            agent_id: "search",
            payload: { agent: "Search agent", available: false },
            created_at: "2026-10-04T10:00:01Z",
          },
          {
            seq: 4,
            kind: "tool_call",
            agent_id: "search",
            payload: { agent: "Search agent", name: "search_documents", arguments: '{"query":"retries"}' },
            created_at: "2026-10-04T10:00:01Z",
          },
          {
            seq: 5,
            kind: "tool_result",
            agent_id: "search",
            payload: { agent: "Search agent", name: "search_documents", output: "LONG OUTPUT " + "x".repeat(5000) },
            created_at: "2026-10-04T10:00:02Z",
          },
          {
            seq: 6,
            kind: "message",
            agent_id: "search",
            payload: { agent: "Search agent", text: "Three retries [1]." },
            created_at: "2026-10-04T10:00:03Z",
          },
          {
            seq: 7,
            kind: "usage",
            agent_id: "search",
            payload: { agent: "Search agent", input_tokens: 10, output_tokens: 5, total_tokens: 15 },
            created_at: "2026-10-04T10:00:03Z",
          },
          {
            seq: 8,
            kind: "end",
            agent_id: "search",
            payload: { status: "completed" },
            created_at: "2026-10-04T10:00:03Z",
          },
        ],
      }),
    ),
  );
  render(
    <MemoryRouter>
      <RunRecordView runKey="run:1" />
    </MemoryRouter>,
  );
  expect(await screen.findByRole("heading", { name: "How many retries?" })).toBeInTheDocument();
  expect(screen.getByText("completed")).toBeInTheDocument();
  expect(screen.getByText("15 tokens")).toBeInTheDocument();
  expect(screen.getByText("SYSTEM PROMPT TEXT")).not.toBeVisible();
  expect(screen.getByText("Reasoning not returned by the provider.")).toBeInTheDocument();
  expect(screen.getByText('{"query":"retries"}')).toBeInTheDocument();
  expect(screen.getByText(/LONG OUTPUT x{5000}/)).toBeInTheDocument();
  expect(screen.getByText("Three retries [1].")).toBeInTheDocument();
});

test("an expert call row says who called it and opens only that call", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      json({
        total: 1,
        runs: [
          {
            ...runs.runs[0],
            kind: "expert_call",
            run_key: "run:9",
            id: "run:9:e1",
            call_id: "e1",
            caller: "Lead",
            title: "Map payments",
            status: "completed",
          },
        ],
      }),
    ),
  );
  render(
    <MemoryRouter>
      <RunsView />
    </MemoryRouter>,
  );
  expect(await screen.findByText("Called as expert by Lead")).toBeVisible();
  expect(screen.getByRole("link", { name: "Map payments" })).toHaveAttribute("href", "/activity/runs/run%3A9?call=e1");
});

test("the record asks the API for one expert call when the link carries call", async () => {
  const fetchMock = vi.fn(async (_url: string) => json({ run_key: "run:9", agent_id: "p", user: null, events: [] }));
  vi.stubGlobal("fetch", fetchMock);
  render(
    <MemoryRouter initialEntries={["/activity/runs/run%3A9?call=e1"]}>
      <RunRecordView runKey="run:9" />
    </MemoryRouter>,
  );
  await waitFor(() => expect(fetchMock.mock.calls[0][0]).toBe("/api/agent-runs/run%3A9?call=e1"));
});

const runCalls = (fetchMock: { mock: { calls: unknown[][] } }) =>
  fetchMock.mock.calls.map((call) => String(call[0])).filter((url) => url.startsWith("/api/agent-runs"));
function Where() {
  const location = useLocation();
  return <output aria-label="Location">{location.search}</output>;
}
function listing(total: number) {
  const fetchMock = vi.fn(async (url: string) =>
    url === "/api/agents"
      ? json({
          agents: [
            { id: "search", name: "Search agent" },
            { id: "payments", name: "Payments" },
          ],
        })
      : json({ ...runs, total }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
const renderRuns = (search = "") =>
  render(
    <MemoryRouter initialEntries={[`/activity/runs${search}`]}>
      <RunsView />
      <Where />
    </MemoryRouter>,
  );
const where = () => screen.getByLabelText("Location").textContent;

test("filters go into the URL and the request", async () => {
  const fetchMock = listing(1);
  renderRuns();
  await screen.findByText("How many retries?");
  expect(runCalls(fetchMock).at(-1)).toBe("/api/agent-runs");
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url === "/api/agents")).toBe(true));
  await choose("Agent", "Payments");
  await choose("Status", "Working");
  const user = screen.getByRole("searchbox", { name: "User" });
  fireEvent.change(user, { target: { value: " Alice " } });
  fireEvent.keyDown(user, { key: "Enter" });
  await screen.findByText("How many retries?");
  await choose("Rows per page", "20");
  await waitFor(() => expect(where()).toBe("?agent_id=payments&status=working&user=Alice&page_size=20"));
  await waitFor(() =>
    expect(runCalls(fetchMock).at(-1)).toBe("/api/agent-runs?agent_id=payments&status=working&user=Alice&page_size=20"),
  );
  expect(await optionsOf("Status")).toEqual(["All statuses", "Working", "Completed", "Failed", "Stopped", "Retrying"]);
});

test("Started and Tokens headers sort the list", async () => {
  const fetchMock = listing(1);
  renderRuns();
  await screen.findByText("How many retries?");
  const started = () => screen.getByRole("columnheader", { name: /Started/ });
  const tokens = () => screen.getByRole("columnheader", { name: /Tokens/ });
  expect(started()).toHaveAttribute("aria-sort", "descending");
  expect(tokens()).not.toHaveAttribute("aria-sort");
  expect(within(screen.getByRole("columnheader", { name: "Agent" })).queryByRole("button")).toBeNull();
  fireEvent.click(within(started()).getByRole("button"));
  await waitFor(() => expect(where()).toBe("?order=asc"));
  expect(started()).toHaveAttribute("aria-sort", "ascending");
  fireEvent.click(within(tokens()).getByRole("button"));
  await waitFor(() => expect(where()).toBe("?sort=tokens"));
  expect(tokens()).toHaveAttribute("aria-sort", "descending");
  fireEvent.click(within(tokens()).getByRole("button"));
  await waitFor(() => expect(where()).toBe("?sort=tokens&order=asc"));
  await waitFor(() => expect(runCalls(fetchMock).at(-1)).toBe("/api/agent-runs?sort=tokens&order=asc"));
});

test("the footer counts pages and runs and moves between pages", async () => {
  const fetchMock = listing(1234);
  renderRuns("?page=2");
  expect(await screen.findByText("Showing 51–100 of 1234")).toBeVisible();
  expect(screen.getByText("Page 2 of 25")).toBeVisible();
  expect(runCalls(fetchMock).at(-1)).toBe("/api/agent-runs?page=2");
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  await waitFor(() => expect(where()).toBe("?page=3"));
  fireEvent.click(await screen.findByRole("button", { name: "Previous page" }));
  await waitFor(() => expect(where()).toBe("?page=2"));
  fireEvent.click(await screen.findByRole("button", { name: "Previous page" }));
  await waitFor(() => expect(where()).toBe(""));
  await waitFor(() => expect(runCalls(fetchMock).at(-1)).toBe("/api/agent-runs"));
  expect(await screen.findByRole("button", { name: "Previous page" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Next page" })).toBeEnabled();
});

test("the last page has no Next; Skeleton rows while loading; no Previous/Next text pager", async () => {
  listing(1);
  renderRuns();
  expect(screen.getAllByTestId("skeleton-row").length).toBeGreaterThan(0);
  expect(await screen.findByText("Showing 1–1 of 1")).toBeVisible();
  expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Next" })).toBeNull();
});

test("changing a filter goes back to the first page", async () => {
  const fetchMock = listing(500);
  renderRuns("?page=3&sort=tokens");
  await screen.findByText("Page 3 of 10");
  await choose("Status", "Failed");
  await waitFor(() => expect(where()).toBe("?sort=tokens&status=failed"));
  await waitFor(() => expect(runCalls(fetchMock).at(-1)).toBe("/api/agent-runs?status=failed&sort=tokens"));
});

test("no runs under filters says so; an unknown agent in the URL still shows in the select", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url === "/api/agents" ? json({ agents: [] }) : json({ runs: [], total: 0, page: 1, page_size: 50 }),
    ),
  );
  renderRuns("?agent_id=gone&page_size=abc");
  expect(await screen.findByText("No runs match these filters.")).toBeVisible();
  expect(screen.getByRole("combobox", { name: "Agent" })).toHaveTextContent("gone");
});

test("the Output filter lists runs that finished with an output schema's JSON, or the ones without", async () => {
  const fetchMock = listing(1);
  renderRuns();
  await screen.findByText("How many retries?");
  expect(await optionsOf("Output")).toEqual(["With or without output", "Has output", "No output"]);
  await choose("Output", "Has output");
  await waitFor(() => expect(runCalls(fetchMock).at(-1)).toBe("/api/agent-runs?has_output=true"));
  expect(where()).toBe("?has_output=true");
  await screen.findByText("How many retries?");
  await choose("Output", "No output");
  await waitFor(() => expect(runCalls(fetchMock).at(-1)).toBe("/api/agent-runs?has_output=false"));
});

test("the Schedule filter lists the schedules and asks for the runs one started", async () => {
  const fetchMock = vi.fn(async (url: string) =>
    url === "/api/schedules"
      ? json({ schedules: [{ id: "c1", name: "Nightly check" }] })
      : url === "/api/agents"
        ? json({ agents: [] })
        : json(runs),
  );
  vi.stubGlobal("fetch", fetchMock);
  renderRuns();
  await screen.findByText("How many retries?");
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url === "/api/schedules")).toBe(true));
  await choose("Schedule", "Nightly check");
  await waitFor(() => expect(runCalls(fetchMock).at(-1)).toBe("/api/agent-runs?schedule_id=c1"));
  expect(where()).toBe("?schedule_id=c1");
});

test("a schedule in the URL that is no longer listed reads (deleted)", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url === "/api/schedules" ? json({ schedules: [] }) : url === "/api/agents" ? json({ agents: [] }) : json(runs),
    ),
  );
  renderRuns("?schedule_id=gone");
  await screen.findByText("How many retries?");
  await waitFor(() => expect(screen.getByRole("combobox", { name: "Schedule" })).toHaveTextContent("(deleted)"));
});

test("an old link's page size that the table does not offer (25) asks for the nearest one (20)", async () => {
  const fetchMock = listing(30);
  renderRuns("?page_size=25");
  await screen.findByText("How many retries?");
  expect(runCalls(fetchMock).at(-1)).toBe("/api/agent-runs?page_size=20");
  expect(screen.getByRole("combobox", { name: "Rows per page" })).toHaveTextContent("20");
  expect(screen.getByText("Page 1 of 2")).toBeInTheDocument();
});

test("a review run is labelled Review of <agent name>", async () => {
  const review = {
    ...runs.runs[0],
    run_key: "cc:rv1:1",
    agent_id: "ops",
    agent: "Session reviewer (Operations)",
    kind: "review_session",
    title: "Review: Be shorter",
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url === "/api/agents"
        ? json({ agents: [{ id: "ops", name: "Operations" }] })
        : url.startsWith("/api/agent-runs")
          ? json({ ...runs, runs: [review] })
          : json({ schedules: [] }),
    ),
  );
  render(
    <MemoryRouter>
      <RunsView />
    </MemoryRouter>,
  );
  expect(await screen.findByText("Review of Operations")).toBeInTheDocument();
});
