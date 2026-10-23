import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import AgentResources from "~/features/agents/AgentResources";
import * as profiles from "~/features/agents/api";

vi.mock("~/features/agents/api", async () => ({
  ...(await vi.importActual("~/features/agents/api")),
  getAgentOptions: vi.fn(),
  saveAgent: vi.fn(),
}));

const entry = (type: profiles.AccessType, id: string, name: string, extra: Partial<profiles.AccessEntry> = {}) => ({
  type,
  id,
  name,
  description_short: `${name} description`,
  ...extra,
});
const agent: profiles.AgentProfile = {
  id: "ops",
  name: "Ops",
  description: "",
  when_to_use: "",
  enabled: true,
  documents_grounded: false,
  revision: 4,
  model_id: null,
  system_prompt: "Prompt",
  instructions: "Notes",
  access: {
    resources: [
      entry("server", "s1", "web-1"),
      entry("document", "d1", "Runbook"),
      { type: "collection", id: "c-gone", name: "", description_short: "", missing: true },
    ],
  },
};
const options: profiles.AgentOptions = {
  server: [entry("server", "s1", "web-1"), entry("server", "s2", "db-1"), entry("server", "s3", "cache-1")],
  service: [entry("service", "v1", "Payments")],
  environment: [],
  code_project: [],
  collection: [entry("collection", "c1", "Jira")],
  document: [entry("document", "d1", "Runbook"), entry("document", "d2", "Guide")],
  skill: [],
  mcp_server: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(profiles.getAgentOptions).mockResolvedValue(structuredClone(options));
  vi.mocked(profiles.saveAgent).mockImplementation(async (value) => ({
    ...value,
    revision: value.revision + 1,
    access: { ...value.access, resources: value.access.resources.filter((item) => !item.missing) },
  }));
});
const show = (onSaved = vi.fn(), onDirtyChange = vi.fn()) =>
  render(
    <MemoryRouter>
      <AgentResources profile={structuredClone(agent)} onSaved={onSaved} onDirtyChange={onDirtyChange} />
    </MemoryRouter>,
  );
const group = (name: string) => screen.getByRole("group", { name });

test("lists the agent's resources by type with names, short descriptions and missing ones", async () => {
  show();
  const servers = await screen.findByRole("group", { name: "Servers" });
  expect(within(servers).getByText("web-1")).toBeVisible();
  expect(within(servers).getByText("web-1 description")).toBeVisible();
  expect(within(group("Documents")).queryByRole("switch")).toBeNull(); // no per-document editable pick
  const collections = group("Collections");
  expect(within(collections).getByText("missing")).toBeVisible();
  expect(within(collections).getByText("Missing collection (c-gone)")).toBeVisible();
  expect(within(collections).getByRole("button", { name: "Remove Missing collection (c-gone)" })).toBeEnabled();
  expect(within(collections).getByText(/dropped when you save/)).toBeVisible();
});

test("add, select all and remove make one PUT with the access object; missing ones are dropped", async () => {
  const user = userEvent.setup();
  const onSaved = vi.fn();
  const onDirty = vi.fn();
  show(onSaved, onDirty);
  await user.click(await screen.findByRole("button", { name: "Add servers" }));
  // only what the agent does not have yet is offered
  expect(screen.queryByRole("button", { name: "Add web-1" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Add db-1" }));
  expect(within(group("Servers")).getByText("db-1")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Add documents" }));
  await user.click(screen.getByRole("button", { name: "Select all documents" }));
  expect(within(group("Documents")).getByText("Guide")).toBeVisible();
  expect(screen.queryByRole("switch", { name: "Editable Guide" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Remove web-1" }));
  expect(within(group("Servers")).queryByText("web-1")).toBeNull();
  expect(onDirty).toHaveBeenLastCalledWith(true);
  await user.click(screen.getByRole("button", { name: "Save resources" }));
  await waitFor(() => expect(profiles.saveAgent).toHaveBeenCalledTimes(1));
  const saved = vi.mocked(profiles.saveAgent).mock.calls[0][0];
  expect(saved.revision).toBe(4);
  expect(saved.instructions).toBe("Notes");
  expect(saved.access).not.toHaveProperty("roles");
  expect(saved.access.resources.map(({ type, id }) => ({ type, id }))).toEqual([
    { type: "document", id: "d1" },
    { type: "collection", id: "c-gone" }, // dropped by saveAgent
    { type: "server", id: "s2" },
    { type: "document", id: "d2" },
  ]);
  await waitFor(() => expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ revision: 5 })));
  expect(onDirty).toHaveBeenLastCalledWith(false);
  expect(screen.queryByText("missing")).toBeNull();
});

test("select all adds every resource of a type once", async () => {
  const user = userEvent.setup();
  show();
  await user.click(await screen.findByRole("button", { name: "Add servers" }));
  await user.click(screen.getByRole("button", { name: "Select all servers" }));
  expect(within(group("Servers")).getAllByRole("button", { name: /^Remove / })).toHaveLength(3);
});

test("discard restores the saved list", async () => {
  const user = userEvent.setup();
  show();
  await user.click(await screen.findByRole("button", { name: "Remove web-1" }));
  await user.click(screen.getByRole("button", { name: "Discard changes" }));
  expect(within(group("Servers")).getByText("web-1")).toBeVisible();
  expect(screen.getByRole("button", { name: "Save resources" })).toBeDisabled();
});
