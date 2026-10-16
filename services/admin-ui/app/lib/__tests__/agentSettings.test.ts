import {
  OWN,
  defaultSettings,
  restoreSettings,
  settingsFromSession,
  settingsSummary,
  shownTools,
  startInput,
  withScope,
} from "~/features/sessions/agentSettings";
import type { SessionDetail } from "~/features/sessions/api";
import { options } from "./agentOptionsFixture";
import { agentOptions } from "./agentScopeFixture";

const doc = (title = "Runbook") => ({ kind: "document" as const, id: "doc-1", title });

test("defaults: the default model, the first tagged prompt, the usable linked resources, every tool on", () => {
  const settings = defaultSettings(options);
  expect(settings).toEqual({
    modelId: "m2",
    promptKey: "p2",
    systemPrompt: options.prompts[1].content,
    chosen: { server_ids: ["s1"], service_ids: ["v1"], environment_ids: [], code_project_ids: [], collection_ids: [] },
    off: [],
    subagents: false,
    experts: [],
  });
  expect(shownTools(options, settings).map((tool) => tool.name)).toEqual(["ssh_run", "save_document", "get_document"]);
});

test("startInput sends only the shown tools that are on", () => {
  const settings = { ...defaultSettings(options), off: ["get_document", "save_document"] };
  expect(startInput(doc(), options, settings, "x").tools).toEqual(["ssh_run"]);
  expect(startInput(doc(), options, { ...settings, promptKey: OWN }, "x").prompt_id).toBeNull();
});

test("the start body gives the document as an editable resource and the context names it", () => {
  const body = startInput(doc(), options, defaultSettings(options), "  Add a section.  ");
  expect(body).toMatchObject({
    started_from: { document_id: "doc-1" },
    message: "Add a section.",
    prompt_id: "p2",
    system_prompt: options.prompts[1].content,
    context: "Document: Runbook (id doc-1)",
  });
  expect(body.resources.documents).toEqual([{ id: "doc-1", editable: true }]);
  expect(body.tools).toEqual(expect.arrayContaining(["save_document", "get_document"]));
  expect(body).not.toHaveProperty("kind");
});

test("settings saved before the update are dropped and rebuilt", () => {
  const old = {
    v: 1,
    linked: ["s1"],
    settings: { modelId: "m1", promptKey: "built-in", systemPrompt: "Old", off: ["ssh_run"] },
  };
  expect(restoreSettings(options, old)).toEqual(defaultSettings(options));
});

test("with no library prompts the person writes their own", () => {
  const none = { ...options, prompts: [], default_prompt_id: null };
  expect(defaultSettings(none)).toMatchObject({ promptKey: OWN, systemPrompt: "" });
});

test("restored settings drop what the options no longer have", () => {
  const linked = ["s1", "s2", "v1"];
  const saved = {
    v: 2,
    linked,
    settings: {
      modelId: "gone",
      promptKey: "gone",
      systemPrompt: "Mine.",
      subagents: true,
      chosen: {
        server_ids: ["s1", "s9"],
        service_ids: [],
        environment_ids: [],
        code_project_ids: ["c1"],
        collection_ids: [],
      },
      off: ["ssh_run", "gone_tool"],
      experts: ["e1", "e9"],
    },
  };
  expect(restoreSettings(options, saved)).toEqual({
    modelId: "m2",
    promptKey: "p2",
    systemPrompt: "Mine.",
    subagents: true,
    chosen: { server_ids: ["s1"], service_ids: [], environment_ids: [], code_project_ids: ["c1"], collection_ids: [] },
    off: ["ssh_run"],
    experts: ["e1"],
  });
  // the document's links changed since: the resources start again from the links
  expect(restoreSettings(options, { ...saved, linked: ["s1"] }).chosen).toEqual(defaultSettings(options).chosen);
  expect(restoreSettings(options, "garbage")).toEqual(defaultSettings(options));
});

