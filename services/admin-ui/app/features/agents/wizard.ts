import { request } from "~/lib/api";
import type { AccessType } from "./api";
import type { CatalogTool, ToolSwitches } from "./tools";

export interface WizardResource {
  id: string;
  name: string;
  description?: string;
}
/** A reference of a proposed agent's access; `editable` for documents only. */
export interface WizardReference {
  type: AccessType;
  id: string;
  editable?: boolean;
}
export interface AgentProposal {
  name: string;
  description: string;
  when_to_use: string;
  system_prompt: string;
  instructions: string;
  model_id: string | null;
  enabled: boolean;
  access: { resources: WizardReference[]; roles: { content_editor: boolean } };
}
export interface WizardOptions {
  prompt: string;
  schema: Record<string, unknown>;
  tools?: CatalogTool[];
  catalog: {
    models: (WizardResource & { provider: string; model: string; api_format: string })[];
    code_projects: (WizardResource & { folder_paths: string[]; remote_url: string | null })[];
    collections: { id: string; name: string; description: string }[];
    servers: (WizardResource & { hostname: string; tags: string[]; ssh_ready: boolean })[];
    environments: WizardResource[];
    documents: { id: string; title: string; description?: string }[];
    skills: WizardResource[];
    mcp_servers: WizardResource[];
    services: WizardResource[];
    existing_agents: { name: string; description: string; when_to_use: string }[];
  };
}
export type NewAgent = AgentProposal & {
  tools?: string[];
  /** May start subagents, and the tools a subagent may not be given. */
  subagents?: boolean;
  subagent_blocked?: string[];
};
/** What an access gives the tool catalog rules (the backend's flags without "Answer only from documents"): code
 * projects, collections and servers. */
export const accessSwitches = (resources: { type: string; id: string }[]): ToolSwitches => ({
  repositories_enabled: resources.some((item) => item.type === "code_project"),
  documents_enabled: resources.some((item) => item.type === "collection"),
  ssh_enabled: resources.some((item) => item.type === "server"),
});
export const getWizardOptions = () => request<WizardOptions>("/api/agents/wizard/options");
export const generateAgent = (role: string, model_id: string) =>
  request<{ proposal: AgentProposal }>("/api/agents/wizard/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role, model_id }),
  });
export const createWizardAgent = (value: NewAgent) =>
  request<{ id: string }>("/api/agents/wizard", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  });
