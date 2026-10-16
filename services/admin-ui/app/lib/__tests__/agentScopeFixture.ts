import type { AgentOptions } from "~/features/sessions/api";

/** Options for the saved agent "Operations" (a per-run agent: it brings no resources of its own). */
export const agentOptions: AgentOptions = {
  agent: {
    id: "ops",
    name: "Operations",
    revision: 3,
    model: { id: "m1", name: "GPT" },
    system_prompt: "You operate servers.",
    subagents_allowed: true,
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
    collections: [],
    documents: [],
  },
  tools: [
    {
      name: "ssh_run",
      label: "SSH",
      summary: "Run a command",
      group: "servers",
      needs: "ssh",
      requires: "at least one server",
    },
    {
      name: "read_file",
      label: "Read file",
      summary: "Read a file",
      group: "code",
      needs: "files",
      requires: "a code project or a collection",
    },
  ],
  experts: [{ id: "e1", name: "Payments expert", when_to_use: "Payments questions" }],
};
