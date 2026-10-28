import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AgentsView from "~/features/agents/AgentsView";
import { renderAgentApp, section, sectionNames } from "~/lib/__tests__/agentRoutes";
import AdminLayout from "../AdminLayout";
import * as llm from "~/features/llm/api";
import * as profiles from "~/features/agents/api";
import { menuItems, rowAction, rowButtons } from "~/lib/__tests__/rowMenu";

vi.mock("~/features/agents/api", async () => ({
  ...(await vi.importActual("~/features/agents/api")),
  getAgents: vi.fn(),
  getAgent: vi.fn(),
  getAgentOptions: vi.fn(),
  saveAgent: vi.fn(),
  deleteAgent: vi.fn(),
  getAgentHistory: vi.fn(),
}));
vi.mock("~/features/agents/wizard", async () => ({
  ...(await vi.importActual("~/features/agents/wizard")),
  getWizardOptions: vi.fn(() => new Promise(() => {})),
}));
vi.mock("~/features/llm/mcp", () => ({ getMCPServers: vi.fn(async () => ({ servers: [] })) }));
vi.mock("../CodeSearchAgentView", () => ({
  default: ({ agentId }: { agentId: string }) => <div>Code test {agentId}</div>,
}));
vi.mock("~/features/agents/AgentChatView", () => ({
  default: ({ agentId }: { agentId: string }) => <span>SSH test {agentId}</span>,
}));
vi.mock("~/features/llm/api", async () => ({
  ...(await vi.importActual("~/features/llm/api")),
  getLlmSettings: vi.fn(),
}));
let agents: profiles.AgentSummary[];
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ is_admin: true }))),
  );
  vi.mocked(llm.getLlmSettings).mockResolvedValue({
    models: [],
    assignments: { search_model_id: null, code_model_id: null, operations_model_id: null, revision: 1 },
  });
  agents = ["search", "operations", "code", "custom-code"].map((id) => ({
    id,
    name: id === "custom-code" ? "Payments code" : llm.agentNames[id as llm.AgentName],
    description: "",
    when_to_use: "",
    enabled: true,
    revision: 1,
    model_id: null,
    system_prompt: `${id} instructions`,
    instructions: "",
    access: { resources: [] },
    counts: {
      server: 0,
      service: 0,
      environment: 0,
      code_project: 0,
      document: 0,
      mcp_server: 0,
    },
    model_name: null,
  }));
  vi.mocked(profiles.getAgents).mockImplementation(async () => ({ agents: structuredClone(agents) }));
  vi.mocked(profiles.getAgent).mockImplementation(async (id) => structuredClone(agents.find((a) => a.id === id)!));
  vi.mocked(profiles.getAgentOptions).mockResolvedValue({
    server: [],
    service: [],
    environment: [],
    code_project: [],
    document: [],
    mcp_server: [],
  });
  vi.mocked(profiles.getAgentHistory).mockResolvedValue({
    conversations: 0,
    documentation_sessions: 0,
    run_records: 0,
  });
});
afterEach(() => vi.unstubAllGlobals());
const app = (path: string) =>
  renderAgentApp(path, {
    routes: [{ path: "/agents", element: <AgentsView /> }],
    wrap: (outlet) => <AdminLayout>{outlet}</AdminLayout>,
  });

test("catalog opens distinct agents with one model field; the sections are sub-pages", async () => {
  const user = userEvent.setup();
  app("/agents");
  await user.click(await screen.findByRole("link", { name: "SSH agent" }));
  await screen.findByRole("combobox", { name: "Model" });
  expect(screen.getByRole("switch", { name: "Enable agent" })).toBeEnabled();
  expect(screen.getAllByRole("combobox", { name: "Model", hidden: true })).toHaveLength(1);
  expect(screen.queryByRole("button", { name: "Save model", hidden: true })).toBeNull(); // saved with the details
  await user.click(section("Chat"));
  expect(await screen.findByText("SSH test operations")).toBeVisible();
  await user.click(section("General"));
  expect(await screen.findByRole("textbox", { name: "System prompt" })).toHaveValue("operations instructions");
  await user.click(
    within(screen.getByRole("region", { name: "Agent details" })).getByRole("link", { name: "All agents" }),
  );
  await user.click(await screen.findByRole("link", { name: "Payments code" }));
  await screen.findByRole("combobox", { name: "Model" });
  expect(screen.getAllByRole("combobox", { name: "Model", hidden: true })).toHaveLength(1);
  await user.click(section("Chat"));
  expect(await screen.findByText("SSH test custom-code")).toBeVisible();
});

