import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import EventsView from "~/features/events/EventsView";
import { describeChanges } from "~/features/events/api";

const row = (id: number, extra = {}) => ({ id, source: "jira", type: "jira.ticket.created", subject: `DEMO-${id}`, title: `Ticket ${id}`,
  occurred_at: "2026-10-09T10:00:00Z", created_at: "2026-10-09T10:00:05Z", status: "processed", depth: 0, parent_id: null,
  root_id: id, handler_id: null, ...extra });
const FACETS = { sources: ["handler", "jira"], types: ["jira.ticket.created", "jira.ticket.updated"], statuses: ["failed", "processed"],
  handlers: [{ id: "h-1", name: "Triage" }] };
const detail = (id: number, extra = {}) => ({ ...row(id), source_id: "c-1", dedupe_key: "k", payload: {}, parent: null, chain: [row(id)], runs: [], ...extra });

function api(pages: Record<string, unknown>) {
  const fetch = vi.fn(async (url: string) => {
    const body = pages[url];
    if (body === undefined) throw new Error(`unexpected ${url}`);
    return new Response(JSON.stringify(body));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const view = () => render(<MemoryRouter><EventsView /></MemoryRouter>);
afterEach(() => vi.unstubAllGlobals());

test("lists events with source and status and loads more by offset", async () => {
  const user = userEvent.setup();
  api({ "/api/events/facets": FACETS,
    "/api/events": { events: [row(3, { source: "handler", status: "failed" }), row(2)], next_offset: 2 },
    "/api/events?offset=2": { events: [row(1)], next_offset: null } });
  view();
  expect(await screen.findByText("DEMO-3")).toBeInTheDocument();
  const table = screen.getByRole("table");
  expect(within(table).getByText("Handler")).toBeInTheDocument();
  expect(within(table).getByText("Failed")).toBeInTheDocument();
  expect(within(table).getAllByText("Processed")).toHaveLength(1);
  await user.click(screen.getByRole("button", { name: "Load more" }));
  expect(await screen.findByText("DEMO-1")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
});

test("a column header sorts, and clicking it again flips the order", async () => {
  const user = userEvent.setup();
  const fetch = api({ "/api/events/facets": FACETS, "/api/events": { events: [row(2)], next_offset: null },
    "/api/events?sort=subject&order=desc": { events: [row(5)], next_offset: null },
    "/api/events?sort=subject&order=asc": { events: [row(4)], next_offset: null },
    "/api/events?order=asc": { events: [row(1)], next_offset: null } });
  view();
  await screen.findByText("DEMO-2");
  await user.click(screen.getByRole("button", { name: "Subject" }));
  expect(await screen.findByText("DEMO-5")).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: /Subject/ })).toHaveAttribute("aria-sort", "descending");
  await user.click(screen.getByRole("button", { name: "Subject" }));
  expect(await screen.findByText("DEMO-4")).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: /Subject/ })).toHaveAttribute("aria-sort", "ascending");
  await user.click(screen.getByRole("button", { name: "Time" }));
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url === "/api/events")).toBe(true));
  await user.click(screen.getByRole("button", { name: "Time" }));
  expect(await screen.findByText("DEMO-1")).toBeInTheDocument();
});

test("filters by type, status, handler and search", async () => {
  const user = userEvent.setup();
  const fetch = api({ "/api/events/facets": FACETS, "/api/events": { events: [row(2)], next_offset: null },
    "/api/events?type=jira.ticket.updated": { events: [row(9, { type: "jira.ticket.updated" })], next_offset: null },
    "/api/events?type=jira.ticket.updated&status=failed": { events: [row(8)], next_offset: null },
    "/api/events?type=jira.ticket.updated&status=failed&handler_id=h-1": { events: [row(7)], next_offset: null },
    "/api/events?type=jira.ticket.updated&status=failed&handler_id=h-1&q=DEMO-7": { events: [row(7)], next_offset: null } });
  view();
  await screen.findByText("DEMO-2");
  await user.click(screen.getByRole("combobox", { name: "Type" }));
  await user.click(screen.getByRole("option", { name: "Ticket updated" }));
  expect(await screen.findByText("DEMO-9")).toBeInTheDocument();
  await user.click(screen.getByRole("combobox", { name: "Status" }));
  await user.click(screen.getByRole("option", { name: "Failed" }));
  expect(await screen.findByText("DEMO-8")).toBeInTheDocument();
  await user.click(screen.getByRole("combobox", { name: "Emitted by" }));
  await user.click(screen.getByRole("option", { name: "Triage" }));
  expect(await screen.findByText("DEMO-7")).toBeInTheDocument();
  await user.type(screen.getByLabelText("Ticket or title"), "DEMO-7");
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url === "/api/events?type=jira.ticket.updated&status=failed&handler_id=h-1&q=DEMO-7")).toBe(true));
});

