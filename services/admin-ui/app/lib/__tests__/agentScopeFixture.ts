import type { AgentOptions } from "~/features/sessions/api";

/** Options for the saved agent "Operations" (a per-run agent: it brings no resources of its own). */
export const agentOptions: AgentOptions = {
  agent: {
    id: "ops",
    name: "Operations",
    revision: 3,
    model: { id: "m1", name: "GPT" },
    system_prompt: "You operate servers.",
  },
  models: [{ id: "m1", name: "GPT" }],
  default_model_id: "m1",
  prompts: [],
  default_prompt_id: null,
  resources: {
    servers: [{ id: "s1", name: "web-1", linked: false, usable: true, reason: null }],
    services: [],
    environments: [],
    code_projects: [],
    documents: [],
  },
  experts: [{ id: "e1", name: "Payments expert", when_to_use: "Payments questions" }],
};
