import { request } from "~/lib/api";
/** The types of resource an agent's access may reference, with their labels, in the order the editor shows them. */
export const ACCESS_TYPES = [
  { type: "server", label: "Servers", one: "server", many: "servers" },
  { type: "service", label: "Services", one: "service", many: "services" },
  { type: "environment", label: "Environments", one: "environment", many: "environments" },
  { type: "code_project", label: "Code projects", one: "code project", many: "code projects" },
  { type: "collection", label: "Collections", one: "collection", many: "collections" },
  { type: "document", label: "Documents", one: "document", many: "documents" },
  { type: "skill", label: "Skills", one: "skill", many: "skills" },
  { type: "mcp_server", label: "MCP servers", one: "MCP server", many: "MCP servers" },
] as const;
export type AccessType = (typeof ACCESS_TYPES)[number]["type"];
/** One reference of an agent's access as the editor shows it: `missing` when the resource no longer exists;
 * `editable` for documents only. */
export interface AccessEntry {
  type: AccessType;
  id: string;
  name: string;
  description_short: string;
  editable?: boolean;
  missing?: boolean;
}
export interface AgentAccess {
  resources: AccessEntry[];
  roles: { content_editor: boolean };
}
export const CONTENT_EDITOR_HELP = "Lets this agent propose services and documents and edit editable documents.";
export const EXPERT_HELP = "Other agents can call this agent as a tool. Its When to use text tells them when.";
/** "1 server · 2 documents", or "No resources". */
export function countsSummary(counts: Partial<Record<AccessType, number>>): string {
  const parts = ACCESS_TYPES.filter(({ type }) => counts[type]).map(
    ({ type, one, many }) => `${counts[type]} ${counts[type] === 1 ? one : many}`,
  );
  return parts.length ? parts.join(" · ") : "No resources";
}
export interface AgentProfile {
  id: string;
  name: string;
  description: string;
  when_to_use: string;
  enabled: boolean;
  expert?: boolean;
  documents_grounded: boolean;
  revision: number;
  model_id: string | null;
  system_prompt: string;
  instructions: string;
  access: AgentAccess;
  cloned_from?: string | null;
  original?: { id: string; name: string } | null;
}
export interface AgentSummary extends AgentProfile {
  counts: Record<AccessType, number>;
  model_name: string | null;
  original_name?: string | null;
}
/** Every resource of the project an agent's access may reference, by type. */
export type AgentOptions = Record<AccessType, AccessEntry[]>;
const json = (method: string, value: unknown) => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(value),
});
const path = (id: string) => `/api/agents/${encodeURIComponent(id)}`;
export const getAgents = () => request<{ agents: AgentSummary[] }>("/api/agents");
export const getAgent = (id: string) => request<AgentProfile>(path(id));
export const getAgentConfig = (id: string) => request<Record<string, unknown>>(path(id) + "/config");
export const getAgentOptions = (id: string) => request<AgentOptions>(path(id) + "/options");
/** Saves the agent: its details and its access object as references (missing ones are dropped; a document's
 * editable is sent, read-only when unset). */
export const saveAgent = (value: AgentProfile) => {
  const {
    revision,
    name,
    description,
    when_to_use,
    enabled,
    documents_grounded,
    instructions,
    access,
    expert,
    model_id,
  } = value;
  return request<AgentProfile>(
    path(value.id),
    json("PUT", {
      revision,
      name,
      description,
      when_to_use,
      enabled,
      documents_grounded,
      instructions,
      access: {
        resources: access.resources
          .filter((item) => !item.missing)
          .map(({ type, id, editable }) =>
            type === "document" ? { type, id, editable: Boolean(editable) } : { type, id },
          ),
        roles: { content_editor: access.roles.content_editor },
      },
      ...(expert !== undefined ? { expert } : {}),
      ...(model_id !== undefined ? { model_id } : {}),
    }),
  );
};
export const saveAgentPrompt = (id: string, revision: number, system_prompt: string) =>
  request<AgentProfile>(path(id) + "/prompt", json("PUT", { revision, system_prompt }));
export const saveProfileModel = (value: AgentProfile, model_id: string | null) =>
  request<AgentProfile>(path(value.id) + "/model", json("PUT", { revision: value.revision, model_id }));

export const deleteAgent = (id: string, revision: number, withHistory = false) =>
  request<void>(
    path(id),
    json("DELETE", { revision, confirmed: true, ...(withHistory ? { with_history: true } : {}) }),
  );
/** What deleting an agent with its history removes; saved documents are kept. `run_records`: Test chats from before
 * the Test tab ran sessions. */
export interface AgentHistory {
  test_chats: number;
  /** Chats started with Run agent on the Agents page. */
  chats: number;
  documentation_sessions: number;
  run_records: number;
}
export const getAgentHistory = (id: string) => request<AgentHistory>(path(id) + "/history");
const HISTORY_LABELS: [keyof AgentHistory, string, string][] = [
  ["test_chats", "test chat", "test chats"],
  ["chats", "chat", "chats"],
  ["documentation_sessions", "documentation session", "documentation sessions"],
  ["run_records", "run record", "run records"],
];
/** "2 test chats, 1 documentation session, 3 run records"; empty when the agent has no history. */
export const describeHistory = (history: AgentHistory) =>
  HISTORY_LABELS.filter(([key]) => history[key] > 0)
    .map(([key, one, many]) => `${history[key]} ${history[key] === 1 ? one : many}`)
    .join(", ");
