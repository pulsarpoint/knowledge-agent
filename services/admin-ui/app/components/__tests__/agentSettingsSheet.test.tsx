import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import AgentSettingsSheet from "~/features/sessions/AgentSettingsSheet";
import { defaultSettings, type AgentSettings } from "~/features/sessions/agentSettings";
import type { SessionDetail } from "~/features/sessions/api";
import { options } from "~/lib/__tests__/agentOptionsFixture";
import { agentOptions } from "~/lib/__tests__/agentScopeFixture";
import type { AgentOptions, ChatScope } from "~/features/sessions/api";

afterEach(() => vi.unstubAllGlobals());

const docScope: ChatScope = { kind: "document", id: "doc-1", title: "Runbook" };
const agentScope: ChatScope = { kind: "agent", id: "ops", name: "Operations" };
/** Radix tabs switch on mouse down. */
const openTab = (name: RegExp) => fireEvent.mouseDown(screen.getByRole("tab", { name }));
const tabs = () => screen.getAllByRole("tab").map((item) => item.textContent);

function Harness({
  session = null,
  onStartNew = () => {},
  message = "",
  scope = docScope,
  opts = options,
}: {
  session?: SessionDetail | null;
  onStartNew?: () => void;
  message?: string;
  scope?: ChatScope;
  opts?: AgentOptions;
}) {
  const [settings, setSettings] = useState<AgentSettings>(defaultSettings(opts));
  return (
    <>
      <AgentSettingsSheet
        open
        onOpenChange={() => {}}
        scope={scope}
        options={opts}
        settings={settings}
        onChange={(patch) => setSettings((current) => ({ ...current, ...patch }))}
        previewMessage={message}
        session={session}
        onStartNew={onStartNew}
      />
      <output aria-label="Settings state">{JSON.stringify(settings)}</output>
    </>
  );
}

test("before a conversation: linked resources ticked, unusable server disabled; no tool switches (every run gets every tool)", () => {
  render(<Harness />);
  expect(screen.getByRole("heading", { name: "Agent settings" })).toBeInTheDocument();
  expect(screen.getByText("What the agent may use in the next chat on “Runbook”.")).toBeInTheDocument();
  // an ad-hoc chat lists every resource (a type with none is left out)
  expect(tabs()).toEqual(["Documents 1/1", "Servers 1/2", "Services 1/1", "Code projects 0/1"]);
  openTab(/^Servers/);
  expect(screen.getByRole("switch", { name: "web-1" })).toBeChecked();
  expect(screen.getByRole("switch", { name: "prod-1" })).toBeDisabled();
  expect(screen.getByText("SSH is not set up for this server.")).toBeInTheDocument();
  expect(screen.queryByText("Tools")).toBeNull();
  expect(screen.queryByRole("switch", { name: "Allow subagents" })).toBeNull();
  openTab(/^Code projects/);
  fireEvent.click(screen.getByRole("switch", { name: "Shop" }));
  expect(tabs()).toContain("Code projects 1/1");
  const state = JSON.parse(screen.getByLabelText("Settings state").textContent!);
  expect(state.chosen.code_project_ids).toEqual(["c1"]);
  expect(state).not.toHaveProperty("off");
  expect(state).not.toHaveProperty("subagents");
  expect(screen.getByRole("checkbox", { name: "Payments expert" })).toBeInTheDocument(); // experts still narrow
});

test("the document is a checked, disabled resource, and the prompt list ends with Own prompt", () => {
  render(<Harness />);
  expect(screen.getByRole("switch", { name: "Runbook" })).toBeChecked();
  expect(screen.getByRole("switch", { name: "Runbook" })).toBeDisabled();
  expect(screen.getByText("The agent may change it (as a draft).")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Advanced" }));
  fireEvent.click(screen.getByRole("combobox", { name: "Prompt" }));
  expect(screen.getAllByRole("option").map((item) => item.textContent)).toEqual([
    "Careful reviser",
    "Revise a document",
    "Own prompt",
  ]);
});

test("Preview sends the message box text, or a placeholder when it is empty", async () => {
  const bodies: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init?: RequestInit) => {
      bodies.push(JSON.parse(String(init?.body)));
      return new Response(JSON.stringify({ config: { model: { name: "Claude" } }, first_message: "x" }), {
        status: 200,
      });
    }),
  );
  const { rerender } = render(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "Preview" }));
  expect((await screen.findByLabelText("Configuration preview")).textContent).toContain('"name": "Claude"');
  expect((bodies[0] as { message: string }).message).toBe("(your next message)");
  rerender(<Harness message="Add log rotation." />);
  fireEvent.click(screen.getByRole("button", { name: "Preview" }));
  await waitFor(() => expect((bodies[1] as { message: string }).message).toBe("Add log rotation."));
});