test("an event opens with its changes and a Jira link", async () => {
  const user = userEvent.setup();
  api({ "/api/events/facets": FACETS, "/api/events": { events: [row(7, { type: "jira.ticket.updated" })], next_offset: null },
    "/api/events/7": { event: detail(7, { type: "jira.ticket.updated",
      payload: { url: "https://jira.internal/jira/browse/DEMO-7", changes: { status: { from: "Open", to: "Done" }, labels: { added: ["agent"], removed: [] } } } }) } });
  view();
  await user.click(await screen.findByText("DEMO-7"));
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByText("Status: Open → Done")).toBeInTheDocument();
  expect(within(sheet).getByText("Labels: +agent")).toBeInTheDocument();
  expect(within(sheet).getByRole("link", { name: "Open in Jira" })).toHaveAttribute("href", "https://jira.internal/jira/browse/DEMO-7");
});

test("the sheet shows the chain and the handler runs with their links", async () => {
  const user = userEvent.setup();
  const child = row(8, { source: "handler", type: "triage.requested", subject: "T-8", depth: 1, parent_id: 7, root_id: 7, handler_id: "h-1" });
  api({ "/api/events/facets": FACETS, "/api/events": { events: [row(7)], next_offset: null },
    "/api/events/7": { event: detail(7, { chain: [row(7), child], runs: [
      { handler_id: "h-1", handler_name: "Triage", status: "succeeded", message: "Emitted triage.requested.", emitted_event_id: 8, agent_session_id: null, agent_id: null, created_at: "2026-10-09T10:00:06Z", agent_task: null },
      { handler_id: "h-2", handler_name: "Ask agent", status: "succeeded", message: "Started a session with Tester.", emitted_event_id: null, agent_session_id: "s-1", agent_id: "tester", created_at: "2026-10-09T10:00:06Z", agent_task: null },
      { handler_id: "h-3", handler_name: "Broken", status: "failed", message: "Template error: x", emitted_event_id: null, agent_session_id: null, agent_id: null, created_at: "2026-10-09T10:00:06Z", agent_task: null }] }) },
    "/api/events/8": { event: detail(8, { ...child, chain: [row(7), child], parent: row(7) }) } });
  view();
  await user.click(await screen.findByText("DEMO-7"));
  const sheet = await screen.findByRole("dialog");
  const chain = within(sheet).getByRole("list", { name: "Chain" });
  expect(within(chain).getByText(/DEMO-7/).closest("[aria-current]")).toHaveAttribute("aria-current", "true");
  expect(within(sheet).getByText("Template error: x")).toBeInTheDocument();
  expect(within(sheet).getByRole("link", { name: "Open agent session" })).toHaveAttribute("href", "/agents/tester?view=test&session_id=s-1");
  await user.click(within(sheet).getByRole("button", { name: "Open event #8" }));
  expect(await screen.findByRole("heading", { name: "T-8 · triage.requested" })).toBeInTheDocument();
  expect(within(screen.getByRole("dialog")).getByText("Parent")).toBeInTheDocument();
});

test("a run shows its status, Open run and Open notification; an older task keeps its own", async () => {
  const user = userEvent.setup();
  const base = { message: "Started a run of Tester.", emitted_event_id: null, created_at: "2026-10-09T10:00:06Z" };
  api({ "/api/events/facets": FACETS, "/api/events": { events: [row(7)], next_offset: null },
    "/api/events/7": { event: detail(7, { runs: [
      { ...base, handler_id: "h-1", handler_name: "New", status: "succeeded", agent_session_id: "s-9", agent_id: "tester", agent_task: null,
        run: { id: "s-9", status: "waiting", agent_id: "tester", notification_id: 12 } },
      { ...base, handler_id: "h-2", handler_name: "Old", status: "succeeded", agent_session_id: null, agent_id: "tester",
        agent_task: { id: "t-1", status: "failed", attempts: 3, notification_id: 4 } }] }) } });
  view();
  await user.click(await screen.findByText("DEMO-7"));
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByText("Waiting for you")).toBeInTheDocument();
  expect(within(sheet).getByRole("link", { name: "Open run" })).toHaveAttribute("href", "/agents/tester?view=test&session_id=s-9");
  const links = within(sheet).getAllByRole("link", { name: "Open notification" });
  expect(links.map(link => link.getAttribute("href"))).toEqual(["/notifications?id=12", "/notifications?id=4"]);
  expect(within(sheet).getByText(/Failed · 3 attempts/)).toBeInTheDocument();
});

test("empty state", async () => {
  api({ "/api/events/facets": { sources: [], types: [], statuses: [], handlers: [] }, "/api/events": { events: [], next_offset: null } });
  view();
  expect(await screen.findByText("No events yet. Turn on Jira → Auto pull to record ticket changes.")).toBeInTheDocument();
});

