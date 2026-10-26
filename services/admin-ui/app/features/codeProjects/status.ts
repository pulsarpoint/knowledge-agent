import type { CodeProject } from "./api";

/** A Git sync or a code graph job is queued or running (the pages reload while it is). */
export const syncing = (project: CodeProject) =>
  ["queued", "running", "waiting_input"].includes(project.sync_status ?? "") ||
  ["queued", "running"].includes(project.processing?.status ?? "");
/** The Git sync's state in words: its last sync's status, not cloned yet, or local folders (no Git source). */
export const syncStatus = (project: CodeProject) =>
  project.remote_url ? (project.sync_status ?? "not cloned") : "Local folders";
/** The code graph's state in words: its running or failed job, else its index status, else not indexed. */
export const graphStatus = (project: CodeProject) =>
  project.processing && ["queued", "running", "failed"].includes(project.processing.status)
    ? project.processing.status
    : (project.code_index?.status ?? "not indexed");