test("read-only for an open run: its saved configuration, and Start new chat with these settings", () => {
  const onStartNew = vi.fn();
  const session = { id: "r1", status: "waiting", config: { model: { name: "GPT" } } } as unknown as SessionDetail;
  render(<Harness session={session} onStartNew={onStartNew} />);
  expect(screen.getByText("This chat's settings. They cannot change while it is open.")).toBeInTheDocument();
  openTab(/^Servers/); // the tabs still switch
  expect(screen.getByRole("switch", { name: "web-1" })).toBeDisabled();
  expect(screen.getByRole("textbox", { name: "Filter servers" })).toBeEnabled();
  expect(screen.getByLabelText("Run configuration").textContent).toContain('"name": "GPT"');
  expect(screen.queryByRole("button", { name: "Preview" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Start new chat with these settings" }));
  expect(onStartNew).toHaveBeenCalledOnce();
});

test("read-only: Advanced still opens and shows the run's system prompt, disabled", () => {
  const session = { id: "r1", status: "waiting", config: {} } as unknown as SessionDetail;
  render(<Harness session={session} />);
  const advanced = screen.getByRole("button", { name: "Advanced" });
  expect(advanced).toBeEnabled();
  fireEvent.click(advanced);
  expect(screen.getByRole("textbox", { name: "System prompt" })).toBeDisabled();
  expect(screen.getByRole("textbox", { name: "System prompt" })).toHaveValue(
    "Read with get_document. Change with edit_document or update_document.",
  );
});

test("a saved agent shows its model and prompt read-only and offers only narrowing", () => {
  render(<Harness scope={agentScope} opts={agentOptions} />);
  expect(screen.getByText("What the agent may use in the next chat.")).toBeInTheDocument();
  expect(screen.getByText("GPT")).toBeInTheDocument();
  expect(screen.queryByRole("combobox", { name: "Model" })).toBeNull();
  expect(screen.getAllByText("Change them in General.").length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole("button", { name: "Advanced" }));
  expect(screen.getByText("You operate servers.")).toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: "System prompt" })).toBeNull();
  expect(screen.queryByRole("combobox", { name: "Prompt" })).toBeNull();
  expect(tabs()).toEqual(["Servers 1/1"]); // only its own resources
  expect(screen.getByRole("switch", { name: "web-1" })).toBeChecked(); // everything it has, by default
  expect(screen.queryByRole("switch", { name: "Allow subagents" })).toBeNull();
  expect(screen.getByRole("checkbox", { name: "Payments expert" })).toBeChecked();
});

test("a saved agent's run picks a subset of its documents and servers; each may be drafted", () => {
  const fuller: AgentOptions = {
    ...agentOptions,
    resources: {
      ...agentOptions.resources,
      servers: [
        ...agentOptions.resources.servers,
        { id: "s2", name: "db-1", linked: false, usable: false, reason: "SSH is not set up" },
      ],
      documents: [
        { id: "d1", name: "Runbook", linked: false },
        { id: "d2", name: "Guide", linked: false },
      ],
    },
  };
  render(<Harness scope={agentScope} opts={fuller} />);
  const guide = screen.getByRole("switch", { name: "Guide" });
  expect(guide).toBeChecked();
  expect(guide).toBeEnabled();
  expect(screen.queryByText("Read only.")).toBeNull();
  expect(screen.getAllByText("The agent may change it (as a draft).")).toHaveLength(2);
  fireEvent.click(guide);
  expect(screen.getByRole("switch", { name: "Guide" })).not.toBeChecked();
  openTab(/^Servers/);
  const db = screen.getByRole("switch", { name: "db-1" }); // given without SSH: it can still be left out
  expect(db).toBeChecked();
  fireEvent.click(db);
  const state = JSON.parse(screen.getByLabelText("Settings state").textContent!);
  expect(state.documents).toEqual(["d1"]);
  expect(state.chosen.server_ids).toEqual(["s1"]);
});

