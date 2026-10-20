import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import JiraUserAgents from "~/features/jira/JiraUserAgents";

const row = (extra = {}) => ({
  jira_user_key: "ops-bot",
  jira_user_name: "Ops Bot",
  agent_id: "ops",
  agent_name: "Ops agent",
  enabled: true,
  updated_at: "2026-10-19T10:00:00Z",
  ...extra,
});
const AGENTS = {
  agents: [
    { id: "ops", name: "Ops agent", enabled: true },
    { id: "docs", name: "Docs agent", enabled: true },
  ],
};

type Route = (body: unknown, url: string) => { status?: number; body?: unknown };
function api(routes: Record<string, Route | unknown>) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url.split("?")[0]}`;
    const route = routes[key];
    if (route === undefined) throw new Error(`unexpected ${key}`);
    const answer =
      typeof route === "function"
        ? (route as Route)(init?.body ? JSON.parse(String(init.body)) : undefined, url)
        : { body: route };
    return new Response(answer.status === 204 ? null : JSON.stringify(answer.body), { status: answer.status ?? 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
afterEach(() => vi.unstubAllGlobals());
const view = () =>
  render(
    <MemoryRouter>
      <JiraUserAgents />
    </MemoryRouter>,
  );

test("lists Jira users with their agents and marks a disabled agent", async () => {
  api({
    "GET /api/jira/user-agents": {
      user_agents: [
        row(),
        row({
          jira_user_key: "docs-bot",
          jira_user_name: "Docs Bot",
          agent_id: "docs",
          agent_name: "Docs agent",
          enabled: false,
        }),
      ],
    },
    "GET /api/agents": AGENTS,
  });
  view();
  const ops = (await screen.findByText("Ops Bot")).closest("li")!;
  expect(within(ops).getByText("ops-bot")).toBeInTheDocument();
  expect(within(ops).getByRole("combobox", { name: "Agent for Ops Bot" })).toHaveTextContent("Ops agent");
  expect(within(ops).queryByText("Agent disabled")).toBeNull();
  const docs = screen.getByText("Docs Bot").closest("li")!;
  expect(within(docs).getByText("Agent disabled")).toBeInTheDocument();
});

test("adds a user picked from the search with an agent", async () => {
  const user = userEvent.setup();
  let saved: unknown;
  let savedUrl = "";
  let searched = "";
  api({
    "GET /api/jira/user-agents": { user_agents: [] },
    "GET /api/agents": AGENTS,
    "GET /api/jira/users": (_body: unknown, url: string) => {
      searched = url;
      return { body: { users: [{ key: "ops-bot", name: "ops-bot", display_name: "Ops Bot" }], warning: null } };
    },
    "PUT /api/jira/user-agents/ops-bot": (body: unknown, url: string) => {
      saved = body;
      savedUrl = url;
      return { body: { user_agents: [row()] } };
    },
  });
  view();
  await user.type(await screen.findByLabelText("Jira user"), "ops");
  await user.click(await screen.findByRole("button", { name: "Ops Bot (ops-bot)" }));
  expect(searched).toBe("/api/jira/users?query=ops");
  expect(screen.getByLabelText("Jira user")).toHaveValue("Ops Bot (ops-bot)");
  await user.click(screen.getByRole("combobox", { name: "Agent" }));
  await user.click(screen.getByRole("option", { name: "Ops agent" }));
  await user.click(screen.getByRole("button", { name: "Add" }));
  await waitFor(() => expect(saved).toEqual({ agent_id: "ops", name: "Ops Bot" }));
  expect(savedUrl).toBe("/api/jira/user-agents/ops-bot");
  expect((await screen.findByText("Ops Bot")).closest("li")).toBeInTheDocument();
  expect(screen.getByLabelText("Jira user")).toHaveValue("");
});

test("a typed key is accepted and search warnings are shown", async () => {
  const user = userEvent.setup();
  let saved: unknown;
  api({
    "GET /api/jira/user-agents": { user_agents: [] },
    "GET /api/agents": AGENTS,
    "GET /api/jira/users": { users: [], warning: "Jira user search failed: Jira rejected access" },
    "PUT /api/jira/user-agents/new-bot": (body: unknown) => {
      saved = body;
      return { body: { user_agents: [row({ jira_user_key: "new-bot", jira_user_name: "new-bot" })] } };
    },
  });
  view();
  await user.type(await screen.findByLabelText("Jira user"), "new-bot");
  expect(await screen.findByText("Jira user search failed: Jira rejected access")).toBeInTheDocument();
  await user.click(screen.getByRole("combobox", { name: "Agent" }));
  await user.click(screen.getByRole("option", { name: "Docs agent" }));
  await user.click(screen.getByRole("button", { name: "Add" }));
  await waitFor(() => expect(saved).toEqual({ agent_id: "docs", name: "" }));
});

test("changes a row's agent and removes a row", async () => {
  const user = userEvent.setup();
  let changed: unknown;
  const fetch = api({
    "GET /api/jira/user-agents": { user_agents: [row()] },
    "GET /api/agents": AGENTS,
    "PUT /api/jira/user-agents/ops-bot": (body: unknown) => {
      changed = body;
      return { body: { user_agents: [row({ agent_id: "docs", agent_name: "Docs agent" })] } };
    },
    "DELETE /api/jira/user-agents/ops-bot": () => ({ status: 204 }),
  });
  view();
  await user.click(await screen.findByRole("combobox", { name: "Agent for Ops Bot" }));
  await user.click(screen.getByRole("option", { name: "Docs agent" }));
  await waitFor(() => expect(changed).toEqual({ agent_id: "docs", name: "Ops Bot" }));
  expect(await screen.findByRole("combobox", { name: "Agent for Ops Bot" })).toHaveTextContent("Docs agent");
  await user.click(screen.getByRole("button", { name: "Remove Ops Bot" }));
  await waitFor(() => expect(screen.queryByText("Ops Bot")).toBeNull());
  expect(
    fetch.mock.calls.some(
      ([url, init]) => url === "/api/jira/user-agents/ops-bot" && (init as RequestInit)?.method === "DELETE",
    ),
  ).toBe(true);
});

test("a failed save shows the server's message", async () => {
  const user = userEvent.setup();
  api({
    "GET /api/jira/user-agents": { user_agents: [] },
    "GET /api/agents": AGENTS,
    "GET /api/jira/users": { users: [], warning: null },
    "PUT /api/jira/user-agents/x": () => ({ status: 400, body: { error: { message: "Choose an existing agent" } } }),
  });
  view();
  await user.type(await screen.findByLabelText("Jira user"), "x");
  await user.click(screen.getByRole("combobox", { name: "Agent" }));
  await user.click(screen.getByRole("option", { name: "Ops agent" }));
  await user.click(screen.getByRole("button", { name: "Add" }));
  expect(await screen.findByText("Choose an existing agent")).toBeInTheDocument();
});

const HANDLER = { id: "h-9", name: "Jira assignee agents", enabled: true };
test("shows the handler that runs the mapped agents with a link to it", async () => {
  api({ "GET /api/jira/user-agents": { user_agents: [row()], handler: HANDLER }, "GET /api/agents": AGENTS });
  view();
  expect(await screen.findByText(/Handled by:/)).toHaveTextContent("Handled by: Jira assignee agents (enabled)");
  expect(screen.getByRole("link", { name: "Jira assignee agents" })).toHaveAttribute(
    "href",
    "/events/handlers?handler=h-9",
  );
  expect(screen.queryByRole("button", { name: "Create handler" })).toBeNull();
});

test("a disabled handler says so; saving a mapping refreshes the handler line", async () => {
  const user = userEvent.setup();
  api({
    "GET /api/jira/user-agents": { user_agents: [], handler: { ...HANDLER, enabled: false } },
    "GET /api/agents": AGENTS,
    "GET /api/jira/users": { users: [], warning: null },
    "PUT /api/jira/user-agents/ops-bot": { user_agents: [row()], handler: HANDLER },
  });
  view();
  expect(await screen.findByText(/Handled by:/)).toHaveTextContent("Handled by: Jira assignee agents (disabled)");
  await user.type(screen.getByLabelText("Jira user"), "ops-bot");
  await user.click(screen.getByRole("combobox", { name: "Agent" }));
  await user.click(screen.getByRole("option", { name: "Ops agent" }));
  await user.click(screen.getByRole("button", { name: "Add" }));
  await waitFor(() =>
    expect(screen.getByText(/Handled by:/)).toHaveTextContent("Handled by: Jira assignee agents (enabled)"),
  );
});

test("without a handler, Create handler creates it", async () => {
  const user = userEvent.setup();
  api({
    "GET /api/jira/user-agents": { user_agents: [row()], handler: null },
    "GET /api/agents": AGENTS,
    "POST /api/jira/user-agents/handler": () => ({ status: 201, body: { handler: HANDLER } }),
  });
  view();
  expect(await screen.findByText("No handler runs these agents.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Create handler" }));
  expect(await screen.findByRole("link", { name: "Jira assignee agents" })).toBeInTheDocument();
  expect(screen.queryByText("No handler runs these agents.")).toBeNull();
});

test("a failed Create handler shows the server's message", async () => {
  const user = userEvent.setup();
  api({
    "GET /api/jira/user-agents": { user_agents: [], handler: null },
    "GET /api/agents": AGENTS,
    "POST /api/jira/user-agents/handler": () => ({
      status: 409,
      body: { error: { message: "A handler with this name already exists." } },
    }),
  });
  view();
  await user.click(await screen.findByRole("button", { name: "Create handler" }));
  expect(await screen.findByText("A handler with this name already exists.")).toBeInTheDocument();
  expect(screen.getByText("No handler runs these agents.")).toBeInTheDocument();
});