test("settingsFromSession rebuilds the settings a run started with", () => {
  const session = {
    model_id: "m1",
    prompt_snapshot: { system_prompt: "Run prompt.", prompt_id: null },
    run_resources: {
      server_ids: ["s1"],
      service_ids: [],
      environment_ids: [],
      code_project_ids: ["c1"],
      collection_ids: [],
    },
    config: {
      tools: {
        items: [
          { tool: "ssh_run", mode: "on" },
          { tool: "read_file", mode: "off" },
          { tool: "get_resource", mode: "on" },
          { tool: "run_subagent", mode: "on" },
          { tool: "get_doc", mode: "on" },
        ],
      },
      experts: [{ id: "e1", name: "Payments expert" }],
    },
  } as unknown as SessionDetail;
  expect(settingsFromSession(options, session)).toEqual({
    modelId: "m1",
    promptKey: OWN,
    systemPrompt: "Run prompt.",
    chosen: { server_ids: ["s1"], service_ids: [], environment_ids: [], code_project_ids: ["c1"], collection_ids: [] },
    off: ["read_file"],
    subagents: true,
    experts: ["e1"],
  });
});

test("settingsFromSession restores what the run was started for, not what an environment brought", () => {
  const withEnvironment = {
    ...options,
    resources: {
      ...options.resources,
      environments: [{ id: "e1", name: "Production", linked: false, brings_code: true, brings_servers: true }],
    },
  };
  const session = {
    model_id: "m1",
    prompt_snapshot: { system_prompt: "Run prompt.", prompt_id: null },
    run_resources: { server_ids: ["s1"], service_ids: ["v1"], code_project_ids: ["c1"], collection_ids: [] },
    chosen_resources: {
      server_ids: [],
      service_ids: [],
      environment_ids: ["e1"],
      code_project_ids: ["c1"],
      collection_ids: [],
    },
    config: { tools: { items: [] } },
  } as unknown as SessionDetail;
  expect(settingsFromSession(withEnvironment, session).chosen).toEqual({
    server_ids: [],
    service_ids: [],
    environment_ids: ["e1"],
    code_project_ids: ["c1"],
    collection_ids: [],
  });
});

test("settingsFromSession leaves out what today's options no longer have or cannot use", () => {
  const session = {
    model_id: "gone-model",
    prompt_snapshot: { system_prompt: "Run prompt.", prompt_id: null },
    run_resources: {
      server_ids: ["s1", "s2", "s9"],
      service_ids: ["v1"],
      environment_ids: [],
      code_project_ids: ["c9"],
      collection_ids: [],
    },
    config: {
      tools: {
        items: [
          { tool: "ssh_run", mode: "off" },
          { tool: "gone_tool", mode: "off" },
        ],
      },
      experts: [{ id: "e1" }, { id: "e9" }],
    },
  } as unknown as SessionDetail;
  expect(settingsFromSession(options, session)).toEqual({
    modelId: "m2",
    promptKey: OWN,
    systemPrompt: "Run prompt.",
    chosen: { server_ids: ["s1"], service_ids: ["v1"], environment_ids: [], code_project_ids: [], collection_ids: [] },
    off: ["ssh_run"],
    subagents: false,
    experts: ["e1"],
  });
});

test("settingsFromSession: a saved agent that no longer allows subagents gets them off", () => {
  const session = {
    model_id: "m1",
    prompt_snapshot: { system_prompt: "x", prompt_id: null },
    run_resources: {},
    config: { tools: { items: [{ tool: "run_subagent", mode: "on" }] } },
  } as unknown as SessionDetail;
  expect(settingsFromSession(agentOptions, session).subagents).toBe(true);
  const solo = { ...agentOptions, agent: { ...agentOptions.agent!, subagents_allowed: false } };
  expect(settingsFromSession(solo, session).subagents).toBe(false);
});

test("the summary names the model, up to three resources and the tools on", () => {
  expect(settingsSummary(options, defaultSettings(options))).toBe("Claude · web-1, Payments · 3 tools");
  const many = {
    ...defaultSettings(options),
    chosen: {
      server_ids: ["s1"],
      service_ids: ["v1"],
      environment_ids: [],
      code_project_ids: ["c1"],
      collection_ids: [],
    },
  };
  expect(settingsSummary(options, { ...many, chosen: { ...many.chosen, server_ids: ["s1", "s2"] } })).toBe(
    "Claude · web-1, prod-1, Payments +1 · 4 tools",
  );
  expect(
    settingsSummary(options, {
      ...defaultSettings(options),
      chosen: { server_ids: [], service_ids: [], environment_ids: [], code_project_ids: [], collection_ids: [] },
    }),
  ).toBe("Claude · no resources · 2 tools");
});