test("a saved agent's chat lists only its resources: All and the filter work within them, nothing can be added", () => {
  const fuller: AgentOptions = {
    ...agentOptions,
    resources: {
      ...agentOptions.resources,
      servers: [
        { id: "s1", name: "web-1", linked: false, usable: true, reason: null },
        { id: "s2", name: "db-1", linked: false, usable: true, reason: null },
      ],
    },
  };
  render(<Harness scope={agentScope} opts={fuller} />);
  expect(tabs()).toEqual(["Servers 2/2"]);
  expect(screen.queryByRole("switch", { name: "prod-1" })).toBeNull(); // not the agent's: not offered
  fireEvent.click(screen.getByRole("switch", { name: "All servers" }));
  expect(JSON.parse(screen.getByLabelText("Settings state").textContent!).chosen.server_ids).toEqual([]);
  fireEvent.change(screen.getByRole("textbox", { name: "Filter servers" }), { target: { value: "db" } });
  expect(screen.queryByRole("switch", { name: "web-1" })).toBeNull();
  fireEvent.click(screen.getByRole("switch", { name: "All servers" }));
  expect(JSON.parse(screen.getByLabelText("Settings state").textContent!).chosen.server_ids).toEqual(["s2"]);
  expect(tabs()).toEqual(["Servers 1/2"]);
});

test("an open agent run shows the run's model and prompt, not the agent's current ones", () => {
  const session = {
    id: "r1",
    status: "waiting",
    model_name: "GPT then",
    prompt_snapshot: { system_prompt: "You operated servers." },
    config: {},
  } as unknown as SessionDetail;
  render(<Harness scope={agentScope} opts={agentOptions} session={session} />);
  expect(screen.getByText("GPT then")).toBeInTheDocument();
  expect(screen.queryByText("GPT")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Advanced" }));
  expect(screen.getByLabelText("Agent prompt").textContent).toBe("You operated servers.");
});

const CATALOG = [
  { name: "search_documents", group: "documents", description: "Search the documents." },
  { name: "ssh_run", group: "servers", description: "Run a command over SSH." },
];
const stubCatalog = () =>
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ tools: CATALOG }), { status: 200 })),
  );

test("Advanced has the Tools: switching one off goes into the settings", async () => {
  stubCatalog();
  render(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "Advanced" }));
  expect(await screen.findByText("2 of 2 tools on")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox", { name: "ssh_run" }));
  expect(screen.getByText("1 of 2 tools on")).toBeInTheDocument();
  expect(JSON.parse(screen.getByLabelText("Settings state").textContent!).disabledTools).toEqual(["ssh_run"]);
});

test("a saved agent's off tools are locked as Off for this agent; the rest can be switched off", async () => {
  stubCatalog();
  const opts = { ...agentOptions, agent: { ...agentOptions.agent!, disabled_tools: ["ssh_run"] } };
  render(<Harness scope={agentScope} opts={opts} />);
  fireEvent.click(screen.getByRole("button", { name: "Advanced" }));
  expect(await screen.findByText("1 of 2 tools on")).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "ssh_run" })).toBeDisabled();
  expect(screen.getByText("Off for this agent")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox", { name: "search_documents" }));
  expect(JSON.parse(screen.getByLabelText("Settings state").textContent!).disabledTools).toEqual(["search_documents"]);
});

test("an open conversation shows its tools read-only", async () => {
  stubCatalog();
  const session = { id: "r1", status: "waiting", config: {}, disabled_tools: ["ssh_run"] } as unknown as SessionDetail;
  render(<Harness session={session} />);
  fireEvent.click(screen.getByRole("button", { name: "Advanced" }));
  await screen.findByText("1 of 2 tools on");
  expect(screen.getByRole("checkbox", { name: "ssh_run" })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: "ssh_run" })).not.toBeChecked();
});

test("a saved agent's next run can be given an output schema; a document's chat has no schema", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url === "/api/output-schemas"
        ? new Response(
            JSON.stringify({
              schemas: [
                {
                  id: "s1",
                  name: "Verdict",
                  description: "",
                  current_version: 1,
                  revision: 1,
                  created_at: "",
                  updated_at: "",
                  used_by: [],
                },
              ],
            }),
          )
        : new Response("{}", { status: 404 }),
    ),
  );
  const { unmount } = render(<Harness scope={docScope} />);
  expect(screen.queryByRole("combobox", { name: "Output schema" })).toBeNull();
  unmount();
  render(<Harness scope={agentScope} opts={agentOptions} />);
  fireEvent.click(screen.getByRole("combobox", { name: "Output schema" }));
  fireEvent.click(await screen.findByRole("option", { name: "Verdict" }));
  await waitFor(() =>
    expect(JSON.parse(screen.getByLabelText("Settings state").textContent!).outputSchema).toEqual({
      id: "s1",
      name: "Verdict",
      version: null,
    }),
  );
});
