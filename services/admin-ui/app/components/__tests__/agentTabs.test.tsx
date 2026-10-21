import { useState } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useParams } from "react-router";
import AgentsView from "~/features/agents/AgentsView";
import AgentDetailView from "~/features/agents/AgentDetailView";
import AdminLayout from "../AdminLayout";
import * as llm from "~/features/llm/api";
import * as profiles from "~/features/agents/api";

vi.mock("~/features/agents/api", async () => ({
  ...(await vi.importActual("~/features/agents/api")),
  getAgents: vi.fn(),
  getAgent: vi.fn(),
  getAgentOptions: vi.fn(),
  saveAgent: vi.fn(),
  deleteAgent: vi.fn(),
  getAgentHistory: vi.fn(),
}));
vi.mock("~/features/llm/mcp", () => ({ getMCPServers: vi.fn(async () => ({ servers: [] })) }));
vi.mock("../CodeSearchAgentView", () => ({
  default: ({ agentId }: { agentId: string }) => <div>Code test {agentId}</div>,
}));
vi.mock("~/features/agents/AgentTestView", () => ({
  default: ({ active, agentId }: { active: boolean; agentId: string }) => {
    const [task, setTask] = useState("");
    return (
      <div hidden={!active}>
        <span>SSH test {agentId}</span>
        <label>
          SSH task
          <input value={task} onChange={(event) => setTask(event.target.value)} />
        </label>
      </div>
    );
  },
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
    documents_grounded: false,
    name: id === "custom-code" ? "Payments code" : llm.agentNames[id as llm.AgentName],
    description: "",
    when_to_use: "",
    enabled: true,
    revision: 1,
    model_id: null,
    system_prompt: `${id} instructions`,
    instructions: "",
    access: { resources: [], roles: { content_editor: false } },
    counts: {
      server: 0,
      service: 0,
      environment: 0,
      code_project: 0,
      collection: 0,
      document: 0,
      skill: 0,
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
    collection: [],
    document: [],
    skill: [],
    mcp_server: [],
  });
  vi.mocked(profiles.getAgentHistory).mockResolvedValue({
    test_chats: 0,
    chats: 0,
    documentation_sessions: 0,
    run_records: 0,
  });
});
function Detail() {
  const { id } = useParams();
  return <AgentDetailView key={id} agentId={id!} />;
}
function app(path: string) {
  return (
    <MemoryRouter initialEntries={[path]}>
      <AdminLayout>
        <Routes>
          <Route path="/agents" element={<AgentsView />} />
          <Route path="/agents/:id" element={<Detail />} />
        </Routes>
      </AdminLayout>
    </MemoryRouter>
  );
}

test("catalog opens distinct agents with one model field and preserves SSH tasks across sections", async () => {
  const user = userEvent.setup();
  render(app("/agents"));
  await user.click(await screen.findByRole("link", { name: "SSH agent" }));
  await screen.findByRole("combobox", { name: "Model" });
  expect(screen.getByRole("switch", { name: "Enable agent" })).toBeEnabled();
  expect(screen.getAllByRole("combobox", { name: "Model", hidden: true })).toHaveLength(1);
  expect(screen.queryByRole("button", { name: "Save model", hidden: true })).toBeNull(); // saved with the details
  await user.click(screen.getByRole("tab", { name: "Test" }));
  await user.type(screen.getByRole("textbox", { name: "SSH task" }), "Inspect disk usage");
  await user.click(screen.getByRole("tab", { name: "General" }));
  await user.click(screen.getByRole("tab", { name: "Test" }));
  expect(screen.getByRole("textbox", { name: "SSH task" })).toHaveValue("Inspect disk usage");
  await user.click(screen.getByRole("tab", { name: "General" }));
  expect(await screen.findByRole("textbox", { name: "System prompt" })).toHaveValue("operations instructions");
  await user.click(
    within(screen.getByRole("region", { name: "Agent details" })).getByRole("link", { name: "All agents" }),
  );
  await user.click(await screen.findByRole("link", { name: "Payments code" }));
  await screen.findByRole("combobox", { name: "Model" });
  expect(screen.getAllByRole("combobox", { name: "Model", hidden: true })).toHaveLength(1);
  await user.click(screen.getByRole("tab", { name: "Test" }));
  expect(screen.getByText("SSH test custom-code")).toBeVisible();
});

test("agents are created only through one New agent panel with the wizard", async () => {
  render(app("/agents"));
  const button = await screen.findByRole("button", { name: "New agent" });
  expect(screen.queryByRole("button", { name: "New Agent Wizard" })).toBeNull();
  expect(screen.queryByLabelText("System prompt")).toBeNull();
  fireEvent.click(button);
  const panel = screen.getByRole("region", { name: "New agent" });
  expect(within(panel).queryByRole("tab")).toBeNull();
  expect(within(panel).getByRole("button", { name: /Generate proposal/ })).toBeInTheDocument();
});

test.each(["search", "operations", "code", "custom-code"])("every agent has the same tabs: %s", async (id) => {
  render(app(`/agents/${id}`));
  await screen.findByRole("combobox", { name: "Model" });
  expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["General", "Resources", "Test"]);
});

test("an unknown view such as the old Tools tab opens General", async () => {
  render(app("/agents/custom-code?view=tools"));
  await screen.findByRole("combobox", { name: "Model" });
  expect(screen.getByRole("tab", { name: "General" })).toHaveAttribute("aria-selected", "true");
  expect(screen.queryByRole("tab", { name: "Tools" })).toBeNull();
});

