import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import NotificationsView from "~/features/events/NotificationsView";

const row = (id: number, extra = {}) => ({
  id,
  created_at: "2026-10-09T10:00:00Z",
  kind: "agent.response",
  status: "succeeded",
  title: `Triage · jira.ticket.updated DEMO-${id}`,
  preview: `Answer ${id}\nmore`,
  event_id: 40 + id,
  agent_id: "triage",
  agent_name: "Triage",
  ...extra,
});
const detail = (id: number, extra = {}) => ({
  ...row(id),
  body: `Answer ${id}\nmore lines`,
  handler_id: "h-1",
  event: { id: 40 + id, type: "jira.ticket.updated", subject: `DEMO-${id}` },
  task: {
    id: `agent-task:${40 + id}:h-1`,
    attempts: 2,
    created_at: "2026-10-09T10:00:00Z",
    finished_at: "2026-10-09T10:01:00Z",
  },
  ...extra,
});

function api(pages: Record<string, unknown>) {
  const fetch = vi.fn(async (url: string) => {
    const body = pages[url];
    if (body === undefined) throw new Error(`unexpected ${url}`);
    return new Response(JSON.stringify(body));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const view = (entry = "/notifications") =>
  render(
    <MemoryRouter initialEntries={[entry]}>
      <NotificationsView />
    </MemoryRouter>,
  );
afterEach(() => vi.unstubAllGlobals());

test("lists notifications newest first with status and the answer's first line, and loads more", async () => {
  const user = userEvent.setup();
  api({
    "/api/notifications": {
      notifications: [row(3, { status: "failed", preview: "Agent is disabled." }), row(2)],
      next_offset: 2,
    },
    "/api/notifications?offset=2": { notifications: [row(2), row(1)], next_offset: null },
  });
  view();
  const table = await screen.findByRole("table");
  expect(within(table).getByRole("columnheader", { name: "Preview" })).toBeInTheDocument();
  expect(within(table).getByText("Failed")).toBeInTheDocument();
  expect(within(table).getByText("Answer 2")).toBeInTheDocument();
  expect(within(table).queryByText(/more/)).toBeNull(); // only the first line
  await user.click(screen.getByRole("button", { name: "Load more" }));
  expect(await screen.findByText("Triage · jira.ticket.updated DEMO-1")).toBeInTheDocument();
  expect(screen.getAllByText("Triage · jira.ticket.updated DEMO-2")).toHaveLength(1); // de-duplicated by id
});

test("filters by status and searches the title", async () => {
  const user = userEvent.setup();
  const fetch = api({
    "/api/notifications": { notifications: [row(1)], next_offset: null },
    "/api/notifications?status=failed": { notifications: [], next_offset: null },
    "/api/notifications?status=failed&q=DEMO": { notifications: [row(2, { status: "failed" })], next_offset: null },
  });
  view();
  await screen.findByText("Answer 1");
  await user.click(screen.getByRole("combobox", { name: "Status" }));
  await user.click(screen.getByRole("option", { name: "Failed" }));
  expect(await screen.findByText("No notifications match these filters.")).toBeInTheDocument();
  await user.type(screen.getByLabelText("Search title"), "DEMO");
  expect(await screen.findByText("Answer 2")).toBeInTheDocument();
  expect(fetch.mock.calls.map(([url]) => url)).toContain("/api/notifications?status=failed&q=DEMO");
});

test("opening a notification shows the full answer, the agent, attempts and a link to its event", async () => {
  const user = userEvent.setup();
  api({
    "/api/notifications": { notifications: [row(1)], next_offset: null },
    "/api/notifications/1": { notification: detail(1) },
  });
  view();
  await user.click(await screen.findByText("Triage · jira.ticket.updated DEMO-1"));
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByText(/Answer 1\s+more lines/)).toBeInTheDocument();
  expect(within(sheet).getByText("Triage")).toBeInTheDocument();
  expect(within(sheet).getByText("2")).toBeInTheDocument();
  expect(within(sheet).getByRole("link", { name: "Open event" })).toHaveAttribute("href", "/events?event=41");
});

test("a notification of a run links to the run", async () => {
  const user = userEvent.setup();
  api({
    "/api/notifications": { notifications: [row(1, { session_id: "s-1" })], next_offset: null },
    "/api/notifications/1": { notification: detail(1, { session_id: "s-1", task: null }) },
  });
  view();
  await user.click(await screen.findByText("Triage · jira.ticket.updated DEMO-1"));
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByRole("link", { name: "Open run" })).toHaveAttribute(
    "href",
    "/agents/triage?view=test&session_id=s-1",
  );
  expect(within(sheet).getByRole("link", { name: "Open event" })).toHaveAttribute("href", "/events?event=41");
});

test("a notification whose event is gone has no event link", async () => {
  const user = userEvent.setup();
  api({
    "/api/notifications": { notifications: [row(1, { event_id: null })], next_offset: null },
    "/api/notifications/1": { notification: detail(1, { event_id: null, event: null }) },
  });
  view();
  await user.click(await screen.findByText("Triage · jira.ticket.updated DEMO-1"));
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).queryByRole("link", { name: "Open event" })).toBeNull();
});

