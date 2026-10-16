import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useParams } from "react-router";
import AgentDetailView from "~/features/agents/AgentDetailView";
import AgentsView from "~/features/agents/AgentsView";
import * as profiles from "~/features/agents/api";
import * as tools from "~/features/agents/tools";
import * as llm from "~/features/llm/api";

vi.mock("~/features/agents/api", async () => ({
  ...(await vi.importActual("~/features/agents/api")),
  getAgent: vi.fn(),
  getAgents: vi.fn(),
  getAgentOptions: vi.fn(),
  saveAgent: vi.fn(),
}));
vi.mock("~/features/agents/tools", async () => ({
  ...(await vi.importActual("~/features/agents/tools")),
  getAgentTools: vi.fn(),
  saveAgentTools: vi.fn(),
}));
vi.mock("~/features/llm/api", async () => ({
  ...(await vi.importActual("~/features/llm/api")),
  getLlmSettings: vi.fn(),
}));
vi.mock("~/features/agents/AgentTestView", () => ({ default: () => null }));
vi.mock("~/features/agents/AgentWizard", () => ({ default: () => null }));

const agent: profiles.AgentProfile = {
  id: "caller",
  name: "Caller",
  description: "",
  when_to_use: "",
  enabled: true,
  documents_grounded: false,
  revision: 4,
  model_id: null,
  system_prompt: "",
  instructions: "",
  access: { resources: [], roles: { content_editor: false } },
};
const expertRow: tools.AgentTool = {
  tool: "expert:p1",
  label: "Payments",
  summary: "Payment questions.",
  group: "experts",
  mode: "on",
  usage: null,
  default_usage: "",
  subagent_allowed: false,
  available: true,
  reason: null,
  tab: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ is_admin: true })),
  );
  vi.mocked(profiles.getAgent).mockImplementation(async (id) => ({ ...structuredClone(agent), id }));
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
  vi.mocked(profiles.saveAgent).mockImplementation(async (draft) => ({ ...draft, revision: draft.revision + 1 }));
  vi.mocked(llm.getLlmSettings).mockResolvedValue({
    models: [],
    assignments: { search_model_id: null, code_model_id: null, operations_model_id: null, revision: 1 },
  });
  vi.mocked(tools.getAgentTools).mockImplementation(async () => ({ revision: 4, tools: [structuredClone(expertRow)] }));
  vi.mocked(tools.saveAgentTools).mockImplementation(async (_id, revision, saved) => ({
    revision: revision + 1,
    tools: saved,
  }));
});
afterEach(() => vi.unstubAllGlobals());
function Detail() {
  const { id } = useParams();
  return <AgentDetailView key={id} agentId={id!} />;
}
const open = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/agents" element={<AgentsView />} />
        <Route path="/agents/:id" element={<Detail />} />
      </Routes>
    </MemoryRouter>,
  );

test("Offer as an expert needs When to use and is saved", async () => {
  const user = userEvent.setup();
  open("/agents/caller");
  const toggle = await screen.findByRole("switch", { name: "Offer as an expert" });
  expect(
    screen.getByText("Other agents can call this agent as a tool. Its When to use text tells them when."),
  ).toBeVisible();
  await user.click(toggle);
  expect(screen.getByText("Experts need When to use.")).toBeVisible();
  expect(screen.getByRole("button", { name: "Save details" })).toBeDisabled();
  await user.type(screen.getByLabelText("When to use this agent"), "Payment questions");
  expect(screen.queryByText("Experts need When to use.")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Save details" }));
  expect(profiles.saveAgent).toHaveBeenLastCalledWith(expect.objectContaining({ id: "caller", expert: true }));
});

test("the Tools tab lists experts and saves them off", async () => {
  const user = userEvent.setup();
  open("/agents/caller?view=tools");
  const group = await screen.findByRole("group", { name: "Experts" });
  expect(within(group).getByText("Payment questions.")).toBeVisible();
  expect(within(group).queryByText("Per run")).toBeNull();
  expect(within(group).queryByRole("button", { name: /Edit usage/ })).toBeNull();
  expect(screen.queryByText(tools.EXPERT_SELF_NOTE)).toBeNull();
  await user.click(within(group).getByRole("switch", { name: "Payments" }));
  await user.click(screen.getByRole("button", { name: "Save tools" }));
  expect(tools.saveAgentTools).toHaveBeenCalledOnce();
  const sent = vi.mocked(tools.saveAgentTools).mock.calls[0][2].find((item) => item.tool === "expert:p1")!;
  expect(sent).toMatchObject({ mode: "off", usage: null, subagent_allowed: false });
});

test("the Experts group shows the empty and the self notes", async () => {
  vi.mocked(tools.getAgentTools).mockResolvedValue({ revision: 4, expert: true, tools: [] });
  open("/agents/caller?view=tools");
  const group = await screen.findByRole("group", { name: "Experts" });
  expect(
    within(group).getByText("No experts yet. Turn on Offer as an expert on an agent's General tab."),
  ).toBeVisible();
  expect(within(group).getByText("When this agent runs as an expert it cannot call experts.")).toBeVisible();
});

test("All agents and the agent header show the Expert badge", async () => {
  vi.mocked(profiles.getAgents).mockResolvedValue({
    agents: [
      {
        ...agent,
        model_name: null,
        expert: true,
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
      },
      {
        ...agent,
        id: "other",
        name: "Other",
        model_name: null,
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
      },
    ],
  });
  const { unmount } = open("/agents");
  const rowOf = (await screen.findByRole("link", { name: "Caller" })).closest("tr")!;
  expect(within(rowOf).getByText("Expert")).toBeVisible();
  expect(within(screen.getByRole("link", { name: "Other" }).closest("tr")!).queryByText("Expert")).toBeNull();
  unmount();
  vi.mocked(profiles.getAgent).mockResolvedValue({ ...agent, expert: true });
  open("/agents/caller");
  expect(await screen.findByText("Expert")).toBeVisible();
});
