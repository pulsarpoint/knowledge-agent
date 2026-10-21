import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import EventsView from "~/features/events/EventsView";

const row = (id: number, extra = {}) => ({
  id,
  source: "jira",
  type: "jira.ticket.created",
  subject: `DEMO-${id}`,
  title: `Ticket ${id}`,
  occurred_at: "2026-10-09T10:00:00Z",
  created_at: "2026-10-09T10:00:05Z",
  status: "processed",
  depth: 0,
  parent_id: null,
  root_id: id,
  handler_id: null,
  ...extra,
});
const detail = (id: number, extra = {}) => ({
  ...row(id),
  source_id: "c-1",
  dedupe_key: "k",
  created_manually: false,
  payload: { labels: ["agent"] },
  parent: null,
  chain: [row(id)],
  runs: [],
  ...extra,
});
const FACETS = {
  sources: ["jira"],
  types: ["jira.ticket.created", "jira.ticket.updated"],
  statuses: ["processed"],
  handlers: [],
  schedules: [],
};

interface Call {
  method: string;
  url: string;
  body: unknown;
}
function api(routes: Record<string, () => Response>) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const route = routes[`${method} ${url}`];
      if (!route) throw new Error(`unexpected ${method} ${url}`);
      return route();
    }),
  );
  return calls;
}
const json =
  (body: unknown, status = 200) =>
  () =>
    new Response(JSON.stringify(body), { status });
const view = () =>
  render(
    <MemoryRouter>
      <EventsView />
    </MemoryRouter>,
  );
afterEach(() => vi.unstubAllGlobals());

async function openForm(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText("DEMO-1");
  await user.click(screen.getByRole("button", { name: "New event" }));
  return screen.findByRole("dialog");
}
const BASE = {
  "GET /api/events/facets": json(FACETS),
  "GET /api/events": json({ events: [row(1)], next_offset: null }),
};

