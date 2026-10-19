import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
  render(<JiraUserAgents />);
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
  render(<JiraUserAgents />);
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
  render(<JiraUserAgents />);
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
  render(<JiraUserAgents />);
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
  render(<JiraUserAgents />);
  await user.type(await screen.findByLabelText("Jira user"), "x");
  await user.click(screen.getByRole("combobox", { name: "Agent" }));
  await user.click(screen.getByRole("option", { name: "Ops agent" }));
  await user.click(screen.getByRole("button", { name: "Add" }));
  expect(await screen.findByText("Choose an existing agent")).toBeInTheDocument();
});