test("the agents list has no Tools column", async () => {
  render(app("/agents"));
  await screen.findByRole("link", { name: "Payments code" });
  expect(screen.queryByRole("columnheader", { name: "Tools" })).toBeNull();
});

test("agent removal requires confirmation and preserves the record on conflicts", async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.deleteAgent)
    .mockRejectedValueOnce(new Error("Agent has retained run history."))
    .mockResolvedValue(undefined);
  render(app("/agents"));
  await user.click(await screen.findByRole("button", { name: "Remove Payments code" }));
  expect(screen.getByRole("alertdialog")).toHaveTextContent(
    "Shared servers, code projects, skills, models, and documents will be kept.",
  );
  expect(profiles.deleteAgent).not.toHaveBeenCalled();
  await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
  expect(profiles.deleteAgent).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Remove Payments code" }));
  await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove agent" }));
  expect(await screen.findByText("Agent has retained run history.")).toBeVisible();
  expect(profiles.deleteAgent).toHaveBeenCalledWith("custom-code", 1, false);
  expect(screen.getByRole("alertdialog")).toBeVisible();
  await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove agent" }));
  expect(screen.queryByRole("button", { name: "Remove Payments code" })).not.toBeInTheDocument();
  expect(await screen.findByText("Agent Payments code removed")).toBeVisible();
});

test("an agent with history lists it and is deleted with it only on the destructive action", async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.getAgentHistory).mockResolvedValue({
    test_chats: 2,
    chats: 0,
    documentation_sessions: 1,
    run_records: 3,
  });
  vi.mocked(profiles.deleteAgent).mockResolvedValue(undefined);
  render(app("/agents"));
  await user.click(await screen.findByRole("button", { name: "Remove Payments code" }));
  const dialog = screen.getByRole("alertdialog");
  expect(await within(dialog).findByText("2 test chats, 1 documentation session, 3 run records")).toBeVisible();
  expect(profiles.getAgentHistory).toHaveBeenCalledWith("custom-code");
  expect(dialog).toHaveTextContent("Saved documents are kept.");
  expect(within(dialog).queryByRole("button", { name: "Remove agent" })).toBeNull();
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(profiles.deleteAgent).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Remove Payments code" }));
  await user.click(
    await within(screen.getByRole("alertdialog")).findByRole("button", { name: "Delete agent and history" }),
  );
  expect(profiles.deleteAgent).toHaveBeenCalledWith("custom-code", 1, true);
  expect(screen.queryByRole("button", { name: "Remove Payments code" })).not.toBeInTheDocument();
  expect(await screen.findByText("Agent Payments code and its history deleted")).toBeVisible();
});

test("a delete refused because history appeared meanwhile shows that history", async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.getAgentHistory)
    .mockResolvedValueOnce({ test_chats: 0, chats: 0, documentation_sessions: 0, run_records: 0 })
    .mockResolvedValue({ test_chats: 1, chats: 0, documentation_sessions: 0, run_records: 1 });
  vi.mocked(profiles.deleteAgent).mockRejectedValueOnce(new Error("This agent has sessions or run history."));
  render(app("/agents"));
  await user.click(await screen.findByRole("button", { name: "Remove Payments code" }));
  await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove agent" }));
  expect(await within(screen.getByRole("alertdialog")).findByText("1 test chat, 1 run record")).toBeVisible();
  expect(
    within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete agent and history" }),
  ).toBeEnabled();
});

const WORKING =
  "This agent is working right now. Stop its documentation sessions and runs, or wait for them to finish, then delete it.";

test("a delete refused because the agent is working says so and keeps the dialog open", async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.getAgentHistory).mockResolvedValue({
    test_chats: 0,
    chats: 0,
    documentation_sessions: 1,
    run_records: 0,
  });
  vi.mocked(profiles.deleteAgent).mockRejectedValueOnce(new Error(WORKING));
  render(app("/agents"));
  await user.click(await screen.findByRole("button", { name: "Remove Payments code" }));
  await user.click(
    await within(screen.getByRole("alertdialog")).findByRole("button", { name: "Delete agent and history" }),
  );
  expect(await within(screen.getByRole("alertdialog")).findByText(WORKING)).toBeVisible();
  expect(screen.getByRole("alertdialog")).toBeVisible();
  expect(screen.getByRole("button", { name: "Remove Payments code", hidden: true })).toBeInTheDocument();
});

test("a working agent whose history appeared meanwhile keeps the working message next to the history", async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.getAgentHistory)
    .mockResolvedValueOnce({ test_chats: 0, chats: 0, documentation_sessions: 0, run_records: 0 })
    .mockResolvedValue({ test_chats: 0, chats: 0, documentation_sessions: 1, run_records: 0 });
  vi.mocked(profiles.deleteAgent).mockRejectedValueOnce(new Error(WORKING));
  render(app("/agents"));
  await user.click(await screen.findByRole("button", { name: "Remove Payments code" }));
  await user.click(await within(screen.getByRole("alertdialog")).findByRole("button", { name: "Remove agent" }));
  const dialog = screen.getByRole("alertdialog");
  expect(await within(dialog).findByText("1 documentation session")).toBeVisible();
  expect(within(dialog).getByText(WORKING)).toBeVisible();
});
