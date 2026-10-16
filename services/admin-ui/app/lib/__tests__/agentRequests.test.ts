import { afterEach, expect, test, vi } from "vitest";
import { saveAgent, type AgentProfile } from "~/features/agents/api";
import { accessSwitches } from "~/features/agents/wizard";
import { impliedTools, saveAgentTools, type AgentTool, type CatalogTool } from "~/features/agents/tools";

afterEach(() => vi.unstubAllGlobals());
const sent = () => JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body));
const lead = {
  id: "lead",
  name: "Lead",
  description: "",
  when_to_use: "",
  enabled: true,
  revision: 3,
  access: { resources: [], roles: { content_editor: false } },
} as unknown as AgentProfile;

test("a tools save sends what a subagent may use", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ revision: 5, tools: [] })),
  );
  await saveAgentTools("lead", 4, [
    { tool: "read_file", mode: "on", usage: null, subagent_allowed: false } as AgentTool,
  ]);
  expect(sent()).toEqual({
    expected_revision: 4,
    tools: [{ tool: "read_file", mode: "on", usage: null, subagent_allowed: false }],
  });
});

test("General sends May start subagents", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(lead)),
  );
  await saveAgent({ ...lead, subagents: true });
  expect(sent().subagents).toBe(true);
});

test("Save sends the access object as references and drops missing ones", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(lead)),
  );
  await saveAgent({
    ...lead,
    documents_grounded: false,
    model_id: null,
    system_prompt: "p",
    instructions: "Notes",
    access: {
      resources: [
        { type: "server", id: "s1", name: "web-1", description_short: "Web" },
        { type: "document", id: "d1", name: "Runbook", description_short: "", editable: true },
        { type: "document", id: "d2", name: "Guide", description_short: "" },
        { type: "collection", id: "gone", name: "", description_short: "", missing: true },
      ],
      roles: { content_editor: true },
    },
  });
  const body = sent();
  expect(body.access).toEqual({
    resources: [
      { type: "server", id: "s1" },
      { type: "document", id: "d1", editable: true },
      { type: "document", id: "d2", editable: false },
    ],
    roles: { content_editor: true },
  });
  expect(body.instructions).toBe("Notes");
  for (const gone of ["skill_ids", "collection_ids", "all_collections", "documents_enabled", "resources"])
    expect(body).not.toHaveProperty(gone);
});

test("a new agent's tools follow what its access gives", () => {
  expect(
    accessSwitches([
      { type: "code_project", id: "p" },
      { type: "document", id: "d" },
    ]),
  ).toEqual({ repositories_enabled: true, documents_enabled: false, ssh_enabled: false });
  expect(
    accessSwitches([
      { type: "collection", id: "c" },
      { type: "server", id: "s" },
    ]),
  ).toEqual({ repositories_enabled: false, documents_enabled: true, ssh_enabled: true });
});

test("new agents never get run_subagent from their capabilities", () => {
  const catalog: CatalogTool[] = [
    { name: "read_file", group: "code", label: "Read file", summary: "", usage: "", needs: "files" },
    { name: "run_subagent", group: "agents", label: "Start subagents", summary: "", usage: "", needs: null },
  ];
  expect(impliedTools(catalog, { repositories_enabled: true, documents_enabled: false, ssh_enabled: false })).toEqual([
    "read_file",
  ]);
});
