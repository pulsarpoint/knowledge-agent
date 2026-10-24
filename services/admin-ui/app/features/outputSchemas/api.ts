import { request } from "~/lib/api";

/** A schema of the library as the list shows it; used_by: the handlers that start runs with it. */
export interface OutputSchemaSummary {
  id: string;
  name: string;
  description: string;
  current_version: number;
  revision: number;
  created_at: string;
  updated_at: string;
  used_by: string[];
}
export interface OutputSchemaVersion {
  version: number;
  created_at: string;
  created_by_email: string | null;
}
/** One schema with its current JSON and its versions, newest first. */
export interface OutputSchema extends OutputSchemaSummary {
  schema: unknown;
  versions: OutputSchemaVersion[];
}
export interface OutputSchemaDraft {
  name: string;
  description: string;
  schema: unknown;
}
/** problems: why the schema is outside the accepted subset (each with its path); sample_errors: null when no sample was
 * sent (or the schema has problems), else why the sample does not match ([]: it matches). */
export interface SchemaCheck {
  problems: string[];
  sample_errors: string[] | null;
}
/** The schema a run is started with: version null follows the current version. name is for showing it. */
export interface OutputSchemaChoice {
  id: string;
  name: string;
  version: number | null;
}
/** GET /api/sessions/{id}/output: the JSON a run finished with, or the errors and raw answer of a failed one. */
export interface RunOutput {
  schema: { id: string; name: string | null; version: number };
  output: unknown;
  valid: boolean;
  errors?: string[];
  raw?: string;
}

const json = (method: string, value: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(value),
});
const one = (id: string) => `/api/output-schemas/${encodeURIComponent(id)}`;

export const getOutputSchemas = (signal?: AbortSignal) =>
  request<{ schemas: OutputSchemaSummary[] }>("/api/output-schemas", { signal });
export const getOutputSchema = (id: string, signal?: AbortSignal) =>
  request<{ schema: OutputSchema }>(one(id), { signal });
export const getOutputSchemaVersion = (id: string, version: number, signal?: AbortSignal) =>
  request<{ version: { version: number; schema: unknown; created_at: string } }>(`${one(id)}/versions/${version}`, {
    signal,
  });
export const createOutputSchema = (draft: OutputSchemaDraft) =>
  request<{ schema: OutputSchema }>("/api/output-schemas", json("POST", draft));
export const saveOutputSchema = (id: string, draft: OutputSchemaDraft, revision: number) =>
  request<{ schema: OutputSchema }>(one(id), json("PUT", { ...draft, revision }));
export const deleteOutputSchema = (id: string, revision: number) =>
  request<void>(`${one(id)}?revision=${revision}`, { method: "DELETE" });
/** Check a schema, and a sample against it when one is given. */
export const checkOutputSchema = (schema: unknown, sample?: { value: unknown }) =>
  request<SchemaCheck>(
    "/api/output-schemas/check",
    json("POST", sample ? { schema, sample: sample.value } : { schema }),
  );
export const getRunOutput = (sessionId: string, signal?: AbortSignal) =>
  request<RunOutput>(`/api/sessions/${encodeURIComponent(sessionId)}/output`, { signal });

/** The text parsed as JSON, or why it is not JSON. */
export function parseJson(text: string): { value: unknown } | { error: string } {
  try {
    return { value: JSON.parse(text) };
  } catch (e) {
    return { error: `Not valid JSON: ${e instanceof Error ? e.message : String(e)}` };
  }
}
/** "Verdict", or "Verdict v2" for a fixed version. */
export const choiceLabel = (choice: OutputSchemaChoice) =>
  choice.version === null ? choice.name : `${choice.name} v${choice.version}`;
