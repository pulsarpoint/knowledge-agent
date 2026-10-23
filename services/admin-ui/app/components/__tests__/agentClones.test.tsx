import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useParams } from "react-router";
import AgentDetailView from "~/features/agents/AgentDetailView";
import AgentsView from "~/features/agents/AgentsView";
import * as profiles from "~/features/agents/api";
import * as clones from "~/features/agents/clones";
import * as llm from "~/features/llm/api";
import { Toaster } from "~/components/ui/sonner";
import { ApiError } from "../../lib/api";

vi.mock("~/features/agents/api", async () => ({
  ...(await vi.importActual("~/features/agents/api")),
  getAgent: vi.fn(),
  getAgents: vi.fn(),
  getAgentOptions: vi.fn(),
  deleteAgent: vi.fn(),
  getAgentHistory: vi.fn(),
}));
vi.mock("~/features/agents/clones");
vi.mock("~/features/llm/api", async () => ({
  ...(await vi.importActual("~/features/llm/api")),
  getLlmSettings: vi.fn(),
}));
vi.mock("~/features/agents/AgentTestView", () => ({ default: () => null }));
vi.mock("~/features/agents/AgentWizard", () => ({ default: () => null }));

const base: profiles.AgentProfile = {
  id: "operations",
  name: "SSH agent",
  description: "",
  when_to_use: "",
  enabled: true,
  revision: 3,
  model_id: null,
  system_prompt: "Operate servers.",
  instructions: "",
  access: { resources: [] },
  cloned_from: null,
  original: null,
};
const copy: profiles.AgentProfile = {
  ...base,
  id: "clone-1",
  name: "SSH agent (copy)",
  revision: 2,
  cloned_from: "operations",
  original: { id: "operations", name: "SSH agent" },
};
const preview: clones.ApplyPreview = {
  original: { id: "operations", name: "SSH agent", revision: 3 },
  fingerprint: "f".repeat(64),
  changes: ["Prompt", "Tools"],
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ is_admin: true })),
  );
  vi.mocked(profiles.getAgent).mockImplementation(async (id) =>
    structuredClone(id === "clone-1" ? copy : { ...base, id }),
  );
  vi.mocked(profiles.getAgents).mockResolvedValue({
    agents: [
      {
        ...structuredClone(base),
        model_name: null,
        original_name: null,
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
        ...structuredClone(copy),
        model_name: null,
        original_name: "SSH agent",
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
    test_chats: 2,
    chats: 0,
    documentation_sessions: 0,
    run_records: 0,
  });
  vi.mocked(llm.getLlmSettings).mockResolvedValue({
    models: [],
    assignments: { search_model_id: null, code_model_id: null, operations_model_id: null, revision: 1 },
  });
  vi.mocked(clones.cloneAgent).mockResolvedValue({ id: "clone-2", name: "SSH agent (copy 2)" });
  vi.mocked(clones.getApplyPreview).mockResolvedValue(structuredClone(preview));
  vi.mocked(clones.applyToOriginal).mockResolvedValue({ id: "operations", revision: 4 });
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
      <Toaster />
    </MemoryRouter>,
  );

test("Clone agent in the header menu opens the new clone", async () => {
  const user = userEvent.setup();
  open("/agents/operations");
  await user.click(await screen.findByRole("button", { name: "Actions for SSH agent" }));
  await user.click(await screen.findByRole("menuitem", { name: "Clone agent" }));
  expect(clones.cloneAgent).toHaveBeenCalledWith("operations");
  await waitFor(() => expect(profiles.getAgent).toHaveBeenLastCalledWith("clone-2"));
  expect(screen.queryByRole("button", { name: /^Apply to/ })).toBeNull(); // not a clone: nothing to apply to
});

test("a clone says what it is a clone of and applies to it after confirmation", async () => {
  const user = userEvent.setup();
  open("/agents/clone-1");
  expect(await screen.findByRole("link", { name: "SSH agent" })).toHaveAttribute("href", "/agents/operations");
  expect(screen.getByText(/Clone of/)).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Apply to SSH agent" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(
    await within(dialog).findByText(
      "These sections of SSH agent are replaced by this clone’s: Prompt, Tools. Its name and description stay.",
    ),
  ).toBeVisible();
  expect(clones.applyToOriginal).not.toHaveBeenCalled();
  await user.click(within(dialog).getByRole("button", { name: "Apply" }));
  await waitFor(() => expect(clones.applyToOriginal).toHaveBeenCalledWith("clone-1", preview));
  expect(await screen.findByText("Applied to SSH agent.")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Delete this clone" }));
  await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Delete clone" }));
  await waitFor(() => expect(profiles.deleteAgent).toHaveBeenCalledWith("clone-1", 2, true));
  await waitFor(() => expect(profiles.getAgent).toHaveBeenLastCalledWith("operations"));
});

test("a refused apply keeps the dialog open with the reason", async () => {
  const user = userEvent.setup();
  vi.mocked(clones.applyToOriginal).mockRejectedValue(new Error("The original changed; review again."));
  open("/agents/clone-1");
  await user.click(await screen.findByRole("button", { name: "Apply to SSH agent" }));
  const dialog = await screen.findByRole("alertdialog");
  await within(dialog).findByText(/These sections of SSH agent/);
  await user.click(within(dialog).getByRole("button", { name: "Apply" }));
  expect(await within(dialog).findByText("The original changed; review again.")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Delete this clone" })).toBeNull();
});

test("All agents shows clones and clones a row", async () => {
  const user = userEvent.setup();
  open("/agents");
  expect(await screen.findByText("Clone of SSH agent")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Clone SSH agent" }));
  expect(clones.cloneAgent).toHaveBeenCalledWith("operations");
  await waitFor(() => expect(profiles.getAgent).toHaveBeenLastCalledWith("clone-2"));
});

test("an original changed since the preview shows the reason and compares again", async () => {
  const user = userEvent.setup();
  vi.mocked(clones.applyToOriginal).mockRejectedValueOnce(new ApiError("The original changed; review again.", 409));
  vi.mocked(clones.getApplyPreview)
    .mockResolvedValueOnce(structuredClone(preview))
    .mockResolvedValueOnce({
      ...structuredClone(preview),
      original: { ...preview.original, revision: 4 },
      changes: ["Model"],
    });
  open("/agents/clone-1");
  await user.click(await screen.findByRole("button", { name: "Apply to SSH agent" }));
  const dialog = await screen.findByRole("alertdialog");
  await within(dialog).findByText(/Prompt, Tools/);
  await user.click(within(dialog).getByRole("button", { name: "Apply" }));
  expect(await within(dialog).findByText("The original changed; review again.")).toBeVisible();
  expect(await within(dialog).findByText(/replaced by this clone’s: Model\./)).toBeVisible();
  expect(clones.getApplyPreview).toHaveBeenCalledTimes(2);
});

test("an agent that is no longer a clone hides Apply", async () => {
  const user = userEvent.setup();
  vi.mocked(clones.getApplyPreview).mockRejectedValue(new ApiError("This agent is not a clone.", 404));
  open("/agents/clone-1");
  await user.click(await screen.findByRole("button", { name: "Apply to SSH agent" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Apply to SSH agent" })).toBeNull());
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(await screen.findByText("This agent is not a clone.")).toBeVisible();
});

test("the agent list empty row spans every column", async () => {
  const user = userEvent.setup();
  open("/agents");
  await user.type(await screen.findByRole("textbox", { name: "Find agents" }), "nothing like it");
  const cell = await screen.findByText("No agents match your search.");
  expect(cell).toHaveAttribute("colspan", String(screen.getAllByRole("columnheader").length));
});

test("Delete this clone shows the history deleted with it", async () => {
  const user = userEvent.setup();
  open("/agents/clone-1");
  await user.click(await screen.findByRole("button", { name: "Apply to SSH agent" }));
  await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Apply" }));
  await user.click(await screen.findByRole("button", { name: "Delete this clone" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(await within(dialog).findByText("2 test chats")).toBeVisible();
  expect(profiles.getAgentHistory).toHaveBeenCalledWith("clone-1");
  await user.click(within(dialog).getByRole("button", { name: "Delete clone" }));
  await waitFor(() => expect(profiles.deleteAgent).toHaveBeenCalledWith("clone-1", 2, true));
});

test("a clone without history is deleted without history", async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.getAgentHistory).mockResolvedValue({
    test_chats: 0,
    chats: 0,
    documentation_sessions: 0,
    run_records: 0,
  });
  open("/agents/clone-1");
  await user.click(await screen.findByRole("button", { name: "Apply to SSH agent" }));
  await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Apply" }));
  await user.click(await screen.findByRole("button", { name: "Delete this clone" }));
  const dialog = await screen.findByRole("alertdialog");
  await waitFor(() => expect(within(dialog).getByRole("button", { name: "Delete clone" })).toBeEnabled());
  expect(within(dialog).queryByText(/history that will be deleted/)).toBeNull();
  await user.click(within(dialog).getByRole("button", { name: "Delete clone" }));
  await waitFor(() => expect(profiles.deleteAgent).toHaveBeenCalledWith("clone-1", 2, false));
});

test("a failed comparison offers Compare again", async () => {
  const user = userEvent.setup();
  vi.mocked(clones.getApplyPreview)
    .mockRejectedValueOnce(new ApiError("request failed (500)", 500))
    .mockResolvedValueOnce(structuredClone(preview));
  open("/agents/clone-1");
  await user.click(await screen.findByRole("button", { name: "Apply to SSH agent" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(await within(dialog).findByText("request failed (500)")).toBeVisible();
  await user.click(within(dialog).getByRole("button", { name: "Compare again" }));
  expect(await within(dialog).findByText(/replaced by this clone’s: Prompt, Tools\./)).toBeVisible();
  expect(within(dialog).queryByText("request failed (500)")).toBeNull();
  expect(within(dialog).queryByRole("button", { name: "Compare again" })).toBeNull();
});
