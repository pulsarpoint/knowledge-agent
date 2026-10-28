import { request } from "~/lib/api";

/** A JSON schema as the session explorer lists it (the strict schema the reviewer's model sees). */
export interface JsonSchema {
  type?: string;
  description?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  additionalProperties?: boolean;
  anyOf?: JsonSchema[];
  items?: JsonSchema;
  enum?: string[];
  minimum?: number;
  maximum?: number;
  [key: string]: unknown;
}
/** One explorer method: its API name, the tool name a model calls, the description and the argument schema. */
export interface ExploreMethod {
  method: string;
  tool: string;
  description: string;
  parameters: JsonSchema;
}
export interface ExploreResult {
  result: unknown;
  size_chars: number;
  approx_tokens: number;
}
/** The digest method's result: the session as text. */
export interface DigestResult {
  format: "text";
  text: string;
  size_chars: number;
  full_chars: number;
  focus_turn: number;
  shortened_turns: number[];
  /** Per turn kept in full, the steps shortened to one line. */
  shortened_steps: Record<string, number[]>;
}

const explore = (runKey: string) => `/api/agent-runs/${encodeURIComponent(runKey)}/explore`;

/** The methods, and the session the run belongs to with the run's turn (a run outside a session: turn 1, no id). */
export const getExploreMethods = (runKey: string, signal?: AbortSignal) =>
  request<{ methods: ExploreMethod[]; session_id: string | null; turn: number }>(explore(runKey), { signal });

export const runExplore = (runKey: string, method: string, args: Record<string, unknown>, signal?: AbortSignal) =>
  request<ExploreResult>(explore(runKey), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ method, args }),
    signal,
  });

/** A field of the args form: the schema's type without its null (strict schemas make every optional field nullable). */
export type FieldKind = "text" | "number" | "checkbox" | "choices";
export function fieldKind(schema: JsonSchema): { kind: FieldKind; choices: string[] } {
  const own = schema.anyOf?.find((part) => part.type !== "null") ?? schema;
  if (own.type === "integer" || own.type === "number") return { kind: "number", choices: [] };
  if (own.type === "boolean") return { kind: "checkbox", choices: [] };
  if (own.type === "array" && own.items?.enum) return { kind: "choices", choices: own.items.enum };
  return { kind: "text", choices: [] };
}

/** The form's values as the method's args: empty fields left out (null to the method), numbers as numbers. */
export function formArgs(schema: JsonSchema, values: Record<string, unknown>): Record<string, unknown> {
  const args: Record<string, unknown> = {};
  for (const [name, property] of Object.entries(schema.properties ?? {})) {
    const value = values[name];
    const { kind } = fieldKind(property);
    if (kind === "number" && value !== undefined && value !== "") args[name] = Number(value);
    else if (kind === "checkbox" && value === true) args[name] = true;
    else if (kind === "choices" && Array.isArray(value) && value.length) args[name] = value;
    else if (kind === "text" && typeof value === "string" && value !== "") args[name] = value;
  }
  return args;
}
