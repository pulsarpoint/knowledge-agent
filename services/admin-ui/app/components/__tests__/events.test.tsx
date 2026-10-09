import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import EventsView from "../EventsView";
import { describeChanges } from "../../lib/events";

const row = (id: number, extra = {}) => ({ id, source: "jira", type: "jira.ticket.created", subject: `DEMO-${id}`, title: `Ticket ${id}`,
  occurred_at: "2026-10-09T10:00:00Z", created_at: "2026-10-09T10:00:05Z", ...extra });

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

test("lists events newest first and loads more", async () => {
  const user = userEvent.setup();
  api({ "/api/events/facets": { sources: ["jira"], types: ["jira.ticket.created"] },
    "/api/events": { events: [row(3), row(2)], next_before: 2 },
    "/api/events?before=2": { events: [row(1)], next_before: null } });
  view();
  expect(await screen.findByText("DEMO-3")).toBeInTheDocument();
  expect(screen.getAllByText("Ticket created")).toHaveLength(2);
  await user.click(screen.getByRole("button", { name: "Load more" }));
  expect(await screen.findByText("DEMO-1")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
});

test("filters by type and search", async () => {
  const user = userEvent.setup();
  const fetch = api({ "/api/events/facets": { sources: ["jira"], types: ["jira.ticket.created", "jira.ticket.updated"] },
    "/api/events": { events: [row(2)], next_before: null },
    "/api/events?type=jira.ticket.updated": { events: [row(9, { type: "jira.ticket.updated" })], next_before: null },
    "/api/events?type=jira.ticket.updated&q=DEMO-9": { events: [row(9, { type: "jira.ticket.updated" })], next_before: null } });
  view();
  await screen.findByText("DEMO-2");
  await user.click(screen.getByRole("combobox", { name: "Type" }));
  await user.click(screen.getByRole("option", { name: "Ticket updated" }));
  expect(await screen.findByText("DEMO-9")).toBeInTheDocument();
  await user.type(screen.getByLabelText("Ticket or title"), "DEMO-9");
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url === "/api/events?type=jira.ticket.updated&q=DEMO-9")).toBe(true));
});

test("an event opens with its changes and a Jira link", async () => {
  const user = userEvent.setup();
  api({ "/api/events/facets": { sources: ["jira"], types: ["jira.ticket.updated"] },
    "/api/events": { events: [row(7, { type: "jira.ticket.updated" })], next_before: null },
    "/api/events/7": { event: { ...row(7, { type: "jira.ticket.updated" }), source_id: "c-1", dedupe_key: "jira:c-1:7:x",
      payload: { url: "https://jira.internal/jira/browse/DEMO-7", changes: { status: { from: "Open", to: "Done" }, labels: { added: ["agent"], removed: [] } } } } } });
  view();
  await user.click(await screen.findByText("DEMO-7"));
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByText("Status: Open → Done")).toBeInTheDocument();
  expect(within(sheet).getByText("Labels: +agent")).toBeInTheDocument();
  expect(within(sheet).getByRole("link", { name: "Open in Jira" })).toHaveAttribute("href", "https://jira.internal/jira/browse/DEMO-7");
});

test("empty state", async () => {
  api({ "/api/events/facets": { sources: [], types: [] }, "/api/events": { events: [], next_before: null } });
  view();
  expect(await screen.findByText("No events yet. Turn on Jira → Auto pull to record ticket changes.")).toBeInTheDocument();
});

test("describeChanges", () => {
  expect(describeChanges({ assignee: { from: null, to: "Ana" }, description: { changed: true }, labels: { added: ["a"], removed: ["b"] } }))
    .toEqual(["Assignee: — → Ana", "Description changed", "Labels: +a −b"]);
  expect(describeChanges(undefined)).toEqual([]);
});

test("a Load more answered after a filter change is ignored", async () => {
  const user = userEvent.setup();
  let release: (response: Response) => void = () => {};
  const late = new Promise<Response>(resolve => { release = resolve; });
  const pages: Record<string, unknown> = {
    "/api/events/facets": { sources: ["jira"], types: ["jira.ticket.created", "jira.ticket.updated"] },
    "/api/events": { events: [row(3)], next_before: 3 },
    "/api/events?type=jira.ticket.updated": { events: [row(9, { type: "jira.ticket.updated" })], next_before: null } };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url === "/api/events?before=3") return late;
    if (pages[url] === undefined) throw new Error(`unexpected ${url}`);
    return new Response(JSON.stringify(pages[url]));
  }));
  view();
  await screen.findByText("DEMO-3");
  await user.click(screen.getByRole("button", { name: "Load more" }));
  await user.click(screen.getByRole("combobox", { name: "Type" }));
  await user.click(screen.getByRole("option", { name: "Ticket updated" }));
  expect(await screen.findByText("DEMO-9")).toBeInTheDocument();
  release(new Response(JSON.stringify({ events: [row(1)], next_before: 1 })));
  await new Promise(resolve => setTimeout(resolve, 20));
  expect(screen.queryByText("DEMO-1")).toBeNull();
  expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
});

test("a non-http payload url gets no Jira link", async () => {
  const user = userEvent.setup();
  api({ "/api/events/facets": { sources: ["jira"], types: ["jira.ticket.created"] },
    "/api/events": { events: [row(7)], next_before: null },
    "/api/events/7": { event: { ...row(7), source_id: null, dedupe_key: "k", payload: { url: "javascript:alert(1)" } } } });
  view();
  await user.click(await screen.findByText("DEMO-7"));
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).queryByRole("link", { name: "Open in Jira" })).toBeNull();
});