test("agents are created only through one New agent sheet with the wizard", async () => {
  app("/agents");
  const button = await screen.findByRole("button", { name: "New agent" });
  expect(screen.queryByRole("button", { name: "New Agent Wizard" })).toBeNull();
  expect(screen.queryByLabelText("System prompt")).toBeNull();
  fireEvent.click(button);
  const panel = await screen.findByRole("dialog", { name: "New agent" });
  expect(within(panel).queryByRole("tab")).toBeNull();
  expect(within(panel).getByRole("button", { name: /Generate proposal/ })).toBeInTheDocument();
});

test.each(["search", "operations", "code", "custom-code"])("every agent has the same tabs: %s", async (id) => {
  app(`/agents/${id}`);
  await screen.findByRole("combobox", { name: "Model" });
  expect(sectionNames()).toEqual(["General", "Resources", "Chat"]);
});

test("an unknown view such as the old Tools tab opens General", async () => {
  app("/agents/custom-code?view=tools");
  await screen.findByRole("combobox", { name: "Model" });
  expect(section("General")).toHaveAttribute("aria-current", "page");
  expect(sectionNames()).not.toContain("Tools");
});

test("the agents list has no Tools column", async () => {
  app("/agents");
  await screen.findByRole("link", { name: "Payments code" });
  expect(screen.queryByRole("columnheader", { name: "Tools" })).toBeNull();
});

test("an agent row keeps Run inline and Clone and Delete in its menu; no Open beside the name link", async () => {
  const user = userEvent.setup();
  app("/agents");
  const row = (await screen.findByRole("link", { name: "Payments code" })).closest("tr")!;
  expect(rowButtons(row)).toEqual(["Run Payments code", "Actions for Payments code"]);
  expect(within(row).getByRole("button", { name: "Run Payments code" })).toHaveTextContent("Run");
  expect(within(row).queryByRole("link", { name: "Open Payments code" })).toBeNull();
  await user.click(within(row).getByRole("button", { name: "Actions for Payments code" }));
  expect(menuItems()).toEqual(["Clone", "Delete"]);
});

test("agent removal requires confirmation and preserves the record on conflicts", async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.deleteAgent)
    .mockRejectedValueOnce(new Error("Agent has retained run history."))
    .mockResolvedValue(undefined);
  app("/agents");
  await rowAction(user, "Payments code", "Delete");
  expect(screen.getByRole("alertdialog")).toHaveTextContent(
    "Shared servers, code projects, skills, models, and documents will be kept.",
  );
  expect(screen.getByRole("alertdialog")).toHaveTextContent("Delete “Payments code”?");
  expect(await within(screen.getByRole("alertdialog")).findByRole("button", { name: "Delete agent" })).toHaveClass(
    "bg-destructive",
  );
  expect(profiles.deleteAgent).not.toHaveBeenCalled();
  await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
  expect(profiles.deleteAgent).not.toHaveBeenCalled();
  await rowAction(user, "Payments code", "Delete");
  await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete agent" }));
  expect(await screen.findByText("Agent has retained run history.")).toBeVisible();
  expect(profiles.deleteAgent).toHaveBeenCalledWith("custom-code", 1, false);
  expect(screen.getByRole("alertdialog")).toBeVisible();
  await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete agent" }));
  expect(screen.queryByRole("button", { name: "Actions for Payments code" })).not.toBeInTheDocument();
  expect(await screen.findByText("Agent Payments code deleted")).toBeVisible();
});

