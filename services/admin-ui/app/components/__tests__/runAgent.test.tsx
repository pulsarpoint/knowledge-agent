import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation, useParams } from "react-router";
import AgentsView from "~/features/agents/AgentsView";
import * as profiles from "~/features/agents/api";

vi.mock("~/features/agents/api", async () => ({
  ...(await vi.importActual("~/features/agents/api")),
  getAgents: vi.fn(),
}));
vi.mock("~/features/agents/AgentWizard", () => ({ default: () => null }));

const counts = {
  server: 1,
  service: 0,
  environment: 0,
  code_project: 0,
  document: 0,
  mcp_server: 0,
};
const summary = (id: string, name: string, enabled = true): profiles.AgentSummary => ({
  id,
  name,
  description: "",
  when_to_use: "",
  enabled,
  revision: 3,
  model_id: "m1",
  system_prompt: "",
  instructions: "",
  access: { resources: [] },
  cloned_from: null,
  original: null,
  counts,
  model_name: "GPT",
  original_name: null,
});

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(profiles.getAgents).mockResolvedValue({
    agents: [summary("ops", "Operations"), summary("triage", "Triage"), summary("old", "Old agent", false)],
  });
});

function Agent() {
  const { search } = useLocation();
  return (
    <p>
      Agent {useParams().id}
      {search}
    </p>
  );
}
const open = () =>
  render(
    <MemoryRouter initialEntries={["/agents"]}>
      <Routes>
        <Route path="/agents" element={<AgentsView />} />
        <Route path="/agents/:id" element={<Agent />} />
      </Routes>
    </MemoryRouter>,
  );

test("Run on a row opens the agent's Chat tab", async () => {
  open();
  fireEvent.click(await screen.findByRole("button", { name: "Run Operations" }));
  expect(await screen.findByText("Agent ops?view=chat")).toBeInTheDocument();
});

test("Run agent in the top bar lists the enabled agents in a sheet and opens the chosen one's Chat tab", async () => {
  open();
  expect(await screen.findByRole("button", { name: "Run Old agent" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Run agent" }));
  const sheet = await screen.findByRole("dialog", { name: "Run agent" });
  expect(
    within(sheet)
      .getAllByRole("button", { name: /^Chat with / })
      .map((item) => item.textContent),
  ).toEqual(["Operations", "Triage"]);
  fireEvent.click(within(sheet).getByRole("button", { name: "Chat with Triage" }));
  expect(await screen.findByText("Agent triage?view=chat")).toBeInTheDocument();
});
