import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import AgentWizard from "~/features/agents/AgentWizard";
import * as api from "~/features/agents/wizard";
import { Toaster } from "~/components/ui/sonner";

vi.mock("~/features/agents/wizard", async () => ({
  ...(await vi.importActual("~/features/agents/wizard")),
  getWizardOptions: vi.fn(),
  generateAgent: vi.fn(),
  createWizardAgent: vi.fn(),
}));
const proposal: api.AgentProposal = {
  name: "Payments helper",
  description: "Investigate payments.",
  when_to_use: "Payment incidents.",
  system_prompt: "Find evidence before answering.",
  instructions: "Check exit codes.",
  model_id: "model",
  enabled: true,
  access: {
    resources: [
      { type: "collection", id: "jira" },
      { type: "code_project", id: "repo" },
      { type: "skill", id: "skill" },
      { type: "mcp_server", id: "mcp" },
      { type: "service", id: "service" },
      { type: "server", id: "server" },
      { type: "document", id: "doc", editable: false },
    ],
    roles: { content_editor: true },
  },
};
const catalogTools = [
  { name: "read_file", group: "code", label: "Read file", summary: "Read a file.", usage: "", needs: "files" },
  { name: "code_search", group: "code", label: "Code search", summary: "Find functions.", usage: "", needs: "code" },
  {
    name: "search_documents",
    group: "documents",
    label: "Search documents",
    summary: "Search.",
    usage: "",
    needs: "documents",
  },
  { name: "ssh_run", group: "servers", label: "SSH", summary: "Run a command.", usage: "", needs: "ssh" },
  {
    name: "run_subagent",
    group: "agents",
    label: "Start subagents",
    summary: "Hand work to a helper.",
    usage: "",
    needs: null,
  },
] as const;
const options: api.WizardOptions = {
  tools: [...catalogTools],
  prompt: "Design a specialist.",
  schema: { type: "object" },
  catalog: {
    models: [{ id: "model", name: "Claude", model: "claude-test", provider: "bedrock", api_format: "native" }],
    code_projects: [{ id: "repo", name: "Payments", folder_paths: ["payments"], remote_url: null }],
    collections: [{ id: "jira", name: "jira-tickets", description: "Jira tickets" }],
    servers: [{ id: "server", name: "Staging", description: "", hostname: "staging.test", tags: [], ssh_ready: true }],
    environments: [{ id: "env", name: "Production", description: "" }],
    documents: [{ id: "doc", title: "Runbook", description: "On-call runbook" }],
    skills: [{ id: "skill", name: "Inspection" }],
    mcp_servers: [{ id: "mcp", name: "Docs MCP" }],
    services: [{ id: "service", name: "Payments API" }],
    existing_agents: [],
  },
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getWizardOptions).mockResolvedValue(structuredClone(options));
  vi.mocked(api.generateAgent).mockResolvedValue({ proposal: structuredClone(proposal) });
  vi.mocked(api.createWizardAgent).mockResolvedValue({ id: "new-agent" });
});
async function open() {
  const user = userEvent.setup();
  const created = vi.fn();
  const close = vi.fn();
  render(
    <MemoryRouter>
      <AgentWizard onClose={close} onCreated={created} />
      <Toaster />
    </MemoryRouter>,
  );
  await screen.findByLabelText("What should this agent do?");
  await user.type(screen.getByLabelText("What should this agent do?"), "Investigate payment incidents.");
  return { user, created, close };
}

