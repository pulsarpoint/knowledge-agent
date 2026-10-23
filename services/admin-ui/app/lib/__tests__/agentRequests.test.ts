import { afterEach, expect, test, vi } from "vitest";
import { saveAgent, type AgentProfile } from "~/features/agents/api";

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
  for (const gone of ["skill_ids", "collection_ids", "all_collections", "documents_enabled", "resources", "subagents"])
    expect(body).not.toHaveProperty(gone);
});

test("Save sends only the content editor role", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(lead)),
  );
  await saveAgent({
    ...lead,
    documents_grounded: false,
    model_id: null,
    system_prompt: "p",
    instructions: "",
    access: { resources: [], roles: { content_editor: false } },
  });
  expect(sent().access.roles).toEqual({ content_editor: false });
});
