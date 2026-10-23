import {
  OWN,
  defaultSettings,
  pickReason,
  restoreSettings,
  settingsFromSession,
  settingsSummary,
  startInput,
  withScope,
} from "~/features/sessions/agentSettings";
import type { SessionDetail } from "~/features/sessions/api";
import { options } from "./agentOptionsFixture";
import { agentOptions } from "./agentScopeFixture";

const doc = (title = "Runbook") => ({ kind: "document" as const, id: "doc-1", title });

test("defaults: the default model, the first tagged prompt, the usable linked resources", () => {
  const settings = defaultSettings(options);
  expect(settings).toEqual({
    modelId: "m2",
    promptKey: "p2",
    systemPrompt: options.prompts[1].content,
    chosen: { server_ids: ["s1"], service_ids: ["v1"], environment_ids: [], code_project_ids: [], collection_ids: [] },
    experts: [],
  });
});

test("startInput sends no tool choice (every run gets every tool) and no content editor request", () => {
  const settings = defaultSettings(options);
  const body = startInput(doc(), options, settings, "x");
  for (const gone of ["tools", "subagents"]) expect(body).not.toHaveProperty(gone);
  expect(startInput(doc(), options, { ...settings, promptKey: OWN }, "x").prompt_id).toBeNull();
});

test("the start body gives the document as a resource and the context names it", () => {
  const body = startInput(doc(), options, defaultSettings(options), "  Add a section.  ");
  expect(body).toMatchObject({
    started_from: { document_id: "doc-1" },
    message: "Add a section.",
    prompt_id: "p2",
    system_prompt: options.prompts[1].content,
    context: "Document: Runbook (id doc-1)",
  });
  expect(body.resources.documents).toEqual([{ id: "doc-1" }]);
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
      subagents: true, // saved before tools were always on: dropped
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
    chosen: { server_ids: ["s1"], service_ids: [], environment_ids: [], code_project_ids: ["c1"], collection_ids: [] },
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
    given_resources: [
      { type: "server", id: "s1" },
      { type: "code_project", id: "c1" },
      { type: "document", id: "d1" },
    ],
    config: {
      experts: [{ id: "e1", name: "Payments expert" }],
    },
  } as unknown as SessionDetail;
  expect(settingsFromSession(options, session)).toEqual({
    modelId: "m1",
    promptKey: OWN,
    systemPrompt: "Run prompt.",
    chosen: { server_ids: ["s1"], service_ids: [], environment_ids: [], code_project_ids: ["c1"], collection_ids: [] },
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
    // the run reached v1 and s1 through e1: they are not what it was given
    given_resources: [
      { type: "environment", id: "e1" },
      { type: "code_project", id: "c1" },
    ],
    config: {},
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
    given_resources: [
      { type: "server", id: "s1" },
      { type: "server", id: "s2" },
      { type: "server", id: "s9" },
      { type: "service", id: "v1" },
      { type: "code_project", id: "c9" },
    ],
    config: {
      experts: [{ id: "e1" }, { id: "e9" }],
    },
  } as unknown as SessionDetail;
  expect(settingsFromSession(options, session)).toEqual({
    modelId: "m2",
    promptKey: OWN,
    systemPrompt: "Run prompt.",
    chosen: { server_ids: ["s1"], service_ids: ["v1"], environment_ids: [], code_project_ids: [], collection_ids: [] },
    experts: ["e1"],
  });
});

test("the summary names the model and up to three resources", () => {
  expect(settingsSummary(options, defaultSettings(options))).toBe("Claude · web-1, Payments");
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
    "Claude · web-1, prod-1, Payments +1",
  );
  expect(
    settingsSummary(options, {
      ...defaultSettings(options),
      chosen: { server_ids: [], service_ids: [], environment_ids: [], code_project_ids: [], collection_ids: [] },
    }),
  ).toBe("Claude · no resources");
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
      { id: "d1", name: "Runbook", linked: false },
      { id: "d2", name: "Guide", linked: false },
    ],
  },
};
const agentScope = { kind: "agent" as const, id: "ops", name: "Operations" };