test("Create sends the form, refreshes the list and opens the new event", async () => {
  const user = userEvent.setup();
  const calls = api({
    ...BASE,
    "POST /api/events": json(
      { event: detail(7, { source: "manual", subject: "DEMO-9", created_manually: true, status: "pending" }) },
      201,
    ),
    "GET /api/events/7": json({
      event: detail(7, { source: "manual", subject: "DEMO-9", created_manually: true, status: "pending" }),
    }),
  });
  view();
  const form = await openForm(user);
  expect(within(form).getByRole("button", { name: "Create" })).toBeDisabled();
  await user.type(within(form).getByLabelText("Type"), "jira.ticket.created");
  await user.type(within(form).getByLabelText("Subject"), "DEMO-9");
  await user.type(within(form).getByLabelText("Title"), "Try me");
  fireEvent.change(within(form).getByLabelText("Payload"), { target: { value: '{"labels":["agent"]}' } });
  await user.click(within(form).getByRole("button", { name: "Create" }));
  await waitFor(() =>
    expect(calls.find((call) => call.method === "POST")?.body).toEqual({
      source: "manual",
      type: "jira.ticket.created",
      subject: "DEMO-9",
      title: "Try me",
      payload: { labels: ["agent"] },
    }),
  );
  expect(await screen.findByText("Created by hand")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Create" })).toBeNull();
  expect(calls.filter((call) => call.url === "/api/events" && call.method === "GET").length).toBeGreaterThanOrEqual(2);
});

test("a payload that is not valid JSON, or not an object, disables Create", async () => {
  const user = userEvent.setup();
  api(BASE);
  view();
  const form = await openForm(user);
  await user.type(within(form).getByLabelText("Type"), "a.b");
  await user.type(within(form).getByLabelText("Subject"), "X");
  const create = within(form).getByRole("button", { name: "Create" });
  expect(create).toBeEnabled();
  fireEvent.change(within(form).getByLabelText("Payload"), { target: { value: "{oops" } });
  expect(within(form).getByText(/not valid JSON/i)).toBeInTheDocument();
  expect(create).toBeDisabled();
  fireEvent.change(within(form).getByLabelText("Payload"), { target: { value: "[1]" } });
  expect(within(form).getByText(/must be a JSON object/i)).toBeInTheDocument();
  expect(create).toBeDisabled();
  fireEvent.change(within(form).getByLabelText("Payload"), { target: { value: "{}" } });
  expect(create).toBeEnabled();
});

test("a server error is shown in the form", async () => {
  const user = userEvent.setup();
  api({ ...BASE, "POST /api/events": json({ error: { message: "Type must look like jira.ticket.created." } }, 400) });
  view();
  const form = await openForm(user);
  await user.type(within(form).getByLabelText("Type"), "Bad");
  await user.type(within(form).getByLabelText("Subject"), "X");
  await user.click(within(form).getByRole("button", { name: "Create" }));
  expect(await within(form).findByText("Type must look like jira.ticket.created.")).toBeInTheDocument();
});

test("Duplicate pre-fills the form from the open event, keeping a Jira source", async () => {
  const user = userEvent.setup();
  const calls = api({
    ...BASE,
    "GET /api/events/1": json({ event: detail(1) }),
    "POST /api/events": json({ event: detail(8, { source: "jira", created_manually: true, status: "pending" }) }, 201),
    "GET /api/events/8": json({ event: detail(8, { source: "jira", created_manually: true, status: "pending" }) }),
  });
  view();
  await user.click(await screen.findByText("DEMO-1"));
  await user.click(await screen.findByRole("button", { name: "Duplicate" }));
  const form = await screen.findByRole("dialog", { name: "New event" });
  await waitFor(() => expect(screen.getAllByRole("dialog", { hidden: true })).toHaveLength(1)); // the detail sheet closed
  expect(within(form).getByLabelText("Type")).toHaveValue("jira.ticket.created");
  expect(within(form).getByLabelText("Subject")).toHaveValue("DEMO-1");
  expect(within(form).getByLabelText("Title")).toHaveValue("Ticket 1");
  expect(within(form).getByLabelText("Payload")).toHaveValue(JSON.stringify({ labels: ["agent"] }, null, 2));
  expect(within(form).getByRole("combobox", { name: "Source" })).toHaveTextContent("Jira (simulated)");
  await user.click(within(form).getByRole("button", { name: "Create" }));
  await waitFor(() =>
    expect(calls.find((call) => call.method === "POST")?.body).toEqual({
      source: "jira",
      type: "jira.ticket.created",
      subject: "DEMO-1",
      title: "Ticket 1",
      payload: { labels: ["agent"] },
    }),
  );
});

test("the title is trimmed before it is sent", async () => {
  const user = userEvent.setup();
  const calls = api({ ...BASE, "POST /api/events": json({ error: { message: "stop" } }, 400) });
  view();
  const form = await openForm(user);
  await user.type(within(form).getByLabelText("Type"), "a.b");
  await user.type(within(form).getByLabelText("Subject"), "X");
  await user.type(within(form).getByLabelText("Title"), "  Hi  ");
  await user.click(within(form).getByRole("button", { name: "Create" }));
  await waitFor(() =>
    expect((calls.find((call) => call.method === "POST")?.body as { title: string }).title).toBe("Hi"),
  );
});

test("the form starts with source Manual", async () => {
  const user = userEvent.setup();
  api(BASE);
  view();
  const form = await openForm(user);
  expect(within(form).getByRole("combobox", { name: "Source" })).toHaveTextContent("Manual");
});

test("an event made by hand says so; others do not", async () => {
  const user = userEvent.setup();
  api({ ...BASE, "GET /api/events/1": json({ event: detail(1) }) });
  view();
  await user.click(await screen.findByText("DEMO-1"));
  await screen.findByRole("button", { name: "Duplicate" });
  expect(screen.queryByText("Created by hand")).toBeNull();
});