test("an agent with history lists it and is deleted with it only on the destructive action", async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.getAgentHistory).mockResolvedValue({
    conversations: 2,
    documentation_sessions: 1,
    run_records: 3,
  });
  vi.mocked(profiles.deleteAgent).mockResolvedValue(undefined);
  app("/agents");
  await rowAction(user, "Payments code", "Delete");
  const dialog = screen.getByRole("alertdialog");
  expect(await within(dialog).findByText("2 conversations, 1 documentation session, 3 run records")).toBeVisible();
  expect(profiles.getAgentHistory).toHaveBeenCalledWith("custom-code");
  expect(dialog).toHaveTextContent("Saved documents are kept.");
  expect(within(dialog).queryByRole("button", { name: "Delete agent" })).toBeNull();
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(profiles.deleteAgent).not.toHaveBeenCalled();
  await rowAction(user, "Payments code", "Delete");
  await user.click(
    await within(screen.getByRole("alertdialog")).findByRole("button", { name: "Delete agent and history" }),
  );
  expect(profiles.deleteAgent).toHaveBeenCalledWith("custom-code", 1, true);
  expect(screen.queryByRole("button", { name: "Actions for Payments code" })).not.toBeInTheDocument();
  expect(await screen.findByText("Agent Payments code and its history deleted")).toBeVisible();
});

test("a delete refused because history appeared meanwhile shows that history", async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.getAgentHistory)
    .mockResolvedValueOnce({ conversations: 0, documentation_sessions: 0, run_records: 0 })
    .mockResolvedValue({ conversations: 1, documentation_sessions: 0, run_records: 1 });
  vi.mocked(profiles.deleteAgent).mockRejectedValueOnce(new Error("This agent has sessions or run history."));
  app("/agents");
  await rowAction(user, "Payments code", "Delete");
  await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete agent" }));
  expect(await within(screen.getByRole("alertdialog")).findByText("1 conversation, 1 run record")).toBeVisible();
  expect(
    within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete agent and history" }),
  ).toBeEnabled();
});

const WORKING =
  "This agent is working right now. Stop its documentation sessions and runs, or wait for them to finish, then delete it.";

test("a delete refused because the agent is working says so and keeps the dialog open", async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.getAgentHistory).mockResolvedValue({
    conversations: 0,
    documentation_sessions: 1,
    run_records: 0,
  });
  vi.mocked(profiles.deleteAgent).mockRejectedValueOnce(new Error(WORKING));
  app("/agents");
  await rowAction(user, "Payments code", "Delete");
  await user.click(
    await within(screen.getByRole("alertdialog")).findByRole("button", { name: "Delete agent and history" }),
  );
  expect(await within(screen.getByRole("alertdialog")).findByText(WORKING)).toBeVisible();
  expect(screen.getByRole("alertdialog")).toBeVisible();
  expect(screen.getByRole("button", { name: "Actions for Payments code", hidden: true })).toBeInTheDocument();
});

test("a working agent whose history appeared meanwhile keeps the working message next to the history", async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.getAgentHistory)
    .mockResolvedValueOnce({ conversations: 0, documentation_sessions: 0, run_records: 0 })
    .mockResolvedValue({ conversations: 0, documentation_sessions: 1, run_records: 0 });
  vi.mocked(profiles.deleteAgent).mockRejectedValueOnce(new Error(WORKING));
  app("/agents");
  await rowAction(user, "Payments code", "Delete");
  await user.click(await within(screen.getByRole("alertdialog")).findByRole("button", { name: "Delete agent" }));
  const dialog = screen.getByRole("alertdialog");
  expect(await within(dialog).findByText("1 documentation session")).toBeVisible();
  expect(within(dialog).getByText(WORKING)).toBeVisible();
});