test("describeChanges", () => {
  expect(describeChanges({ assignee: { from: null, to: "Ana" }, description: { changed: true }, labels: { added: ["a"], removed: ["b"] } }))
    .toEqual(["Assignee: — → Ana", "Description changed", "Labels: +a −b"]);
  expect(describeChanges(undefined)).toEqual([]);
  expect(describeChanges({ a: null, b: "x", labels: { added: "z" } })).toEqual([]);
});

test("a Load more answered after a filter change is ignored", async () => {
  const user = userEvent.setup();
  let release: (response: Response) => void = () => {};
  const late = new Promise<Response>(resolve => { release = resolve; });
  const pages: Record<string, unknown> = { "/api/events/facets": FACETS, "/api/events": { events: [row(3)], next_offset: 1 },
    "/api/events?type=jira.ticket.updated": { events: [row(9, { type: "jira.ticket.updated" })], next_offset: null } };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url === "/api/events?offset=1") return late;
    if (pages[url] === undefined) throw new Error(`unexpected ${url}`);
    return new Response(JSON.stringify(pages[url]));
  }));
  view();
  await screen.findByText("DEMO-3");
  await user.click(screen.getByRole("button", { name: "Load more" }));
  await user.click(screen.getByRole("combobox", { name: "Type" }));
  await user.click(screen.getByRole("option", { name: "Ticket updated" }));
  expect(await screen.findByText("DEMO-9")).toBeInTheDocument();
  release(new Response(JSON.stringify({ events: [row(1)], next_offset: 2 })));
  await new Promise(resolve => setTimeout(resolve, 20));
  expect(screen.queryByText("DEMO-1")).toBeNull();
  expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
});

test("a non-http payload url gets no Jira link", async () => {
  const user = userEvent.setup();
  api({ "/api/events/facets": FACETS, "/api/events": { events: [row(7)], next_offset: null },
    "/api/events/7": { event: detail(7, { payload: { url: "javascript:alert(1)" } }) } });
  view();
  await user.click(await screen.findByText("DEMO-7"));
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).queryByRole("link", { name: "Open in Jira" })).toBeNull();
});

test("Load more skips an event already on the page", async () => {
  const user = userEvent.setup();
  api({ "/api/events/facets": FACETS, "/api/events": { events: [row(3), row(2)], next_offset: 2 },
    "/api/events?offset=2": { events: [row(2), row(1)], next_offset: null } });
  view();
  await screen.findByText("DEMO-3");
  await user.click(screen.getByRole("button", { name: "Load more" }));
  expect(await screen.findByText("DEMO-1")).toBeInTheDocument();
  expect(screen.getAllByText("DEMO-2")).toHaveLength(1);
});

test("the chain renders when the open event is missing from a capped chain", async () => {
  const user = userEvent.setup();
  const other = row(6, { subject: "DEMO-6", root_id: 6 });
  api({ "/api/events/facets": FACETS, "/api/events": { events: [row(7)], next_offset: null },
    "/api/events/7": { event: detail(7, { chain: [other] }) } });
  view();
  await user.click(await screen.findByText("DEMO-7"));
  const chain = within(await screen.findByRole("dialog")).getByRole("list", { name: "Chain" });
  expect(within(chain).getByText(/DEMO-7/).closest("[aria-current]")).toHaveAttribute("aria-current", "true");
  expect(within(chain).getByText(/DEMO-6/)).toBeInTheDocument();
});

test("a Send to agent run shows its task status and opens its notification", async () => {
  const user = userEvent.setup();
  const run = { handler_id: "h-1", handler_name: "Ask Triage", status: "succeeded", message: "Queued for Triage.", emitted_event_id: null,
    agent_session_id: null, agent_id: "triage", created_at: "2026-10-09T10:00:06Z",
    agent_task: { id: "agent-task:7:h-1", status: "succeeded", attempts: 2, notification_id: 9 } };
  const queued = { ...run, handler_name: "Ask Ops", message: "Queued for Ops.", agent_task: { id: "agent-task:7:h-2", status: "queued", attempts: 0, notification_id: null } };
  api({ "/api/events/facets": FACETS, "/api/events": { events: [row(7)], next_offset: null },
    "/api/events/7": { event: detail(7, { runs: [run, queued] }) } });
  view();
  await user.click(await screen.findByText("DEMO-7"));
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByText("Queued for Triage.")).toBeInTheDocument();
  expect(within(sheet).getByText("Succeeded · 2 attempts")).toBeInTheDocument();
  expect(within(sheet).getByText("Queued")).toBeInTheDocument();
  const links = within(sheet).getAllByRole("link", { name: "Open notification" });
  expect(links).toHaveLength(1);
  expect(links[0]).toHaveAttribute("href", "/notifications?id=9");
});

test("an address with ?event= opens that event", async () => {
  api({ "/api/events/facets": FACETS, "/api/events": { events: [], next_offset: null }, "/api/events/4": { event: detail(4) } });
  render(<MemoryRouter initialEntries={["/events?event=4"]}><EventsView /></MemoryRouter>);
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByText(/DEMO-4/)).toBeInTheDocument();
});
