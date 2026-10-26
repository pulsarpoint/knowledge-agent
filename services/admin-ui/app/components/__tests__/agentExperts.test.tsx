import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderAgentApp } from "~/lib/__tests__/agentRoutes";
import AgentsView from "~/features/agents/AgentsView";
import * as profiles from "~/features/agents/api";
import * as llm from "~/features/llm/api";

vi.mock("~/features/agents/api", async () => ({
  ...(await vi.importActual("~/features/agents/api")),
  getAgent: vi.fn(),
  getAgents: vi.fn(),
  getAgentOptions: vi.fn(),
  saveAgent: vi.fn(),
}));
vi.mock("~/features/llm/api", async () => ({
  ...(await vi.importActual("~/features/llm/api")),
  getLlmSettings: vi.fn(),
}));
vi.mock("~/features/agents/AgentChatView", () => ({ default: () => null }));
vi.mock("~/features/agents/AgentWizard", () => ({ default: () => null }));

const agent: profiles.AgentProfile = {
  id: "caller",
  name: "Caller",
  description: "",
  when_to_use: "",
  enabled: true,
  revision: 4,
  model_id: null,
  system_prompt: "",
  instructions: "",
  access: { resources: [] },
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
    document: [],
    mcp_server: [],
  });
  vi.mocked(profiles.saveAgent).mockImplementation(async (draft) => ({ ...draft, revision: draft.revision + 1 }));
  vi.mocked(llm.getLlmSettings).mockResolvedValue({
    models: [],
    assignments: { search_model_id: null, code_model_id: null, operations_model_id: null, revision: 1 },
  });
});
afterEach(() => vi.unstubAllGlobals());
const open = (path: string) => renderAgentApp(path, { routes: [{ path: "/agents", element: <AgentsView /> }] });

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

test("General has no May start subagents switch: every saved agent may start subagents", async () => {
  open("/agents/caller");
  await screen.findByRole("switch", { name: "Offer as an expert" });
  expect(screen.queryByRole("switch", { name: "May start subagents" })).toBeNull();
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
          document: 0,
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
          document: 0,
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
