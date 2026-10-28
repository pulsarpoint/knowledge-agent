import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { renderAgentApp, section, sectionNames } from "~/lib/__tests__/agentRoutes";
import AgentsView from "~/features/agents/AgentsView";
import * as profiles from "~/features/agents/api";
import * as llm from "~/features/llm/api";
import { Toaster } from "~/components/ui/sonner";

vi.mock("~/features/agents/api", async () => ({
  ...(await vi.importActual("~/features/agents/api")),
  getAgent: vi.fn(),
  getAgents: vi.fn(),
  getAgentOptions: vi.fn(),
  saveAgent: vi.fn(),
  saveProfileModel: vi.fn(),
  deleteAgent: vi.fn(),
}));
vi.mock("~/features/llm/api", async () => ({
  ...(await vi.importActual("~/features/llm/api")),
  getLlmSettings: vi.fn(),
}));
vi.mock("~/features/agents/AgentChatView", () => ({
  default: () => <div>Native test chat</div>,
}));
vi.mock("~/features/agents/AgentWizard", () => ({ default: () => <div>Agent wizard</div> }));

const writer: profiles.AgentProfile = {
  id: "writer",
  name: "Technical writer",
  description: "",
  when_to_use: "",
  enabled: true,
  revision: 1,
  model_id: "claude",
  system_prompt: "You are an expert technical writer.",
  instructions: "",
  access: { resources: [] },
};
const NO_COUNTS = {
  server: 0,
  service: 0,
  environment: 0,
  code_project: 0,
  document: 0,
  mcp_server: 0,
};
const connection = {
  base_url: null,
  aws_region: "eu-central-1",
  has_api_key: true,
  revision: 1,
  created_at: "",
  updated_at: "",
  enabled: true,
};
const claude = {
  ...connection,
  id: "claude",
  name: "Claude on Bedrock",
  provider: "bedrock",
  model: "eu.anthropic.claude-sonnet-4-5-20250929-v1:0",
  api_format: "native",
} as llm.ModelProfile;
const gpt = {
  ...connection,
  id: "gpt",
  name: "GPT",
  provider: "openai",
  model: "gpt-5",
  api_format: "responses",
} as llm.ModelProfile;

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ is_admin: true })),
  );
  vi.mocked(profiles.getAgent).mockImplementation(async (id) => ({
    ...structuredClone(writer),
    id,
    model_id: id === "search" ? "gpt" : "claude",
  }));
  vi.mocked(profiles.getAgentOptions).mockResolvedValue({
    server: [],
    service: [],
    environment: [],
    code_project: [],
    document: [],
    mcp_server: [],
  });
  vi.mocked(profiles.saveAgent).mockImplementation(async (value) => ({ ...value, revision: value.revision + 1 }));
  vi.mocked(llm.getLlmSettings).mockResolvedValue({
    models: [claude, gpt],
    assignments: { search_model_id: null, code_model_id: null, operations_model_id: null, revision: 1 },
  });
});
afterEach(() => vi.unstubAllGlobals());
const open = (path: string) =>
  renderAgentApp(path, {
    wrap: (outlet) => (
      <>
        {outlet}
        <Toaster />
      </>
    ),
  });
const TABS = ["General", "Resources", "Chat"];

test.each(["writer", "search"])("every agent shows the same tabs: %s", async (id) => {
  open(`/agents/${id}`);
  await screen.findByRole("heading", { name: "Technical writer", level: 2 });
  expect(sectionNames()).toEqual(TABS);
});

test.each(["ssh", "skills", "documents", "git", "mcp", "services", "nonsense"])(
  "a removed or unknown tab opens General: %s",
  async (view) => {
    open(`/agents/writer?view=${view}`);
    await screen.findByRole("textbox", { name: "Name" });
    expect(section("General")).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("textbox", { name: "Name" })).toBeVisible();
  },
);

test("General shows every compatible model", async () => {
  const user = userEvent.setup();
  open("/agents/search");
  expect(screen.queryByRole("combobox", { name: "Agent type" })).toBeNull();
  await user.click(await screen.findByRole("combobox", { name: "Model" }));
  expect(screen.getByRole("option", { name: "GPT" })).toBeVisible();
  expect(screen.getByRole("option", { name: "Claude on Bedrock" })).toBeVisible();
  expect(screen.queryByText("Save model")).toBeNull();
});

