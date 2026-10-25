import { request } from "~/lib/api";

export type SkillSource =
  | { kind: "upload"; file: string }
  | { kind: "git"; url: string; ref: string; path: string; commit: string }
  | { kind: "editor" };

export interface SkillSummary {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  revision: number;
  source: SkillSource;
  /** The current folder, relative to the data folder; `version` is its last part (a commit or a UTC timestamp). */
  folder: string;
  version: string;
  created_at: string;
  updated_at: string;
}
export interface SkillFile {
  path: string;
  size: number;
  executable: boolean;
}
export interface SkillVersion {
  version: string;
  created_at: string | null;
  source: SkillSource | null;
  folder: string;
  current: boolean;
}
export interface Skill extends SkillSummary {
  files: SkillFile[];
  versions: SkillVersion[];
}
/** A skill found in an archive or a repository, before importing it. */
export interface FoundSkill {
  name: string;
  description: string;
  files: { path: string; size: number }[];
}
export interface FileChange {
  path: string;
  text?: string;
  content_base64?: string;
  delete?: true;
  rename_to?: string;
}
export interface Changed {
  path: string;
  change: "added" | "removed" | "modified";
}
export interface GitInput {
  url: string;
  ref: string;
  path: string;
}

const skillUrl = (id: string) => `/api/skills/${encodeURIComponent(id)}`;
const json = (method: string, value: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(value),
});
const zip = (file: File): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/zip" },
  body: file,
});

export const getSkills = (signal?: AbortSignal) => request<{ skills: SkillSummary[] }>("/api/skills", { signal });
export const getSkill = (id: string) => request<Skill>(skillUrl(id));
export const createSkill = (value: { name: string; description: string }) =>
  request<Skill>("/api/skills", json("POST", value));
export const previewZip = (file: File) =>
  request<{ skills: FoundSkill[] }>(
    `/api/skills/import/zip?file=${encodeURIComponent(file.name)}&preview=true`,
    zip(file),
  );
export const importZip = (file: File) =>
  request<{ skills: Skill[] }>(`/api/skills/import/zip?file=${encodeURIComponent(file.name)}`, zip(file));
export const previewGit = (value: GitInput) =>
  request<{ skills: FoundSkill[] }>("/api/skills/import/git", json("POST", { ...value, preview: true }));
export const importGit = (value: GitInput) =>
  request<{ skills: Skill[] }>("/api/skills/import/git", json("POST", value));
export const updateFromSource = (skill: SkillSummary) =>
  request<{ skill: Skill; changed: Changed[] }>(
    `${skillUrl(skill.id)}/update/source`,
    json("POST", { revision: skill.revision }),
  );
export const updateFromUpload = (skill: SkillSummary, file: File) =>
  request<{ skill: Skill; changed: Changed[] }>(
    `${skillUrl(skill.id)}/update/upload?file=${encodeURIComponent(file.name)}&revision=${skill.revision}`,
    zip(file),
  );
export const readSkillFile = (id: string, path: string) =>
  request<{ path: string; size: number; executable: boolean; text: string | null }>(
    `${skillUrl(id)}/files/${path.split("/").map(encodeURIComponent).join("/")}`,
  );
export const saveSkillFiles = (skill: SkillSummary, changes: FileChange[]) =>
  request<{ skill: Skill; changed: Changed[] }>(
    `${skillUrl(skill.id)}/files`,
    json("PUT", { revision: skill.revision, changes }),
  );
export const restoreSkill = (skill: SkillSummary, version: string) =>
  request<Skill>(`${skillUrl(skill.id)}/restore`, json("POST", { revision: skill.revision, version }));
export const deleteSkill = (id: string) => request<void>(skillUrl(id), { method: "DELETE" });
export const setSkillEnabled = (skill: SkillSummary, enabled: boolean) =>
  request<SkillSummary>(`${skillUrl(skill.id)}/status`, json("PATCH", { enabled, revision: skill.revision }));

/** A pasted `https://github.com/<owner>/<repo>/tree/<ref>/<path>` as URL, ref and path; null for any other text. */
export function githubTree(value: string): GitInput | null {
  const match = /^https:\/\/github\.com\/([^/\s]+)\/([^/\s]+)\/tree\/([^/\s]+)(?:\/(.*))?$/.exec(value.trim());
  if (!match) return null;
  return {
    url: `https://github.com/${match[1]}/${match[2]}`,
    ref: match[3],
    path: (match[4] ?? "").replace(/^\/+|\/+$/g, ""),
  };
}

export function sourceLabel(source: SkillSource | null): string {
  if (source?.kind === "git")
    return `Git: ${source.url}${source.ref || source.path ? ` @ ${[source.ref, source.path].filter(Boolean).join(", ")}` : ""}`;
  if (source?.kind === "upload") return `Uploaded ${source.file || "zip"}`;
  if (source?.kind === "editor") return "Edited in the app";
  return "Unknown";
}
export const changedLabel = (changed: Changed[]) =>
  changed.length
    ? `Changed: ${changed.map((item) => `${item.path} (${item.change})`).join(", ")}.`
    : "No files changed.";
