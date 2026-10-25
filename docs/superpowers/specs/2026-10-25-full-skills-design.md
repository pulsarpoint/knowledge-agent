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
3. Existing skills keep working: each becomes a folder with just its `SKILL.md`.

## 1. Storage

- Migration `0139_skill_files`:
  - `ka.skill_versions` (`skill_id`, `version` (1, 2, …), `created_by`, `created_at`, `source jsonb` — upload / git
    `{url, ref, path, commit}` / editor) and `ka.skill_files` (`skill_id`, `version`, `path`, `content bytea`,
    `size`, `sha256`, `executable boolean`), primary key (`skill_id`, `version`, `path`).
  - `ka.agent_skills` keeps `name`, `description`, `enabled`, `revision` and gains `current_version`; `instructions`
    moves into `SKILL.md` of version 1 (the migration writes each existing skill's `SKILL.md` with frontmatter from its
    name and description) and the column is dropped. Down: restore `instructions` from the current `SKILL.md` body, drop
    the new tables and column.
- `name` and `description` are always those of the current version's `SKILL.md` frontmatter (validated on save:
  `name` matches the existing name rule; `description` 1–1024 characters).
- Limits: a skill version up to 10 MB in total, 500 files, a file up to 5 MB; paths relative, no `..`, no absolute paths,
  no symlinks (refused on import). Binary files allowed (assets); text shown in the editor.
- Every change (an import, an editor save) adds a version; old versions are kept and can be restored (restore = a new
  version with that content).

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
- Scripts: the run's skills (the version pinned at run start) are materialised read-only at `/skills/<name>/` inside
  the shell sandbox. The admin-api writes each skill version's files once to `<DATA_ROOT>/skills/<skill_id>/<version>/`
  (content-addressed, idempotent; executable bits kept) and the shell worker mounts the run's ones read-only, like code
  projects today. Scripts run with no network, in the scratch working directory; their outputs stay in scratch.
- The run pins its skills' versions (`pinned config` access entries of type skill carry `version`), so an update during
  a run does not change it.
- Debug lists the run's skills with versions.

## 4. UI

- **Skills** page: the list (name, description, version, source), **New skill** (opens the editor with a `SKILL.md`
  template), **Import** (upload a zip, or from git: URL, ref, path), and per skill **Update from source** when it came
  from git.
- The skill editor: a file tree (add file, add folder, upload files, rename, delete with the AlertDialog confirmation),
  a text editor for text files (frontmatter checked for `SKILL.md`), a preview of binary files' name and size, **Save**
  (a new version), and **Versions** in a right-side Sheet (view, restore).
- Agents pick skills as today.

## Not in scope

- Skills that need network or packages not in the shell worker image (they run, and fail as they would without
  network); private git repositories beyond the code projects' credentials; a public skill marketplace browser.
- MCP work (test server, per-tool selection, stdio bridge) — the next spec.

## Testing

- Migration: existing skills become version 1 with a `SKILL.md`; down restores `instructions`.
- Import: zip (root and single folder), several skills in one zip, limits, paths (`..`, absolute, symlinks), git (a
  local test repository with a ref and a path), Update from source with a changed file list, name clash.
- Runtime: `load_skill` with the file list, `read_skill_file` (text, truncation, binary, refusal), scripts runnable in
  `shell_run` at `/skills/<name>/` read-only, pinned versions.
- Editor API: save adds a version, restore, frontmatter validation.
- UI: Skills page, import flows, editor tree, versions sheet.
