export interface ChatSource { n: number; path: string; heading_path: string; anchor: string | null }
export interface AgentContext {
  folders: string[] | null;
  include_subfolders: boolean;
  repositories: string[] | null;
}
export interface ContextOptions {
  folders: { path: string; description: string; indexed_files: number }[];
  warnings: string[];
}
export interface Transcript {
  session: string; messages: { role: string; content: string; sources_used?: string[]; sources?: ChatSource[]; not_covered?: boolean }[];
  context?: AgentContext; sources?: { docs: boolean };
}
export type ChatEvent =
  | { type: "todo"; text: string }
  | { type: "session"; key: string }
  | { type: "tool"; name: string; argument: string }
  | { type: "token"; text: string }
  | { type: "sources"; sources: ChatSource[] }
  | { type: "done"; text: string; not_covered: boolean; sources_used?: string[] }
  | { type: "error"; message: string };
