import { request } from './api';

export interface BoardTask {
  id: string; agent_id: string | null; agent_name: string; task: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
  cancel_requested: boolean; model_name: string | null; prompt_version: number | null;
  result: string | null; error: string | null; usage: unknown;
  created_at: string; started_at: string | null; finished_at: string | null;
  parent_id: string | null; phase: 'agent' | 'planning' | 'working' | 'reviewing' | 'done'; plan: string;
  trace_id: string | null; span_id: string | null; trace_url: string | null;
}

export interface AgentActivity {
  id: string; task_id: string; kind: 'tool' | 'agentMessage' | 'commandExecution' | 'mcpToolCall' | 'plan' | 'delegation';
  title: string; detail: string; status: string; time: string;
}

export interface TeamTask extends BoardTask {
  subagents: { id: string; name: string }[];
  subtasks: BoardTask[];
}

export const getBoard = (offset: number, signal?: AbortSignal) =>
  request<{ tasks: TeamTask[]; has_more: boolean }>(`/api/agent-board/tasks?offset=${offset}`, { signal });
export const submitBoardTask = (value: { id: string; subagent_ids: string[]; task: string }) =>
  request<{ id: string }>('/api/agent-board/tasks', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value),
  });
export const cancelBoardTask = (id: string) =>
  request<{ id: string }>(`/api/agent-board/tasks/${encodeURIComponent(id)}/cancel`, { method: 'POST' });

export interface CoordinatorSettings {
  agent_id: string; name: string; enabled: boolean; notes: string;
  model_id: string | null; model_name: string | null; model_enabled: boolean | null;
  system_prompt: string; summary_prompt: string; max_turns: number; max_subtasks: number; timeout_seconds: number; revision: number;
}
export const getCoordinatorSettings = () => request<CoordinatorSettings>('/api/agent-board/settings');
export const saveCoordinatorSettings = (settings: Pick<CoordinatorSettings, 'notes' | 'summary_prompt' | 'max_turns' | 'max_subtasks' | 'timeout_seconds' | 'revision'>) =>
  request<CoordinatorSettings>('/api/agent-board/settings', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(settings),
  });
