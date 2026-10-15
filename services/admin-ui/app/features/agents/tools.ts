import { request } from "~/lib/api";

export type ToolGroup = "code" | "documents" | "servers" | "inventory" | "memory" | "mcp" | "agents" | "experts";
export type ToolNeed = "files" | "code" | "code_graph" | "documents" | "grounded" | "ssh";
export interface AgentTool {
  tool: string;
  label: string;
  summary: string;
  group: ToolGroup;
  mode: "on" | "off" | "per_run";
  usage: string | null;
  default_usage: string;
  subagent_allowed: boolean;
  available: boolean;
  reason: string | null;
  tab: string | null;
  resources?: "fixed" | "per_run";
}
export interface AgentTools {
  revision: number;
  expert?: boolean;
  tools: AgentTool[];
}
export interface CatalogTool {
  name: string;
  group: ToolGroup;
  label: string;
  summary: string;
  usage: string;
  needs: ToolNeed | null;
}
export interface ToolsSummary {
  on?: number;
  per_run?: number;
  groups?: Partial<Record<ToolGroup, number>>;
}
export const TOOL_GROUPS: [ToolGroup, string][] = [
  ["code", "Code"],
  ["documents", "Documents"],
  ["servers", "Servers"],
  ["inventory", "Inventory"],
  ["memory", "Memory"],
  ["mcp", "MCP"],
  ["agents", "Agents"],
  ["experts", "Experts"],
];
export const NEED_REASONS: Record<ToolNeed, string> = {
  files: 'Needs code projects, or documents without "Answer only from documents"',
  code: "Needs code projects",
  code_graph: "Needs code projects",
  documents: "Needs documents",
  grounded: 'Needs "Answer only from documents"',
  ssh: "Needs SSH servers",
};
export const TOOLS_TAB_NOTE = "Tools for these projects are chosen on the Tools tab.";
export const SUBAGENT_TOOL = "run_subagent";
export const SUBAGENTS_HELP = "The agent can hand separate parts of its work to helpers that run in parallel.";
export const SUBAGENTS_OFF_NOTE = "Turn on May start subagents in General to let this agent start helpers.";
export const EXPERT_HELP = "Other agents can call this agent as a tool. Its When to use text tells them when.";
export const NO_EXPERTS_NOTE = "No experts yet. Turn on Offer as an expert on an agent's General tab.";
export const EXPERT_SELF_NOTE = "When this agent runs as an expert it cannot call experts.";
export const TAB_LABELS: Record<string, string> = {
  git: "Code projects",
  documents: "Documents",
  ssh: "SSH",
  mcp: "MCP",
};

const path = (id: string) => `/api/agents/${encodeURIComponent(id)}/tools`;
export const getToolCatalog = () => request<{ tools: CatalogTool[] }>("/api/tools");
export const getAgentTools = (id: string) => request<AgentTools>(path(id));
/** Replaces the agent's tools; a usage of null keeps the built-in text; subagent_allowed: whether run_subagent may give it to a subagent. */
export const saveAgentTools = (id: string, revision: number, tools: AgentTool[]) =>
  request<AgentTools>(path(id), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      expected_revision: revision,
      tools: tools.map(({ tool, mode, usage, subagent_allowed }) => ({
        tool,
        mode: mode === "on" ? "on" : "off",
        usage,
        subagent_allowed,
      })),
    }),
  });

export interface ToolSwitches {
  repositories_enabled: boolean;
  documents_enabled: boolean;
  documents_grounded?: boolean;
  ssh_enabled: boolean;
}
/** The capability rule of the backend catalog (needs_met without run-time facts). */
export function toolNeedMet(need: ToolNeed | null, s: ToolSwitches): boolean {
  const grounded = s.documents_enabled && Boolean(s.documents_grounded);
  switch (need) {
    case null:
      return true;
    case "files":
      return s.repositories_enabled || (s.documents_enabled && !grounded);
    case "code":
    case "code_graph":
      return s.repositories_enabled;
    case "documents":
      return s.documents_enabled;
    case "grounded":
      return grounded;
    case "ssh":
      return s.ssh_enabled;
  }
}
/** The tools a new agent gets from its capabilities (the backend's enabled_capability_tools); never run_subagent,
 * which comes with May start subagents. */
export const impliedTools = (catalog: CatalogTool[], s: ToolSwitches) =>
  catalog
    .filter(
      (t) =>
        t.group !== "agents" &&
        t.group !== "experts" &&
        t.group !== "inventory" &&
        t.group !== "memory" &&
        toolNeedMet(t.needs, s),
    )
    .map((t) => t.name);
/** All agents: "7 tools · Code 6 · Servers 1" */
export function toolsSummaryLabel(summary?: ToolsSummary): string {
  if (!summary) return "—";
  if (!summary.on) return "No tools";
  const parts = [
    `${summary.on} tool${summary.on === 1 ? "" : "s"}`,
    ...TOOL_GROUPS.filter(([group]) => summary.groups?.[group]).map(
      ([group, label]) => `${label} ${summary.groups![group]}`,
    ),
  ];
  if (summary.per_run) parts.push(`+${summary.per_run} per run`);
  return parts.join(" · ");
}