test("an address with ?id= opens that notification", async () => {
  api({
    "/api/notifications": { notifications: [], next_offset: null },
    "/api/notifications/5": { notification: detail(5) },
  });
  view("/notifications?id=5");
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByText(/Answer 5/)).toBeInTheDocument();
});

test("empty and error states", async () => {
  api({ "/api/notifications": { notifications: [], next_offset: null } });
  view();
  expect(
    await screen.findByText(
      "No notifications yet. A handler that sends events to an agent puts the agent's answers here.",
    ),
  ).toBeInTheDocument();
  vi.unstubAllGlobals();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ error: { message: "Database down" } }), { status: 503 })),
  );
  view();
  await waitFor(() => expect(screen.getByText("Database down")).toBeInTheDocument());
});

const JIRA = { jira_base_url: "https://jira.internal/jira", jira_attempts: 1, jira_comment_id: null, jira_error: null };
test.each([
  [{ jira_status: "posted", jira_issue_key: "DEMO-3", jira_comment_id: "10500" }, "Posted to DEMO-3"],
  [
    { jira_status: "pending", jira_issue_key: "DEMO-3", jira_error: "Jira could not complete the request" },
    "Posting to DEMO-3… (attempt 1 failed: Jira could not complete the request)",
  ],
  [{ jira_status: "pending", jira_issue_key: "DEMO-3", jira_attempts: 0 }, "Posting to DEMO-3…"],
  [
    { jira_status: "failed", jira_issue_key: "DEMO-3", jira_error: "Jira refused the comment" },
    "Not posted to DEMO-3: Jira refused the comment",
  ],
])("the detail shows the Jira comment's state %#", async (jira, text) => {
  api({
    "/api/notifications": { notifications: [], next_offset: null },
    "/api/notifications/3": { notification: detail(3, { ...JIRA, ...jira }) },
  });
  view("/notifications?id=3");
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByText(text)).toBeInTheDocument();
});

test("a posted comment links to it on Jira", async () => {
  api({
    "/api/notifications": { notifications: [], next_offset: null },
    "/api/notifications/3": {
      notification: detail(3, { ...JIRA, jira_status: "posted", jira_issue_key: "DEMO-3", jira_comment_id: "10500" }),
    },
  });
  view("/notifications?id=3");
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByRole("link", { name: "Posted to DEMO-3" })).toHaveAttribute(
    "href",
    "https://jira.internal/jira/browse/DEMO-3?focusedCommentId=10500",
  );
});

test("a notification without a Jira comment shows no Jira line", async () => {
  api({
    "/api/notifications": { notifications: [], next_offset: null },
    "/api/notifications/4": { notification: detail(4) },
  });
  view("/notifications?id=4");
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).queryByText("Jira")).toBeNull();
});
