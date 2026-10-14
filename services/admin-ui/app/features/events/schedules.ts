import { request } from "~/lib/api";

export type RunStatus = "succeeded" | "failed" | "skipped";
export interface Schedule {
  id: string; name: string; target_kind: string; target_id: string; cron: string; description: string; timezone: string;
  enabled: boolean; next_run_at: string | null; running: boolean; last_run_at: string | null; last_finished_at: string | null;
  last_status: RunStatus | null; last_message: string | null; revision: number; params: Record<string, unknown>;
}
export interface ScheduleRun { id: number; trigger: "schedule" | "manual"; started_at: string; finished_at: string; status: RunStatus; message: string }
export interface ScheduleInput { cron: string; timezone: string; enabled: boolean }
export interface AutoPullInput extends ScheduleInput { jql: string; limit: number }
export interface AutoPull { schedule: Schedule | null; jql: string | null; limit: number | null; connected: boolean }

export const PRESETS = [
  { cron: "*/15 * * * *", label: "Every 15 minutes" },
  { cron: "*/30 * * * *", label: "Every 30 minutes" },
  { cron: "0 * * * *", label: "Every hour" },
  { cron: "0 */6 * * *", label: "Every 6 hours" },
  { cron: "0 6 * * *", label: "Every day at 06:00" },
];
export const DEFAULT_CRON = PRESETS[0].cron;
export const TARGETS: Record<string, string> = { "jira.pull": "Jira tickets" };
export const STATUS_LABELS: Record<RunStatus, string> = { succeeded: "Succeeded", failed: "Failed", skipped: "Skipped" };

const send = (method: string, body: unknown): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const path = (id: string) => `/api/schedules/${encodeURIComponent(id)}`;

export const getSchedules = (signal?: AbortSignal) => request<{ schedules: Schedule[] }>("/api/schedules", { signal });
export const getScheduleRuns = (id: string, signal?: AbortSignal) => request<{ runs: ScheduleRun[] }>(`${path(id)}/runs`, { signal });
export const saveSchedule = (id: string, value: ScheduleInput & { revision: number }) => request<{ schedule: Schedule }>(path(id), send("PUT", value));
export const createSchedule = (value: ScheduleInput & { target_kind: string }) => request<{ schedule: Schedule }>("/api/schedules", send("POST", value));
export const deleteSchedule = (id: string) => request<void>(path(id), { method: "DELETE" });
export const runSchedule = (id: string) => request<{ schedule: Schedule }>(`${path(id)}/run`, { method: "POST" });
export const previewSchedule = (cron: string, timezone: string) => request<{ runs: string[] }>("/api/schedules/preview", send("POST", { cron, timezone }));
export const getAutoPull = (signal?: AbortSignal) => request<AutoPull>("/api/jira/auto-pull", { signal });
export const saveAutoPull = (value: AutoPullInput) => request<AutoPull>("/api/jira/auto-pull", send("PUT", value));

export function browserTimeZone(): string {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch { return "UTC"; }
}

/** IANA time zones for the picker: UTC first, and the current value kept even if this browser does not list it. */
export function timeZones(current: string): string[] {
  let zones: string[] = [];
  try { zones = Intl.supportedValuesOf("timeZone"); } catch { zones = []; }
  return [...new Set(["UTC", current, ...zones])];
}
