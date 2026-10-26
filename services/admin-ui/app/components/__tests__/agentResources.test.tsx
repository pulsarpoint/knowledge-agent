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

const entry = (
  type: profiles.AccessType,
  id: string,
  name: string,
  extra: Partial<profiles.AgentOptions[profiles.AccessType][number]> = {},
) => ({
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
  revision: 4,
  model_id: null,
  system_prompt: "Prompt",
  instructions: "Notes",
  access: {
    resources: [
      entry("server", "s1", "web-1"),
      entry("document", "d1", "Runbook"),
      { type: "service", id: "v-gone", name: "", description_short: "", missing: true },
    ],
  },
};
const options: profiles.AgentOptions = {
  server: [entry("server", "s1", "web-1"), entry("server", "s2", "db-1"), entry("server", "s3", "cache-1")],
  service: [entry("service", "v1", "Payments")],
  environment: [entry("environment", "e1", "Production", { server_ids: ["s2"], service_ids: ["v1"] })],
  code_project: [],
  document: [entry("document", "d1", "Runbook"), entry("document", "d2", "Guide")],
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
const tab = (name: RegExp) => screen.findByRole("tab", { name });

test("a tab per type with counts; every resource of the project with a switch; missing ones first", async () => {
  const user = userEvent.setup();
  show();
  await screen.findByRole("switch", { name: "db-1" });
  expect(screen.getAllByRole("tab").map((item) => item.textContent)).toEqual([
    "Servers 1/3",
    "Services 1/2",
    "Environments 0/1",
    "Code projects 0/0",
    "Documents 1/2",
    "MCP servers 0/0",
  ]);
  expect(screen.getByRole("switch", { name: "web-1" })).toBeChecked();
  expect(screen.getByRole("switch", { name: "db-1" })).not.toBeChecked();
  expect(screen.getByText("web-1 description")).toBeVisible();
  await user.click(await tab(/^Services/));
  const services = screen.getAllByRole("switch").map((item) => item.getAttribute("aria-label"));
  expect(services).toEqual(["All services", "Missing service (v-gone)", "Payments"]);
  expect(screen.getByText("missing")).toBeVisible();
  expect(screen.getByText(/dropped when you save/)).toBeVisible();
  expect(screen.queryByText(/skill/i)).toBeNull(); // every agent sees every enabled skill
});

test("switches and All make one PUT with the access object; tabs keep unsaved changes; missing ones are dropped", async () => {
  const user = userEvent.setup();
  const onSaved = vi.fn();
  const onDirty = vi.fn();
  show(onSaved, onDirty);
  await user.click(await screen.findByRole("switch", { name: "db-1" }));
  await user.click(screen.getByRole("switch", { name: "web-1" }));
  expect(onDirty).toHaveBeenLastCalledWith(true);
  await user.click(await tab(/^Documents/));
  await user.click(screen.getByRole("switch", { name: "All documents" }));
  expect(screen.getByRole("tab", { name: "Documents 2/2" })).toBeInTheDocument();
  await user.click(await tab(/^Servers/));
  expect(screen.getByRole("switch", { name: "db-1" })).toBeChecked();
  await user.click(screen.getByRole("button", { name: "Save resources" }));
  await waitFor(() => expect(profiles.saveAgent).toHaveBeenCalledTimes(1));
  const saved = vi.mocked(profiles.saveAgent).mock.calls[0][0];
  expect(saved.revision).toBe(4);
  expect(saved.instructions).toBe("Notes");
  expect(saved.access.resources.map(({ type, id }) => ({ type, id }))).toEqual([
    { type: "document", id: "d1" },
    { type: "service", id: "v-gone" }, // dropped by saveAgent
    { type: "server", id: "s2" },
    { type: "document", id: "d2" },
  ]);
  await waitFor(() => expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ revision: 5 })));
  expect(onDirty).toHaveBeenLastCalledWith(false);
  await user.click(await tab(/^Services/));
  expect(screen.queryByText("missing")).toBeNull();
});

test("a server or service a selected environment brings shows via environment", async () => {
  const user = userEvent.setup();
  show();
  await screen.findByRole("switch", { name: "db-1" });
  expect(screen.queryByText("via environment Production")).toBeNull();
  await user.click(await tab(/^Environments/));
  await user.click(screen.getByRole("switch", { name: "Production" }));
  await user.click(await tab(/^Servers/));
  expect(screen.getByText("via environment Production")).toBeVisible(); // db-1, a member of Production
  await user.click(await tab(/^Services/));
  expect(screen.getByText("via environment Production")).toBeVisible(); // Payments, deployed in it
});

test("discard restores the saved list", async () => {
  const user = userEvent.setup();
  show();
  await user.click(await screen.findByRole("switch", { name: "web-1" }));
  expect(screen.getByRole("switch", { name: "web-1" })).not.toBeChecked();
  await user.click(screen.getByRole("button", { name: "Discard changes" }));
  expect(screen.getByRole("switch", { name: "web-1" })).toBeChecked();
  expect(screen.getByRole("button", { name: "Save resources" })).toBeDisabled();
});

test("narrow screens show an accordion section per type", async () => {
  const user = userEvent.setup();
  const width = window.innerWidth;
  window.innerWidth = 500;
  try {
    show();
    await user.click(await screen.findByRole("button", { name: "Servers 1/3" }));
    expect(await screen.findByRole("switch", { name: "db-1" })).toBeVisible();
    expect(screen.queryByRole("tab")).toBeNull();
  } finally {
    window.innerWidth = width;
  }
});