test("General saves the model with the details", async () => {
  const user = userEvent.setup();
  open("/agents/search");
  await user.click(await screen.findByRole("combobox", { name: "Model" }));
  await user.click(screen.getByRole("option", { name: "Claude on Bedrock" }));
  await user.click(screen.getByRole("button", { name: "Save details" }));
  await waitFor(() =>
    expect(profiles.saveAgent).toHaveBeenLastCalledWith(expect.objectContaining({ model_id: "claude" })),
  );
  expect(profiles.saveProfileModel).not.toHaveBeenCalled();
});

test("the Chat page is the native chat", async () => {
  open("/agents/search/chat");
  expect(await screen.findByText("Native test chat")).toBeInTheDocument();
  expect(section("Chat")).toHaveAttribute("aria-current", "page");
  expect(sectionNames()).not.toContain("Test");
  expect(screen.queryByRole("link", { name: "Start a session" })).toBeNull();
});

test("an old ?view=test link opens the Chat page", async () => {
  open("/agents/search?view=test");
  expect(await screen.findByText("Native test chat")).toBeInTheDocument();
  expect(section("Chat")).toHaveAttribute("aria-current", "page");
});

test("All agents has no type column and New agent opens one wizard", async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.getAgents).mockResolvedValue({
    agents: [
      { ...writer, id: "search", name: "Search agent", counts: { ...NO_COUNTS, document: 1 }, model_name: "GPT" },
      { ...writer, counts: NO_COUNTS, model_name: "Claude on Bedrock" },
    ],
  });
  render(
    <MemoryRouter initialEntries={["/agents"]}>
      <Routes>
        <Route path="/agents" element={<AgentsView />} />
      </Routes>
    </MemoryRouter>,
  );
  const row = (await screen.findByRole("link", { name: "Technical writer" })).closest("tr")!;
  expect(screen.queryByRole("columnheader", { name: "Agent type" })).toBeNull();
  expect(within(row).getByText("Claude on Bedrock")).toBeVisible();
  const other = screen.getByRole("link", { name: "Search agent" }).closest("tr")!;
  expect(within(other).getByText("1 document")).toBeVisible();
  await user.type(screen.getByRole("searchbox", { name: "Search agents" }), "technical");
  expect(screen.queryByRole("link", { name: "Search agent" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "New agent" }));
  expect(screen.getByText("Agent wizard")).toBeVisible();
  expect(screen.queryByRole("tab")).toBeNull();
});

function agentsList() {
  render(
    <MemoryRouter initialEntries={["/agents"]}>
      <Routes>
        <Route path="/agents" element={<AgentsView />} />
      </Routes>
    </MemoryRouter>,
  );
}
const agentNames = () => screen.getAllByRole("link").map((link) => link.textContent);

test("the agent list: Skeleton rows while loading, then an empty state with New agent", async () => {
  let resolve: (value: { agents: profiles.AgentSummary[] }) => void = () => {};
  vi.mocked(profiles.getAgents).mockReturnValue(new Promise((done) => (resolve = done)));
  agentsList();
  expect(screen.getAllByTestId("skeleton-row").length).toBeGreaterThan(0);
  resolve({ agents: [] });
  expect(await screen.findByText("No agents yet")).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "New agent" })).toHaveLength(2);
});

test("the agent list sorts by a header, pages 20 rows and searches from page 1", async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.getAgents).mockResolvedValue({
    agents: Array.from({ length: 25 }, (_, i) => ({
      ...writer,
      id: `a${i}`,
      name: `Agent ${String(i).padStart(2, "0")}`,
      counts: NO_COUNTS,
      model_name: i % 2 ? "Zeta" : "Alpha",
    })),
  });
  agentsList();
  await screen.findByRole("link", { name: "Agent 00" });
  expect(agentNames()).toHaveLength(20);
  expect(screen.getByText("Showing 1–20 of 25")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Agent" }));
  expect(agentNames()[0]).toBe("Agent 24");
  await user.click(screen.getByRole("button", { name: "Next page" }));
  expect(agentNames()).toHaveLength(5);
  await user.type(screen.getByRole("searchbox", { name: "Search agents" }), "Agent 1");
  expect(screen.getByText("Page 1 of 1")).toBeInTheDocument();
  expect(agentNames()).toHaveLength(10);
});