test("generates a reviewable proposal, edits resources and the Content editor switch, then creates once", async () => {
  const { user, created } = await open();
  await user.click(screen.getByRole("button", { name: "Show design context" }));
  expect(
    (screen.getByLabelText("Generation prompt, configuration schema, and resource catalog") as HTMLTextAreaElement)
      .value,
  ).toContain("Design a specialist.");
  await user.click(screen.getByRole("button", { name: "Generate proposal" }));
  await screen.findByLabelText("Agent name");
  expect(api.generateAgent).toHaveBeenCalledWith("Investigate payment incidents.", "model");
  expect(api.createWizardAgent).not.toHaveBeenCalled();
  expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["General", "Prompt", "Resources", "Tools"]);
  await user.clear(screen.getByLabelText("Agent name"));
  await user.type(screen.getByLabelText("Agent name"), "Reviewed agent");
  expect(screen.getByLabelText("Proposed resources")).toHaveTextContent(
    "1 server · 1 service · 1 code project · 1 collection · 1 document · 1 skill · 1 MCP server",
  );
  const editor = screen.getByRole("switch", { name: "Content editor" });
  expect(editor).toBeChecked();
  await user.click(editor);
  await user.click(screen.getByRole("tab", { name: "Resources" }));
  const projects = screen.getByRole("group", { name: "Code projects" });
  expect(within(projects).getByRole("checkbox", { name: "Payments" })).toBeChecked();
  await user.click(within(projects).getByRole("checkbox", { name: "Payments" }));
  expect(screen.getByRole("checkbox", { name: "jira-tickets" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Staging" })).toBeChecked();
  await user.click(screen.getByRole("checkbox", { name: "Production" }));
  await user.click(screen.getByRole("switch", { name: "Editable Runbook" }));
  await user.click(screen.getByRole("tab", { name: "Prompt" }));
  expect(screen.getByLabelText("System prompt")).toHaveAttribute("maxLength", "100000");
  fireEvent.change(screen.getByLabelText("System prompt"), { target: { value: "Use evidence and cite sources." } });
  await user.click(screen.getByRole("button", { name: "Create agent" }));
  expect(api.createWizardAgent).toHaveBeenCalledTimes(1);
  const sent = vi.mocked(api.createWizardAgent).mock.calls[0][0];
  expect(sent).toMatchObject({
    name: "Reviewed agent",
    system_prompt: "Use evidence and cite sources.",
    instructions: "Check exit codes.",
    access: { roles: { content_editor: false } },
  });
  expect(sent).not.toHaveProperty("resources");
  expect(sent).not.toHaveProperty("servers");
  expect(sent.access.resources).toEqual([
    { type: "collection", id: "jira" },
    { type: "skill", id: "skill" },
    { type: "mcp_server", id: "mcp" },
    { type: "service", id: "service" },
    { type: "server", id: "server" },
    { type: "document", id: "doc", editable: true },
    { type: "environment", id: "env" },
  ]);
  expect(created).toHaveBeenCalledWith("new-agent");
  expect(await screen.findByText("Agent Reviewed agent created")).toBeVisible();
});

test("generation and save errors preserve role and proposal for retry", async () => {
  const { user, created } = await open();
  vi.mocked(api.generateAgent).mockRejectedValueOnce(new Error("Model unavailable."));
  await user.click(screen.getByRole("button", { name: "Generate proposal" }));
  expect(await screen.findByText("Model unavailable.")).toBeVisible();
  expect(screen.getByLabelText("What should this agent do?")).toHaveValue("Investigate payment incidents.");
  await user.click(screen.getByRole("button", { name: "Generate proposal" }));
  await screen.findByLabelText("Agent name");
  vi.mocked(api.createWizardAgent).mockRejectedValueOnce(new Error("Name already exists."));
  await user.click(screen.getByRole("button", { name: "Create agent" }));
  expect(await screen.findByText("Name already exists.")).toBeVisible();
  expect(screen.getByLabelText("Agent name")).toHaveValue("Payments helper");
  expect(created).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Edit role" }));
  expect(screen.getByLabelText("What should this agent do?")).toHaveValue("Investigate payment incidents.");
  await user.click(screen.getByRole("button", { name: "Back to proposal" }));
  expect(screen.getByLabelText("Agent name")).toHaveValue("Payments helper");
});

test("requires a prompt before saving and can cancel without creating", async () => {
  const { user, close } = await open();
  await user.click(screen.getByRole("button", { name: "Generate proposal" }));
  await screen.findByLabelText("Agent name");
  await user.click(screen.getByRole("tab", { name: "Prompt" }));
  fireEvent.change(screen.getByLabelText("System prompt"), { target: { value: "" } });
  await user.click(screen.getByRole("tab", { name: "General" }));
  expect(screen.getByRole("button", { name: "Create agent" })).toBeDisabled();
  expect(api.createWizardAgent).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(close).toHaveBeenCalledTimes(1);
});

test("start blank opens an empty proposal without generating and can create without a model", async () => {
  const user = userEvent.setup();
  const created = vi.fn();
  vi.mocked(api.getWizardOptions).mockResolvedValue({
    ...structuredClone(options),
    catalog: { ...structuredClone(options.catalog), models: [] },
  });
  render(
    <MemoryRouter>
      <AgentWizard onClose={vi.fn()} onCreated={created} />
    </MemoryRouter>,
  );
  await user.click(await screen.findByRole("button", { name: "Start blank" }));
  expect(api.generateAgent).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Agent name")).toHaveValue("");
  expect(screen.getByRole("button", { name: "Create agent" })).toBeDisabled();
  await user.type(screen.getByLabelText("Agent name"), "Blank helper");
  await user.click(screen.getByRole("tab", { name: "Prompt" }));
  fireEvent.change(screen.getByLabelText("System prompt"), { target: { value: "Answer carefully." } });
  await user.click(screen.getByRole("button", { name: "Create agent" }));
  expect(api.createWizardAgent).toHaveBeenCalledWith(
    expect.objectContaining({
      name: "Blank helper",
      system_prompt: "Answer carefully.",
      model_id: null,
      access: { resources: [], roles: { content_editor: false } },
    }),
  );
  expect(created).toHaveBeenCalledWith("new-agent");
});

test("Agent model comes from page 1 and shows on the review", async () => {
  vi.mocked(api.getWizardOptions).mockResolvedValue({
    ...structuredClone(options),
    catalog: {
      ...structuredClone(options.catalog),
      models: [
        { id: "model", name: "Claude", model: "eu.anthropic.claude-test", provider: "bedrock", api_format: "native" },
        { id: "gpt", name: "GPT", model: "gpt-5", provider: "openai", api_format: "responses" },
      ],
    },
  });
  const user = userEvent.setup();
  render(
    <MemoryRouter>
      <AgentWizard onClose={vi.fn()} onCreated={vi.fn()} />
      <Toaster />
    </MemoryRouter>,
  );
  await screen.findByLabelText("What should this agent do?");
  await user.click(screen.getByRole("combobox", { name: "Agent model" }));
  await user.click(screen.getByRole("option", { name: /^GPT/ }));
  await user.click(screen.getByRole("button", { name: "Start blank" }));
  expect(screen.queryByRole("combobox", { name: "Agent type" })).toBeNull();
  expect(screen.queryByRole("combobox", { name: "Agent model" })).toBeNull();
  expect(screen.queryByRole("combobox", { name: "Resources" })).toBeNull();
  expect(screen.getByLabelText("Chosen model")).toHaveTextContent("GPT");
  await user.click(screen.getByRole("button", { name: "Edit role" }));
  expect(screen.getByRole("combobox", { name: "Agent model" })).toHaveTextContent("GPT");
  await user.click(screen.getByRole("combobox", { name: "Agent model" }));
  await user.click(screen.getByRole("option", { name: /^Claude/ }));
  await user.click(screen.getByRole("button", { name: "Back to proposal" }));
  expect(screen.getByLabelText("Chosen model")).toHaveTextContent("Claude");
  await user.type(screen.getByLabelText("Agent name"), "Writer");
  await user.click(screen.getByRole("tab", { name: "Prompt" }));
  await user.type(screen.getByLabelText("System prompt"), "Write documentation.");
  await user.click(screen.getByRole("button", { name: "Create agent" }));
  expect(api.createWizardAgent).toHaveBeenCalledWith(expect.objectContaining({ name: "Writer", model_id: "model" }));
  expect(vi.mocked(api.createWizardAgent).mock.calls[0][0]).not.toHaveProperty("runtime");
});

test("step 1 starts with Agent model, which shapes the proposal and a blank start", async () => {
  vi.mocked(api.getWizardOptions).mockResolvedValue({
    ...structuredClone(options),
    catalog: {
      ...structuredClone(options.catalog),
      models: [
        { id: "gpt", name: "GPT", model: "gpt-5", provider: "openai", api_format: "responses" },
        { id: "model", name: "Claude", model: "eu.anthropic.claude-test", provider: "bedrock", api_format: "native" },
      ],
    },
  });
  const { user } = await open();
  expect(screen.getAllByRole("combobox")[0]).toHaveAttribute("id", "wizard-agent-model");
  expect(screen.getByRole("combobox", { name: "Agent model" })).toHaveTextContent("GPT"); // the first model that fits
  expect(screen.queryByRole("combobox", { name: "Agent type" })).toBeNull();
  expect(screen.queryByRole("combobox", { name: "Resources" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Generate proposal" }));
  expect(api.generateAgent).toHaveBeenCalledWith("Investigate payment incidents.", "gpt");
  expect(await screen.findByLabelText("Chosen model")).toHaveTextContent("GPT");
  expect(screen.queryByRole("combobox", { name: "Agent model" })).toBeNull();
});

test("the proposal is generated with the first catalog model", async () => {
  vi.mocked(api.getWizardOptions).mockResolvedValue({
    ...structuredClone(options),
    catalog: {
      ...structuredClone(options.catalog),
      models: [
        { id: "gpt", name: "GPT", model: "gpt-5", provider: "openai", api_format: "responses" },
        { id: "model", name: "Claude", model: "eu.anthropic.claude-test", provider: "bedrock", api_format: "native" },
      ],
    },
  });
  const { user } = await open();
  await user.click(screen.getByRole("button", { name: "Generate proposal" }));
  expect(api.generateAgent).toHaveBeenCalledWith("Investigate payment incidents.", "gpt");
});

test("a generated proposal runs on the model chosen on page 1", async () => {
  vi.mocked(api.getWizardOptions).mockResolvedValue({
    ...structuredClone(options),
    catalog: {
      ...structuredClone(options.catalog),
      models: [
        { id: "gpt", name: "GPT", model: "gpt-5", provider: "openai", api_format: "responses" },
        { id: "model", name: "Claude", model: "eu.anthropic.claude-test", provider: "bedrock", api_format: "native" },
      ],
    },
  });
  const { user } = await open();
  await user.click(screen.getByRole("combobox", { name: "Agent model" }));
  await user.click(screen.getByRole("option", { name: /^GPT/ }));
  await user.click(screen.getByRole("button", { name: "Generate proposal" })); // the proposal names 'model'
  await screen.findByLabelText("Agent name");
  await user.click(screen.getByRole("button", { name: "Create agent" }));
  expect(api.createWizardAgent).toHaveBeenCalledWith(expect.objectContaining({ model_id: "gpt" }));
});

test("the wizard Tools page follows the resources until changed and sends the choice", async () => {
  const { user } = await open();
  await user.click(screen.getByRole("button", { name: "Generate proposal" }));
  await screen.findByLabelText("Agent name");
  await user.click(screen.getByRole("tab", { name: "Tools" }));
  const checked = () =>
    ["Read file", "Code search", "Search documents", "SSH"].filter(
      (name) => screen.getByRole("switch", { name }).getAttribute("aria-checked") === "true",
    );
  expect(checked()).toEqual(["Read file", "Code search", "Search documents", "SSH"]);
  await user.click(screen.getByRole("tab", { name: "Resources" }));
  await user.click(screen.getByRole("checkbox", { name: "Staging" }));
  await user.click(screen.getByRole("tab", { name: "Tools" }));
  expect(checked()).toEqual(["Read file", "Code search", "Search documents"]);
  await user.click(screen.getByRole("switch", { name: "Code search" }));
  await user.click(screen.getByRole("tab", { name: "Resources" }));
  await user.click(screen.getByRole("checkbox", { name: "Staging" }));
  await user.click(screen.getByRole("tab", { name: "Tools" }));
  expect(checked()).toEqual(["Read file", "Search documents"]);
  await user.click(screen.getByRole("button", { name: "Create agent" }));
  expect(api.createWizardAgent).toHaveBeenCalledWith(
    expect.objectContaining({ tools: ["read_file", "search_documents"] }),
  );
});

test("Follow the resources drops the tool choice: the switches follow the resources and no tools are sent", async () => {
  const { user } = await open();
  await user.click(screen.getByRole("button", { name: "Generate proposal" }));
  await screen.findByLabelText("Agent name");
  await user.click(screen.getByRole("tab", { name: "Tools" }));
  const checked = () =>
    ["Read file", "Code search", "Search documents", "SSH"].filter(
      (name) => screen.getByRole("switch", { name }).getAttribute("aria-checked") === "true",
    );
  expect(screen.queryByRole("button", { name: "Follow the resources" })).toBeNull();
  await user.click(screen.getByRole("switch", { name: "Code search" }));
  expect(checked()).toEqual(["Read file", "Search documents", "SSH"]);
  expect(screen.getByText(/Your choice of tools\./)).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Follow the resources" }));
  expect(checked()).toEqual(["Read file", "Code search", "Search documents", "SSH"]);
  expect(screen.getByText(/Follows the resources until you change a tool\./)).toBeVisible();
  expect(screen.queryByRole("button", { name: "Follow the resources" })).toBeNull();
  await user.click(screen.getByRole("tab", { name: "Resources" }));
  await user.click(screen.getByRole("checkbox", { name: "Staging" }));
  await user.click(screen.getByRole("tab", { name: "Tools" }));
  expect(checked()).toEqual(["Read file", "Code search", "Search documents"]); // following again
  await user.click(screen.getByRole("button", { name: "Create agent" }));
  expect(vi.mocked(api.createWizardAgent).mock.calls[0][0]).not.toHaveProperty("tools");
});

test("an untouched Tools page lets the backend apply the resources", async () => {
  const { user } = await open();
  await user.click(screen.getByRole("button", { name: "Generate proposal" }));
  await screen.findByLabelText("Agent name");
  await user.click(screen.getByRole("button", { name: "Create agent" }));
  expect(vi.mocked(api.createWizardAgent).mock.calls[0][0]).not.toHaveProperty("tools");
});

test("the wizard Tools page switches May start subagents and chooses what a subagent may use", async () => {
  const { user } = await open();
  await user.click(screen.getByRole("button", { name: "Generate proposal" }));
  await screen.findByLabelText("Agent name");
  await user.click(screen.getByRole("tab", { name: "Tools" }));
  expect(screen.queryByRole("switch", { name: "Start subagents" })).toBeNull(); // not a tool to switch: May start subagents is
  const subagents = screen.getByRole("switch", { name: "May start subagents" });
  expect(subagents).not.toBeChecked();
  expect(screen.queryByRole("group", { name: "A subagent may use" })).toBeNull();
  await user.click(subagents);
  const may = screen.getByRole("group", { name: "A subagent may use" });
  for (const name of ["Read file", "Code search", "Search documents", "SSH"])
    expect(within(may).getByRole("checkbox", { name })).toBeChecked();
  await user.click(within(may).getByRole("checkbox", { name: "SSH" }));
  await user.click(screen.getByRole("button", { name: "Create agent" }));
  const created = vi.mocked(api.createWizardAgent).mock.calls[0][0];
  expect(created).toMatchObject({ subagents: true, subagent_blocked: ["ssh_run"] });
  expect(created).not.toHaveProperty("tools"); // the tools still follow the resources
});
