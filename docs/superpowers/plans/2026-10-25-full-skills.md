# Full Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Skills are folders in the Agent Skills format (`SKILL.md` + files) stored on disk with metadata in the
database; they are imported from a zip or git, edited in the app, read with `load_skill` / `read_skill_file`, and their
scripts run in the sandboxed shell at `/skills/<name>/`.

**Architecture:** A `skills/` package holds the pure rules (frontmatter, paths, limits), the folder store (write a new
folder, switch, keep the last 3, clean up unused) and the import (zip, git). The run pins each skill's folder; the
capabilities read from it and the shell worker mounts it read-only. The UI gets a Skills page with import and a
file-tree editor.

**Tech Stack:** PostgreSQL 17; Python 3.14 / FastAPI / psycopg 3 / pytest; git (CLI in the admin-api image — check it is
installed; code projects already clone); the shell worker (bubblewrap); React Router 7 / TypeScript / shadcn/ui /
vitest.

**Spec:** `docs/superpowers/specs/2026-10-25-full-skills-design.md`

## Global Constraints

- **Simple code;** rules (frontmatter parsing, path checks, limits, which folders to delete) are pure functions.
- Migration `0139_skill_folders` deletes existing skills and their links (test data), drops `instructions`, adds
  `source jsonb NOT NULL` and `folder text NOT NULL`.
- Disk layout `<DATA_ROOT>/skills/<skill_id>/<version>/` (`<version>`: short commit or UTC timestamp
  `YYYYMMDDTHHMMSSZ`); write the whole new folder, then switch `folder` in one update; keep the last 3; delete older ones
  only when no running run pinned them.
- Limits: 10 MB per folder, 500 files, 5 MB per file; relative paths only, no `..`, no symlinks.
- Exact texts: "A skill named <name> exists; update it instead."; the `load_skill` note "Read a file with
  read_skill_file. Scripts are under /skills/<name>/ in shell_run."
- Scripts run only through `shell_run` in the existing sandbox (no network, read-only skill mounts, scratch writable).
- Work only in `.claude/worktrees/full-skills` (branch `feature/full-skills`). Never `make`, never touch `.env`/`.local`,
  never `git stash` in any form, never touch the running Docker stack, never kill processes you did not start, never
  spawn subagents. UI: `node_modules` is a symlink (never commit it, never `pnpm install`).
- Format: `uv run --frozen ruff format <files>`, `pnpm format`. Commits end with `Co-Authored-By: Claude Opus 5.5
  <noreply@anthropic.com>`; never `git add` `.superpowers` or `node_modules`.
- Full suites: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (known failures: code-graph tests in
  worktrees incl. test_code_processing and test_code_graph_sdk/config, test_deploy docs command, test_environments
  rollback); `cd services/admin-ui && pnpm exec vitest run && pnpm exec tsc --noEmit && pnpm format:check` (documentSaves
  and skills are slow: rerun alone).

## Review Focus

1. A skill updated while a run is using it → the run keeps reading and running its pinned folder (Task 2).
2. A zip with `../evil` or a symlink → refused, nothing written (Task 1).
3. Update from source on a git skill → new folder with the new commit, changed files listed, previous folder kept for
   Restore (Task 1).
4. A skill script runs in `shell_run` at `/skills/<name>/scripts/...` and cannot write into the skill folder (Task 2).
5. Cleanup never deletes a folder a running run pinned (Task 1).

---

### Task 1: Storage, rules and import (backend)

**Files:** `db/migrations/0139_skill_folders.{up,down}.sql` + migration test; a `skills/` package: `rules.py`
(`parse_skill_md(text) -> {name, description, body}`, `check_paths(entries)`, `check_limits(...)`,
`folders_to_delete(kept, pinned)`), `store.py` (write folder, switch, list kept folders, restore, delete, cleanup),
`imports.py` (zip; git shallow clone of URL + ref + path into a temp folder, recording the commit; Update from source
with a changed-files list), `api.py` (list, get with file tree, create from template, import zip / git, update from
source/upload, restore, delete, file read/write for the editor: save = new folder from the current one plus changes),
startup cleanup in `app.py`; the existing skills API/modules replaced (find them: `agent_skills`, `SkillForm`'s API);
tests (`test_skill_rules.py`, `test_skill_store.py`, `test_skill_import.py` with a local git repo fixture,
`test_skills_api.py`); `docs/agents.md` skills section.

- [ ] Failing tests (Review Focus 2, 3, 5; limits; name clash; several skills in one zip) → implement → full backend
  suite → commit `feat(skills): skills are folders, imported from zip or git`.

### Task 2: Runtime — load_skill, read_skill_file, scripts

**Files:** `capabilities/skills.py` (`load_skill` returns the `SKILL.md` body + file list + the note; new
`read_skill_file(name, path)` with 64 KB truncation and binary handling; checks via access), run start / access
building (skill entries carry `folder`, pinned), `capabilities/shell/client.py` and the shell worker (mount each run
skill's folder read-only at `/skills/<name>/`; find how code project paths are mounted and passed), `tools/tools.json`
(+ golden, matrix rows), `sessions/debug.py` (skills with folder), tests (`test_skill_runtime.py`: Review Focus 1, 4;
the shell mount through the existing shell-worker test path).

- [ ] Failing tests → implement → full backend suite → commit `feat(skills): load_skill lists files, read_skill_file, scripts run at /skills`.

### Task 3: UI — Skills page, import, editor

**Files:** `features/agents/SkillsView.tsx` and `SkillForm.tsx` replaced by a Skills page (list with source and
updated, New skill, Import: zip upload / git URL + ref + path, Update from source), a skill editor page (file tree: add
file/folder, upload, rename, delete with AlertDialog; text editor with `SKILL.md` frontmatter check; binary preview;
Save; **Previous versions** right-side Sheet with Restore); routes; the agent skill picker unchanged apart from types;
tests; `docs/user-guide.md`.

- [ ] Failing tests → implement → UI suite → commit `feat(admin-ui): skills as folders — import and a file-tree editor`.