test("General has no role switch and saves Instructions", async () => {
  const user = userEvent.setup();
  open("/agents/search");
  await screen.findByRole("textbox", { name: "Instructions" });
  expect(screen.queryByRole("switch", { name: "Answer only from documents" })).toBeNull();
  expect(screen.queryByRole("switch", { name: /editor/i })).toBeNull();
  expect(screen.queryByRole("combobox", { name: "Resources" })).toBeNull();
  await user.type(screen.getByRole("textbox", { name: "Instructions" }), "Check the runbook first.");
  await user.click(screen.getByRole("button", { name: "Save details" }));
  await waitFor(() =>
    expect(profiles.saveAgent).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "search",
        instructions: "Check the runbook first.",
        access: { resources: [] },
      }),
    ),
  );
});

test("General has no inventory role: every agent writes inventory drafts", async () => {
  open("/agents/search");
  await screen.findByRole("textbox", { name: "Instructions" });
  const switches = screen.getAllByRole("switch").map((item) => item.getAttribute("id"));
  expect(switches).not.toContain("agent-inventory-editor");
  expect(screen.queryByText(/add and change servers, services/)).not.toBeInTheDocument();
});

test("General saves with the resources saved on the Resources page", async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.getAgentOptions).mockResolvedValue({
    server: [{ type: "server", id: "s1", name: "web-1", description_short: "Web" }],
    service: [],
    environment: [],
    code_project: [],
    document: [],
    mcp_server: [],
  });
  open("/agents/search/resources");
  await user.click(await screen.findByRole("switch", { name: "web-1" }));
  await user.click(screen.getByRole("button", { name: "Save resources" }));
  await waitFor(() => expect(profiles.saveAgent).toHaveBeenCalledTimes(1));
  await user.click(section("General"));
  await user.type(await screen.findByRole("textbox", { name: "Instructions" }), "x");
  await user.click(screen.getByRole("button", { name: "Save details" }));
  await waitFor(() => expect(profiles.saveAgent).toHaveBeenCalledTimes(2));
  expect(vi.mocked(profiles.saveAgent).mock.calls[1][0]).toEqual(
    expect.objectContaining({
      revision: 2,
      instructions: "x",
      access: expect.objectContaining({ resources: [expect.objectContaining({ type: "server", id: "s1" })] }),
    }),
  );
});

test("All agents shows how many resources of each type an agent has", async () => {
  vi.mocked(profiles.getAgents).mockResolvedValue({
    agents: [
      {
        ...writer,
        id: "search",
        name: "Search agent",
        counts: { ...NO_COUNTS, server: 1, document: 2, mcp_server: 2 },
        model_name: "GPT",
      },
      { ...writer, counts: NO_COUNTS, model_name: "Claude on Bedrock" },
    ],
  });
  render(
    <MemoryRouter initialEntries={["/agents"]}>
      <Routes>
        <Route path="/agents" element={<AgentsView />} />
      </Routes>
    </MemoryRouter>,
  );
  const row = (await screen.findByRole("link", { name: "Technical writer" })).closest("tr")!;
  expect(screen.getByRole("columnheader", { name: "Resources" })).toBeVisible();
  expect(screen.queryByRole("columnheader", { name: "Capabilities" })).toBeNull();
  expect(within(row).getByText("No resources")).toBeVisible();
  expect(
    within(screen.getByRole("link", { name: "Search agent" }).closest("tr")!).getByText(
      "1 server · 2 documents · 2 MCP servers",
    ),
  ).toBeVisible();
});

test("General has Tools under Advanced and saves the tools switched off", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url === "/api/tools"
        ? Response.json({
            tools: [
              { name: "search_documents", group: "documents", description: "Search the documents." },
              { name: "ssh_run", group: "servers", description: "Run a command over SSH." },
            ],
          })
        : Response.json({ is_admin: true }),
    ),
  );
  vi.mocked(profiles.getAgent).mockResolvedValue({ ...structuredClone(writer), disabled_tools: ["ssh_run"] });
  open("/agents/writer");
  await screen.findByRole("textbox", { name: "Name" });
  expect(screen.queryByRole("checkbox", { name: "ssh_run" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Advanced" }));
  expect(await screen.findByText("1 of 2 tools on")).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "ssh_run" })).not.toBeChecked();
  await user.click(screen.getByRole("checkbox", { name: "search_documents" }));
  await user.click(screen.getByRole("button", { name: "Save details" }));
  await waitFor(() =>
    expect(profiles.saveAgent).toHaveBeenLastCalledWith(
      expect.objectContaining({ disabled_tools: ["ssh_run", "search_documents"] }),
    ),
  );
});
