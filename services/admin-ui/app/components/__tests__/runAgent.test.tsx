import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useParams } from "react-router";
import AgentsView from "~/features/agents/AgentsView";
import * as profiles from "~/features/agents/api";
import * as sessions from "~/features/sessions/api";
import * as outputSchemas from "~/features/outputSchemas/api";
import { agentOptions } from "~/lib/__tests__/agentScopeFixture";

vi.mock("~/features/agents/api", async () => ({
  ...(await vi.importActual("~/features/agents/api")),
  getAgents: vi.fn(),
}));
vi.mock("~/features/sessions/api", async () => ({
  ...(await vi.importActual("~/features/sessions/api")),
  getSessionOptions: vi.fn(),
  startSession: vi.fn(),
}));
vi.mock("~/features/agents/AgentWizard", () => ({ default: () => null }));
vi.mock("~/features/outputSchemas/api", async () => ({
  ...(await vi.importActual("~/features/outputSchemas/api")),
  getOutputSchemas: vi.fn(),
}));

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
  localStorage.clear();
  vi.mocked(profiles.getAgents).mockResolvedValue({
    agents: [summary("ops", "Operations"), summary("triage", "Triage"), summary("old", "Old agent", false)],
  });
  vi.mocked(sessions.getSessionOptions).mockImplementation(async ({ agentId }) => ({
    ...structuredClone(agentOptions),
    agent: { ...agentOptions.agent!, id: agentId!, name: agentId === "ops" ? "Operations" : "Triage" },
    resources: {
      ...agentOptions.resources,
      servers: [
        { id: "s1", name: "web-1", linked: false, usable: true, reason: null },
        { id: "s2", name: "db-1", linked: false, usable: true, reason: null },
      ],
    },
  }));
  vi.mocked(sessions.startSession).mockResolvedValue({ id: "sess-9" } as sessions.SessionDetail);
  vi.mocked(outputSchemas.getOutputSchemas).mockResolvedValue({
    schemas: [
      {
        id: "schema-1",
        name: "Verdict",
        description: "",
        current_version: 3,
        revision: 1,
        created_at: "",
        updated_at: "",
        used_by: [],
      },
    ],
  });
});

function Session() {
  return <p>Session {useParams().id}</p>;
}
const open = () =>
  render(
    <MemoryRouter initialEntries={["/agents"]}>
      <Routes>
        <Route path="/agents" element={<AgentsView />} />
        <Route path="/sessions/:id" element={<Session />} />
      </Routes>
    </MemoryRouter>,
  );

test("Run on a row opens the dialog with that agent; Start posts a chat and opens its session", async () => {
  open();
  fireEvent.click(await screen.findByRole("button", { name: "Run Operations" }));
  const dialog = await screen.findByRole("alertdialog", { name: "Run agent" });
  expect(within(dialog).getByRole("combobox", { name: "Agent" })).toHaveTextContent("Operations");
  await waitFor(() => expect(sessions.getSessionOptions).toHaveBeenCalledWith({ agentId: "ops" }, expect.anything()));
  expect(await within(dialog).findByText(/web-1, db-1/)).toBeInTheDocument(); // every resource by default
  const start = within(dialog).getByRole("button", { name: "Start" });
  expect(start).toBeDisabled(); // the first message is required
  fireEvent.change(within(dialog).getByRole("textbox", { name: "First message" }), {
    target: { value: "  Check the disk.  " },
  });
  // narrowed with the Test tab's settings sheet
  fireEvent.click(within(dialog).getByRole("button", { name: "Choose resources" }));
  fireEvent.click(await screen.findByRole("checkbox", { name: "db-1" }));
  fireEvent.click(screen.getByRole("button", { name: "Done" }));
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Start" }));
  await screen.findByText("Session sess-9");
  expect(sessions.startSession).toHaveBeenCalledWith(
    expect.objectContaining({
      agent_id: "ops",
      purpose: "chat",
      message: "Check the disk.",
      resources: expect.objectContaining({ server_ids: ["s1"], documents: [] }),
    }),
  );
});

test("Run agent in the top bar asks for an enabled agent first; a disabled agent's row has no Run", async () => {
  open();
  expect(await screen.findByRole("button", { name: "Run Old agent" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Run agent" }));
  const dialog = await screen.findByRole("alertdialog", { name: "Run agent" });
  fireEvent.change(within(dialog).getByRole("textbox", { name: "First message" }), { target: { value: "Hi" } });
  expect(within(dialog).getByRole("button", { name: "Start" })).toBeDisabled();
  expect(sessions.getSessionOptions).not.toHaveBeenCalled();
  fireEvent.click(within(dialog).getByRole("combobox", { name: "Agent" }));
  expect(screen.getAllByRole("option").map((item) => item.textContent)).toEqual(["Operations", "Triage"]);
  fireEvent.click(screen.getByRole("option", { name: "Triage" }));
  await waitFor(() => expect(within(dialog).getByRole("button", { name: "Start" })).toBeEnabled());
  fireEvent.click(within(dialog).getByRole("button", { name: "Start" }));
  await screen.findByText("Session sess-9");
  expect(sessions.startSession).toHaveBeenCalledWith(
    expect.objectContaining({ agent_id: "triage", purpose: "chat", message: "Hi" }),
  );
  expect(vi.mocked(sessions.startSession).mock.calls[0][0]).not.toHaveProperty("output_schema_id");
});

test("Run agent can start with an output schema, at the current version or a fixed one", async () => {
  open();
  fireEvent.click(await screen.findByRole("button", { name: "Run Operations" }));
  const dialog = await screen.findByRole("alertdialog", { name: "Run agent" });
  fireEvent.change(within(dialog).getByRole("textbox", { name: "First message" }), { target: { value: "Judge" } });
  fireEvent.click(within(dialog).getByRole("combobox", { name: "Output schema" }));
  fireEvent.click(await screen.findByRole("option", { name: "Verdict" }));
  fireEvent.click(within(dialog).getByRole("combobox", { name: "Schema version" }));
  fireEvent.click(await screen.findByRole("option", { name: "v2" }));
  await waitFor(() => expect(within(dialog).getByRole("button", { name: "Start" })).toBeEnabled());
  // the picker is the dialog's own: not in the Resources line, not in the resources sheet
  expect(within(dialog).getByText(/web-1, db-1/)).not.toHaveTextContent("Output");
  fireEvent.click(within(dialog).getByRole("button", { name: "Choose resources" }));
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).queryByRole("combobox", { name: "Output schema" })).toBeNull();
  fireEvent.click(within(sheet).getByRole("button", { name: "Done" }));
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Start" }));
  await screen.findByText("Session sess-9");
  expect(sessions.startSession).toHaveBeenCalledWith(
    expect.objectContaining({
      agent_id: "ops",
      purpose: "chat",
      output_schema_id: "schema-1",
      output_schema_version: 2,
    }),
  );
});

test("a refused start keeps the dialog open with the reason", async () => {
  vi.mocked(sessions.startSession).mockRejectedValue(new Error("This agent is disabled."));
  open();
  fireEvent.click(await screen.findByRole("button", { name: "Run Operations" }));
  const dialog = await screen.findByRole("alertdialog", { name: "Run agent" });
  fireEvent.change(within(dialog).getByRole("textbox", { name: "First message" }), { target: { value: "Hi" } });
  await waitFor(() => expect(within(dialog).getByRole("button", { name: "Start" })).toBeEnabled());
  fireEvent.click(within(dialog).getByRole("button", { name: "Start" }));
  expect(await within(dialog).findByText("This agent is disabled.")).toBeInTheDocument();
});