export function sizeLabel(size: number): string {
  if (size < 1024) return `${size} bytes`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

const NAME = /^[a-z0-9]+(-[a-z0-9]+)*$/;
const NO_FRONTMATTER = "SKILL.md must start with a frontmatter block between --- lines.";
const NAME_RULE = "SKILL.md's name must be 1-64 lowercase letters, digits and single hyphens, such as pdf-forms.";
const DESCRIPTION_RULE = "SKILL.md's description must be text of 1-1024 characters.";

/** A top-level `key: value` of simple YAML: plain or quoted, or a block scalar (`>`, `|`) of indented lines. */
function frontmatterValue(lines: string[], key: string): string | undefined {
  const index = lines.findIndex((line) => line.startsWith(`${key}:`));
  if (index === -1) return undefined;
  const value = lines[index].slice(key.length + 1).trim();
  if (/^[>|][-+]?$/.test(value)) {
    const block: string[] = [];
    for (const line of lines.slice(index + 1)) {
      if (line.trim() && !/^\s/.test(line)) break;
      block.push(line.trim());
    }
    return block.join(value.startsWith(">") ? " " : "\n").trim();
  }
  const quoted = /^(["'])(.*)\1$/.exec(value);
  return quoted ? quoted[2] : value;
}

/** Why SKILL.md's frontmatter would be refused on save (the server's rules, for simple YAML), or null. */
export function checkSkillMd(text: string): string | null {
  text = text.replace(/\r\n/g, "\n").replace(/^﻿/, "");
  if (!text.startsWith("---\n")) return NO_FRONTMATTER;
  const end = text.indexOf("\n---", 3);
  if (end === -1 || !["", "\n"].includes(text.slice(end + 4, end + 5))) return NO_FRONTMATTER;
  const lines = text.slice(4, end).split("\n");
  const name = frontmatterValue(lines, "name");
  if (!name || name.length > 64 || !NAME.test(name)) return NAME_RULE;
  const description = frontmatterValue(lines, "description")?.trim();
  if (!description || description.length > 1024) return DESCRIPTION_RULE;
  return null;
}
export const skillMdName = (text: string) =>
  frontmatterValue(text.replace(/\r\n/g, "\n").split("\n---")[0].split("\n"), "name");

export interface TreeRow {
  path: string;
  name: string;
  depth: number;
  kind: "file" | "folder";
}
/** The tree's rows in display order: in each folder its folders first, then its files, by name. */
export function treeRows(files: string[], folders: string[]): TreeRow[] {
  const all = new Set<string>(folders);
  for (const file of files) {
    const parts = file.split("/");
    for (let i = 1; i < parts.length; i++) all.add(parts.slice(0, i).join("/"));
  }
  const rows: TreeRow[] = [
    ...[...all].map((path) => ({ path, kind: "folder" as const })),
    ...files.map((path) => ({ path, kind: "file" as const })),
  ].map((row) => ({ ...row, name: row.path.split("/").pop()!, depth: row.path.split("/").length - 1 }));
  // Each row sorts by its ancestors' names, then folder before file, then its own name.
  const key = (row: TreeRow) => [
    ...row.path
      .split("/")
      .slice(0, -1)
      .map((part) => `0${part}`),
    `${row.kind === "folder" ? 0 : 1}${row.name}`,
  ];
  return rows.sort((a, b) => {
    const [x, y] = [key(a), key(b)];
    for (let i = 0; i < Math.min(x.length, y.length); i++) if (x[i] !== y[i]) return x[i].localeCompare(y[i]);
    return x.length - y.length;
  });
}

/** A file of the editor's working copy: where it came from (null for a new file), and its content when known. */
export interface WorkingFile {
  origin: string | null;
  size: number;
  /** The current text, once loaded or typed; `saved` is the text as loaded. */
  text?: string;
  saved?: string;
  /** Uploaded content. */
  base64?: string;
  binary?: boolean;
}
/** The save's changes from the saved paths to the working copy (by path): deletes, renames (through temporary names,
 * so two files can swap names), then writes of new or changed content. */
export function fileChanges(saved: string[], working: Record<string, WorkingFile>): FileChange[] {
  const entries = Object.entries(working);
  const kept = new Set(entries.map(([, file]) => file.origin));
  const deletes: FileChange[] = saved.filter((path) => !kept.has(path)).map((path) => ({ path, delete: true }));
  const moved = entries.filter(([path, file]) => file.origin !== null && file.origin !== path);
  const renames: FileChange[] = [
    ...moved.map(([, file], index) => ({ path: file.origin!, rename_to: `.renaming-${index}` })),
    ...moved.map(([path], index) => ({ path: `.renaming-${index}`, rename_to: path })),
  ];
  const writes: FileChange[] = entries.flatMap(([path, file]): FileChange[] => {
    if (file.base64 !== undefined) return [{ path, content_base64: file.base64 }];
    if (file.text !== undefined && (file.origin === null || file.text !== file.saved))
      return [{ path, text: file.text }];
    return [];
  });
  return [...deletes, ...renames, ...writes];
}

/** Why a path typed in the editor cannot be used, or null. */
export function pathProblem(path: string, taken: (path: string) => boolean): string | null {
  if (
    !path ||
    path.startsWith("/") ||
    path.includes("\\") ||
    path.split("/").some((part) => ["", ".", ".."].includes(part))
  )
    return "Use a relative path such as references/guide.md, without .. or empty parts.";
  if (taken(path)) return `${path} already exists.`;
  return null;
}
