import { request } from "~/lib/api";

export type RunStatus = "succeeded" | "failed" | "skipped";
/** A handler that runs an emit schedule: type pattern takes schedule.fired, condition subject equals the name. */
export interface ScheduleHandler {
  id: string;
  name: string;
  enabled: boolean;
}
export interface Schedule {
  id: string;
  name: string;
  target_kind: string;
  target_id: string;
  cron: string;
  description: string;
  timezone: string;
  enabled: boolean;
  next_run_at: string | null;
  running: boolean;
  last_run_at: string | null;
  last_finished_at: string | null;
  last_status: RunStatus | null;
  last_message: string | null;
  revision: number;
  params: Record<string, unknown>;
  /** Emit event schedules only, and only in the list (create and save responses leave it out). */
  handlers?: ScheduleHandler[];
}
export interface ScheduleRun {
  id: number;
  trigger: "schedule" | "manual";
  started_at: string;
  finished_at: string;
  status: RunStatus;
  message: string;
}
/** What a jira.pull schedule fetches; a missing value falls back to the last manual fetch. */
export interface JiraPullParams {
  jql?: string;
  limit?: number;
}
export interface ScheduleInput {
  /** Emit event schedules only: the Jira pull schedule keeps its fixed name. */
  name?: string;
  cron: string;
  timezone: string;
  enabled: boolean;
  params?: JiraPullParams;
}

export const PRESETS = [
  { cron: "*/15 * * * *", label: "Every 15 minutes" },
  { cron: "*/30 * * * *", label: "Every 30 minutes" },
  { cron: "0 * * * *", label: "Every hour" },
  { cron: "0 */6 * * *", label: "Every 6 hours" },
  { cron: "0 6 * * *", label: "Every day at 06:00" },
];
export const DEFAULT_CRON = PRESETS[0].cron;
export const EMIT = "event.emit";
export const TARGETS: Record<string, string> = { "jira.pull": "Jira tickets", [EMIT]: "Emit event" };
export const STATUS_LABELS: Record<RunStatus, string> = {
  succeeded: "Succeeded",
  failed: "Failed",
  skipped: "Skipped",
};

const send = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
const path = (id: string) => `/api/schedules/${encodeURIComponent(id)}`;

export const getSchedules = (signal?: AbortSignal) => request<{ schedules: Schedule[] }>("/api/schedules", { signal });
export const getScheduleRuns = (id: string, signal?: AbortSignal) =>
  request<{ runs: ScheduleRun[] }>(`${path(id)}/runs`, { signal });
/** handlers_updated: a rename rewrites the subject condition of the handlers bound to the old name. */
export const saveSchedule = (id: string, value: ScheduleInput & { revision: number }) =>
  request<{ schedule: Schedule; handlers_updated: number }>(path(id), send("PUT", value));
export const createSchedule = (value: ScheduleInput & { target_kind: string }) =>
  request<{ schedule: Schedule }>("/api/schedules", send("POST", value));
export const deleteSchedule = (id: string) => request<void>(path(id), { method: "DELETE" });
export const runSchedule = (id: string) => request<{ schedule: Schedule }>(`${path(id)}/run`, { method: "POST" });
export const previewSchedule = (cron: string, timezone: string) =>
  request<{ runs: string[] }>("/api/schedules/preview", send("POST", { cron, timezone }));

export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** IANA time zones for the picker: UTC first, and the current value kept even if this browser does not list it. */
export function timeZones(current: string): string[] {
  let zones: string[] = [];
  try {
    zones = Intl.supportedValuesOf("timeZone");
  } catch {
    zones = [];
  }
  return [...new Set(["UTC", current, ...zones])];
}