test("switching the document never writes the first document's settings under the second's key", async () => {
  const { renderHook, waitFor, act } = await import("@testing-library/react");
  const { useAgentSettings } = await import("~/features/sessions/agentSettings");
  const store = new Map<string, string>();
  const writes: [string, string][] = [];
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => {
      writes.push([k, v]);
      store.set(k, v);
    },
    removeItem: (k: string) => store.delete(k),
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async (url: string) =>
        new Response(JSON.stringify(url.includes("doc-2") ? { ...options, default_model_id: "m1" } : options), {
          status: 200,
        }),
    ),
  );
  const { result, rerender } = renderHook(({ id }) => useAgentSettings({ kind: "document", id, title: "" }), {
    initialProps: { id: "doc-1" },
  });
  await waitFor(() => expect(result.current.settings?.modelId).toBe("m2"));
  act(() => result.current.update({ systemPrompt: "Doc one prompt." }));
  await waitFor(() =>
    expect(writes.some(([k, v]) => k === "ask-agent-settings:doc-1" && v.includes("Doc one prompt."))).toBe(true),
  );
  rerender({ id: "doc-2" });
  expect(result.current.options).toBeNull();
  expect(result.current.settings).toBeNull();
  await waitFor(() => expect(result.current.settings?.modelId).toBe("m1"));
  expect(result.current.settings?.systemPrompt).toBe(options.prompts[1].content);
  for (const [key, value] of writes)
    if (key === "ask-agent-settings:doc-2") expect(value).not.toContain("Doc one prompt.");
  vi.unstubAllGlobals();
});

/** The saved agent with more in its access: a server without usable SSH, an environment and two documents. */
const fuller = {
  ...agentOptions,
  resources: {
    ...agentOptions.resources,
    servers: [
      ...agentOptions.resources.servers,
      { id: "s2", name: "db-1", linked: false, usable: false, reason: "SSH is not set up" },
    ],
    environments: [{ id: "env", name: "Production", linked: false, brings_code: false, brings_servers: false }],
    documents: [
      { id: "d1", name: "Runbook", linked: false, editable: true },
      { id: "d2", name: "Guide", linked: false, editable: false },
    ],
  },
};
const agentScope = { kind: "agent" as const, id: "ops", name: "Operations" };

test("a saved agent starts with its own configuration and only narrows it", () => {
  const settings = {
    ...defaultSettings(fuller),
    chosen: { ...defaultSettings(fuller).chosen, server_ids: ["s1"] },
    documents: ["d1"],
    off: ["read_file"],
  };
  const body = startInput(agentScope, fuller, settings, " Check disk ");
  expect(body).toEqual({
    agent_id: "ops",
    message: "Check disk",
    context: "",
    tools: ["ssh_run"],
    expert_ids: ["e1"],
    subagents: true,
    resources: {
      server_ids: ["s1"],
      service_ids: [],
      environment_ids: ["env"],
      code_project_ids: [],
      collection_ids: [],
      documents: [{ id: "d1", editable: true }],
    },
  });
});

test("a saved agent's documents are sent with the agent's own editable flag", () => {
  const body = startInput(agentScope, fuller, defaultSettings(fuller), "Go");
  expect(body.resources.documents).toEqual([
    { id: "d1", editable: true },
    { id: "d2", editable: false },
  ]);
});

test("a saved agent defaults: its model and prompt, every resource of its access chosen", () => {
  expect(defaultSettings(fuller)).toEqual({
    modelId: "m1",
    promptKey: OWN,
    systemPrompt: "You operate servers.",
    chosen: {
      server_ids: ["s1", "s2"],
      service_ids: [],
      environment_ids: ["env"],
      code_project_ids: [],
      collection_ids: [],
    },
    documents: ["d1", "d2"],
    off: [],
    subagents: true,
    experts: ["e1"],
  });
});

