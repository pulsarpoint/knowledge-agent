export interface CodeCitation { file: string; lines: string; quote?: string }
export interface ChatSource { n: number; path: string; heading_path: string; anchor: string | null }
export interface AgentContext {
  folders: string[] | null;
  include_subfolders: boolean;
  repositories: string[] | null;
}
export interface ContextOptions {
  folders: { path: string; description: string; indexed_files: number }[];
  repositories: { id: string; name: string; synced: boolean }[];
  warnings: string[];
}
export interface Transcript {
  session: string; messages: { role: string; content: string; sources_used?: string[] }[];
  context?: AgentContext; sources?: { docs: boolean; codex: boolean };
}
export type ChatEvent =
  | { type: "todo"; text: string }
  | { type: "session"; key: string }
  | { type: "tool"; name: string; argument: string }
  | { type: "token"; text: string }
  | { type: "sources"; sources: ChatSource[] }
  | { type: "done"; text: string; not_covered: boolean; escalation_offered?: boolean; sources_used?: string[] }
  | { type: "error"; message: string };


export type EscalateEvent =
  | { type: "todo"; text: string }
  | { type: "tool"; name: string; detail: string }
  | { type: "token"; text: string }
  | { type: "done"; answer: string; citations: CodeCitation[]; not_found: boolean; citations_dropped: number }
  | { type: "error"; message: string };
