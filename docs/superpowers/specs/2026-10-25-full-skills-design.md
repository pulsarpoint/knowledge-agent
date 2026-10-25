# Full Skills — Design

Approved direction: 2026-10-25 (conversation). Skills become folders in the Agent Skills format (a `SKILL.md` plus
references, scripts and assets), so skills from GitHub and skill marketplaces work as they are.

## Problem

- A skill today is one row in `ka.agent_skills` (name, description, one instructions text). The run's instructions list
  skills by name and description; `load_skill(name)` returns the text.
- Real skills are folders: `SKILL.md` (frontmatter `name`, `description`, then instructions that point to files),
  `references/` (more documents to read when needed), `scripts/` (helpers to run), `assets/` (templates, examples). A
  skill that relies on any of its files cannot be used today.

## Decisions (from the conversation)

1. **A skill is a folder** in the Agent Skills format, imported from a zip or from a git repository, or written in the
   app.
2. **Scripts may run**, in the existing sandboxed shell (shell worker: bubblewrap, no network, read-only mounts,
   writable scratch folder).
3. **Files live on disk, the database holds metadata** (as Claude Code keeps skills as folders, and its plugin cache keeps
   one folder per version): name, description, where the skill came from, its current folder, and when and by whom it
   was updated.
4. Existing skills are test data and are removed by the migration; nothing is converted.

## 1. Storage

- **Database** — migration `0139_skill_folders`: delete the existing skills (and their agent and access links: the
  `skill` entries in stored access objects and any join rows), drop `ka.agent_skills.instructions`, add `source jsonb NOT
  NULL` (`{kind: "upload", file}` / `{kind: "git", url, ref, path, commit}` / `{kind: "editor"}`) and `folder text NOT
  NULL` (the current files, relative to the data folder). `name`, `description`, `enabled`, `revision`, `updated_at`,
  `updated_by` stay. Down: drop the two columns and add `instructions` back (empty table; skills are test data).
- **Disk** — `<DATA_ROOT>/skills/<skill_id>/<version>/`, `<version>` being the git commit (short) or a UTC timestamp
  for uploads and editor saves. Executable bits are kept.
- **An update writes a new folder, then switches**: an import, an Update from source or an editor save writes the
  complete new folder, then sets `folder` in one database update. A run that already started keeps the folder it began
  with, so a script never sees half-written files.
- **The last 3 folders are kept** (also for **Restore previous**, which switches `folder` back); older ones are deleted
  when no running run uses them (a cleanup on update and at admin-api startup). Git skills' real history is their
  repository; the recorded commit says what is in use.
- `name` and `description` are read from `SKILL.md`'s frontmatter at every import or save and stored (validated:
  `name` matches the existing name rule; `description` 1–1024 characters).
- Limits: a skill folder up to 10 MB in total, 500 files, a file up to 5 MB; paths relative, no `..`, no absolute paths,
  no symlinks (refused on import). Binary files allowed (assets).
- Deleting a skill removes its row and its folders (no running run using them; otherwise the folders go at the next
  cleanup).

## 2. Import

- **Upload**: a `.zip` (or a folder in the browser, sent as a zip). The archive's root, or its only top-level folder,
  must contain `SKILL.md`. Several skills in one archive (a folder per skill, each with `SKILL.md`) import as several
  skills, shown for confirmation first.
- **Git**: a repository URL, a ref (default branch when empty) and an optional path (e.g. `skills/pdf-forms` in a
  marketplace repository). The admin-api clones shallowly into a temporary folder (no credentials for now: public
  repositories, or the code projects' existing git credential when the URL matches one), reads the folder, records the
  commit. **Update from source** re-imports from the same URL, ref and path as a new version, showing which files changed.
- Importing a skill whose name already exists: refused with "A skill named <name> exists; update it instead." unless
  importing into that skill (Update from upload / source).

## 3. At run time

- The run's instructions keep listing skills by name and description (unchanged).
- `load_skill(name)` returns the current version's `SKILL.md` body and the list of its other files (path, size), plus a
  short note: "Read a file with read_skill_file. Scripts are under /skills/<name>/ in shell_run."
- New tool `read_skill_file(name, path)`: returns a text file's content (up to 64 KB, the rest cut with a note); binary
  files answer with their size and type only. Refused for a skill not in the run's access, with the existing refusal.
- Scripts: the run's skills are mounted read-only at `/skills/<name>/` inside the shell sandbox — the shell worker
  mounts each skill's folder (from the data folder it already shares) like code projects today. Scripts run with no
  network, in the scratch working directory; their outputs stay in scratch.
- The run pins its skills' folders at start (the access entries of type skill carry `folder`), so an update during a
  run does not change what it reads or runs.
- Debug lists the run's skills with their folder (commit or timestamp).

## 4. UI

- **Skills** page: the list (name, description, version, source), **New skill** (opens the editor with a `SKILL.md`
  template), **Import** (upload a zip, or from git: URL, ref, path), and per skill **Update from source** when it came
  from git.
- The skill editor: a file tree (add file, add folder, upload files, rename, delete with the AlertDialog confirmation),
  a text editor for text files (frontmatter checked for `SKILL.md`), a preview of binary files' name and size, **Save**
  (writes a new folder and switches), and **Previous versions** in a right-side Sheet (the kept folders: when, source;
  **Restore**).
- Agents pick skills as today.

## Not in scope

- Skills that need network or packages not in the shell worker image (they run, and fail as they would without
  network); private git repositories beyond the code projects' credentials; a public skill marketplace browser.
- MCP work (test server, per-tool selection, stdio bridge) — the next spec.

## Testing

- Migration: existing skills and their links are removed; columns changed; down.
- Import: zip (root and single folder), several skills in one zip, limits, paths (`..`, absolute, symlinks), git (a
  local test repository with a ref and a path), Update from source with a changed file list, name clash.
- Folders: an update writes a new folder and switches; a run started before keeps its folder; the last 3 kept, older
  deleted only when unused; restore; delete.
- Runtime: `load_skill` with the file list, `read_skill_file` (text, truncation, binary, refusal), scripts runnable in
  `shell_run` at `/skills/<name>/` read-only, pinned folders.
- Editor API: save writes a new folder, frontmatter validation.
- UI: Skills page, import flows, editor tree, versions sheet.