test("a saved agent's restored settings keep the picked subset of today's resources", () => {
  const picked = { ...defaultSettings(fuller), documents: ["d2", "gone"] };
  const saved = { v: 2, revision: 3, linked: [], settings: picked };
  expect(restoreSettings(fuller, saved).documents).toEqual(["d2"]);
});

test("settings saved for an older revision of the agent are dropped", () => {
  const saved = { v: 2, revision: 2, linked: [], settings: { ...defaultSettings(agentOptions), off: ["ssh_run"] } };
  expect(restoreSettings(agentOptions, saved)).toEqual(defaultSettings(agentOptions));
  expect(restoreSettings(agentOptions, { ...saved, revision: 3 }).off).toEqual(["ssh_run"]);
});

test("a new document: the start body names it as started_from and as the one editable document", () => {
  const fresh = { ...options, resources: { ...options.resources, documents: [] } };
  const body = startInput(
    { kind: "new-document", folder: "payments", title: "Payments API" },
    fresh,
    defaultSettings(fresh),
    " Write it. ",
  );
  expect(body).toEqual({
    started_from: { new_document: { folder: "payments", name: "Payments API" } },
    message: "Write it.",
    model_id: "m2",
    prompt_id: "p2",
    system_prompt: options.prompts[1].content,
    context: "",
    resources: {
      server_ids: ["s1"],
      service_ids: ["v1"],
      environment_ids: [],
      code_project_ids: [],
      collection_ids: [],
      documents: [{ new: { folder: "payments", name: "Payments API" }, editable: true }],
    },
    tools: ["ssh_run", "save_document", "get_document"],
    expert_ids: [],
    subagents: false,
  });
});

test("the start body for a server: the server is chosen even when not linked, so ssh_run is shown and sent", () => {
  const base = defaultSettings(options);
  const settings = { ...base, chosen: { ...base.chosen, server_ids: [], service_ids: [] } };
  const scope = { kind: "server" as const, id: "s1", name: "web-1" };
  expect(shownTools(options, settings).map((tool) => tool.name)).not.toContain("ssh_run");
  expect(shownTools(options, withScope(scope, settings)).map((tool) => tool.name)).toContain("ssh_run");
  const body = startInput(scope, options, settings, " Explore. ");
  expect(body).toMatchObject({
    started_from: { server_id: "s1" },
    message: "Explore.",
    context: "",
    prompt_id: base.promptKey,
    resources: { ...settings.chosen, server_ids: ["s1"], documents: [] },
    expert_ids: [],
    subagents: false,
  });
  expect(body.tools).toContain("ssh_run");
});

test("the start body for an inventory document: its resource is chosen, a server only when the run can use it", () => {
  const base = defaultSettings(options);
  const settings = { ...base, chosen: { ...base.chosen, server_ids: [], service_ids: [] } };
  const inventory = (type: "server" | "service", id: string) => ({
    kind: "new-document" as const,
    folder: "payments",
    title: "Payments API",
    inventoryFor: { type, id },
  });
  const server = startInput(inventory("server", "s1"), options, settings, "Write it.");
  expect(server.resources.server_ids).toEqual(["s1"]);
  const written = { folder: "payments", name: "Payments API", tags: ["inventory"], server_id: "s1" };
  expect(server.started_from).toEqual({ new_document: written });
  expect(server.resources.documents).toEqual([{ new: written, editable: true }]);
  expect(server.tools).toContain("ssh_run");
  // prod-1 has no usable SSH: the run starts without it (the start says so)
  const unusable = startInput(inventory("server", "s2"), options, settings, "Write it.");
  expect(unusable.resources.server_ids).toEqual([]);
  expect(unusable.tools).not.toContain("ssh_run");
  const service = startInput(inventory("service", "v1"), options, settings, "Write it.");
  expect(service.resources.service_ids).toEqual(["v1"]);
  expect(shownTools(options, withScope(inventory("service", "v1"), settings, options)).map((t) => t.name)).toContain(
    "ssh_run",
  );
});
