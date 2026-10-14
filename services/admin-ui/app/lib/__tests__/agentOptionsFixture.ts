import type { AgentOptions } from "~/features/sessions/api";

export const options: AgentOptions = {
  models: [
    { id: "m1", name: "GPT" },
    { id: "m2", name: "Claude" },
  ],
  default_model_id: "m2",
  prompts: [
    { id: "p1", name: "Careful reviser", content: "You revise carefully." },
    {
      id: "p2",
      name: "Revise a document",
      content: "Read with get_document. Change with edit_document or save_document.",
    },
  ],
  default_prompt_id: "p2",
  resources: {
    servers: [
      { id: "s1", name: "web-1", linked: true, usable: true, reason: null },
      { id: "s2", name: "prod-1", linked: true, usable: false, reason: "SSH is not set up for this server." },
    ],
    services: [{ id: "v1", name: "Payments", linked: true, brings_code: false, brings_servers: true }],
    environments: [],
    code_projects: [{ id: "c1", name: "Shop", linked: false }],
    collections: [],
    documents: [{ id: "doc-1", name: "Runbook", linked: true, editable: true }],
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
    {
      name: "save_document",
      label: "Save a document",
      summary: "Save the draft",
      group: "documents",
      needs: "editable_document",
      requires: "a document to edit",
    },
    {
      name: "get_document",
      label: "Read document",
      summary: "Read a document",
      group: "inventory",
      needs: null,
      requires: null,
    },
    {
      name: "get_service_info",
      label: "Service info",
      summary: "A service",
      group: "inventory",
      needs: null,
      requires: null,
    },
  ],
  experts: [{ id: "e1", name: "Payments expert", when_to_use: "Payments questions" }],
};