test("a saved agent starts with its own configuration and only narrows it", () => {
  const settings = {
    ...defaultSettings(fuller),
    chosen: { ...defaultSettings(fuller).chosen, server_ids: ["s1"] },
    documents: ["d1"],
  };
  const body = startInput(agentScope, fuller, settings, " Check disk ");
  expect(body).toEqual({
    agent_id: "ops",
    message: "Check disk",
    context: "",
    expert_ids: ["e1"],
    resources: {
      server_ids: ["s1"],
      service_ids: [],
      environment_ids: ["env"],
      code_project_ids: [],
      collection_ids: [],
      documents: [{ id: "d1" }],
    },
  });
});

test("a saved agent's documents are sent as picked", () => {
  const body = startInput(agentScope, fuller, defaultSettings(fuller), "Go");
  expect(body.resources.documents).toEqual([{ id: "d1" }, { id: "d2" }]);
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
    experts: ["e1"],
  });
});

test("a saved agent with resources needs at least one picked; one without resources does not", () => {
  const none = { server_ids: [], service_ids: [], environment_ids: [], code_project_ids: [], collection_ids: [] };
  expect(pickReason(fuller, defaultSettings(fuller))).toBeNull();
  expect(pickReason(fuller, { ...defaultSettings(fuller), chosen: none, documents: [] })).toBe(
    "Pick at least one resource",
  );
  expect(pickReason(fuller, { ...defaultSettings(fuller), chosen: none, documents: ["d2"] })).toBeNull();
  const empty = { ...fuller, resources: { ...fuller.resources, servers: [], environments: [], documents: [] } };
  expect(pickReason(empty, { ...defaultSettings(empty), chosen: none, documents: [] })).toBeNull();
  expect(pickReason(options, { ...defaultSettings(options), chosen: none })).toBeNull(); // not a saved agent
});

test("a saved agent's restored settings keep the picked subset of today's resources", () => {
  const picked = { ...defaultSettings(fuller), documents: ["d2", "gone"] };
  const saved = { v: 2, revision: 3, linked: [], settings: picked };
  expect(restoreSettings(fuller, saved).documents).toEqual(["d2"]);
});

test("settings saved for an older revision of the agent are dropped", () => {
  const saved = { v: 2, revision: 2, linked: [], settings: { ...defaultSettings(agentOptions), experts: [] } };
  expect(restoreSettings(agentOptions, saved)).toEqual(defaultSettings(agentOptions));
  expect(restoreSettings(agentOptions, { ...saved, revision: 3 }).experts).toEqual([]);
});

test("a new document: the start body names it as started_from and as the one document it writes", () => {
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
      documents: [{ new: { folder: "payments", name: "Payments API" } }],
    },
    expert_ids: [],
  });
});

test("the start body for a server: the server is chosen even when not linked", () => {
  const base = defaultSettings(options);
  const settings = { ...base, chosen: { ...base.chosen, server_ids: [], service_ids: [] } };
  const scope = { kind: "server" as const, id: "s1", name: "web-1" };
  expect(withScope(scope, settings).chosen.server_ids).toEqual(["s1"]);
  const body = startInput(scope, options, settings, " Explore. ");
  expect(body).toMatchObject({
    started_from: { server_id: "s1" },
    message: "Explore.",
    context: "",
    prompt_id: base.promptKey,
    resources: { ...settings.chosen, server_ids: ["s1"], documents: [] },
    expert_ids: [],
  });
});

test("the start body for a resource's new document: its resource is chosen, a server only when the run can use it", () => {
  const base = defaultSettings(options);
  const settings = { ...base, chosen: { ...base.chosen, server_ids: [], service_ids: [] } };
  const scope = (type: "server" | "service", id: string) => ({
    kind: "new-document" as const,
    folder: "payments",
    title: "Payments API",
    resourceFor: { type, id },
  });
  const server = startInput(scope("server", "s1"), options, settings, "Write it.");
  expect(server.resources.server_ids).toEqual(["s1"]);
  const written = { folder: "payments", name: "Payments API", server_id: "s1" };
  expect(server.started_from).toEqual({ new_document: written });
  expect(server.resources.documents).toEqual([{ new: written }]);
  // prod-1 has no usable SSH: the run starts without it (the start says so)
  const unusable = startInput(scope("server", "s2"), options, settings, "Write it.");
  expect(unusable.resources.server_ids).toEqual([]);
  const service = startInput(scope("service", "v1"), options, settings, "Write it.");
  expect(service.resources.service_ids).toEqual(["v1"]);
});
