import { request } from './api';
import type { AgentRuntime, ClaudeCodeTool } from './profiles';

export type ToolGroup = 'code' | 'documents' | 'servers' | 'mcp' | 'agents';
export type ToolNeed = 'files' | 'code' | 'code_graph' | 'documents' | 'grounded' | 'ssh';
export interface AgentTool {
  tool: string; label: string; summary: string; group: ToolGroup; mode: 'on' | 'off' | 'per_run';
  usage: string | null; default_usage: string; subagent_allowed: boolean; available: boolean; reason: string | null; tab: string | null;
}
export interface AgentTools { runtime: AgentRuntime; revision: number; claude_code_tools?: ClaudeCodeTool[]; tools: AgentTool[] }
export interface CatalogTool { name: string; group: ToolGroup; label: string; summary: string; usage: string; needs: ToolNeed | null }
export interface ToolsSummary { on?: number; per_run?: number; groups?: Partial<Record<ToolGroup, number>>; claude_code_tools?: string[] }
export const TOOL_GROUPS: [ToolGroup, string][] = [['code', 'Code'], ['documents', 'Documents'], ['servers', 'Servers'], ['mcp', 'MCP'], ['agents', 'Agents']];
export const NEED_REASONS: Record<ToolNeed, string> = {
  files: 'Needs code projects, or documents without "Answer only from documents"', code: 'Needs code projects',
  code_graph: 'Needs code projects', documents: 'Needs documents', grounded: 'Needs "Answer only from documents"', ssh: 'Needs SSH servers' };
export const CLAUDE_CODE_TOOLS_NOTE = "Claude Code agents use Claude Code's own tools on their working folders. Switch them on the General tab.";
export const TOOLS_TAB_NOTE = 'Tools for these projects are chosen on the Tools tab.';
export const TAB_LABELS: Record<string, string> = { git: 'Code projects', documents: 'Documents', ssh: 'SSH', mcp: 'MCP' };

const path = (id: string) => `/api/agents/${encodeURIComponent(id)}/tools`;
export const getToolCatalog = () => request<{ tools: CatalogTool[] }>('/api/tools');
export const getAgentTools = (id: string) => request<AgentTools>(path(id));
/** Replaces the agent's tools; a usage of null keeps the built-in text. */
export const saveAgentTools = (id: string, revision: number, tools: AgentTool[]) => request<AgentTools>(path(id), {
  method: 'PUT', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ expected_revision: revision,
    tools: tools.map(({ tool, mode, usage }) => ({ tool, mode: mode === 'on' ? 'on' : 'off', usage })) }),
});

export interface ToolSwitches { repositories_enabled: boolean; documents_enabled: boolean; documents_grounded?: boolean; ssh_enabled: boolean }
/** The capability rule of the backend catalog (needs_met without run-time facts). */
export function toolNeedMet(need: ToolNeed | null, s: ToolSwitches): boolean {
  const grounded = s.documents_enabled && Boolean(s.documents_grounded);
  switch (need) {
    case null: return true;
    case 'files': return s.repositories_enabled || s.documents_enabled && !grounded;
    case 'code': case 'code_graph': return s.repositories_enabled;
    case 'documents': return s.documents_enabled;
    case 'grounded': return grounded;
    case 'ssh': return s.ssh_enabled;
  }
}
/** The tools a new agent gets from its capabilities (the backend's implied_tools). */
export const impliedTools = (catalog: CatalogTool[], s: ToolSwitches) => catalog.filter(t => toolNeedMet(t.needs, s)).map(t => t.name);
/** All agents: "7 tools · Code 6 · Servers 1"; Claude Code agents: "Read, Grep, Glob, Bash". */
export function toolsSummaryLabel(summary?: ToolsSummary): string {
  if (!summary) return '—';
  if (summary.claude_code_tools) return summary.claude_code_tools.length ? summary.claude_code_tools.join(', ') : 'No tools';
  if (!summary.on) return 'No tools';
  const parts = [`${summary.on} tool${summary.on === 1 ? '' : 's'}`,
    ...TOOL_GROUPS.filter(([group]) => summary.groups?.[group]).map(([group, label]) => `${label} ${summary.groups![group]}`)];
  if (summary.per_run) parts.push(`+${summary.per_run} per run`);
  return parts.join(' · ');
}
