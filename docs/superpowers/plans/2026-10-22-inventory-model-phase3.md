# Inventory Model Phase 3 (The Map Draws From the Model) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The system map is drawn from the environment model (`inventory/model.py` `build_model`), so the map, the
agents (`get_environment_model`) and the API read one source; connection edges show their protocol and port.

**Architecture:** `servers/inventory_map.py` stops loading rows itself. A small store read builds the models of all the
project's environments (one query set, then `build_model` per environment) plus what no environment holds (servers and
services without deployments). `placements` / `view_edges` / `build_view` take those models. The JSON / mermaid / d2 /
svg outputs keep their shape; connection edges gain a label (`postgres:5432`).

**Tech Stack:** Python 3.14 / FastAPI / psycopg 3 / networkx / pytest; d2 and mermaid renderers
(`servers/system_graph_render.py`); React Router 7 / TypeScript / vitest.

**Spec:** `docs/superpowers/specs/2026-10-22-inventory-model-design.md` ("System map"; Phase 3). Phases 1–2 merged
(01a70e7, 2a015f4).

## Global Constraints

- **Simple code, no unnecessary abstractions;** the map's placement and edge logic stay pure functions of the models.
- **One source:** after this phase `inventory_map.py` reads nothing but the models (no SQL of its own); grep it.
- Work only in `.claude/worktrees/inventory-map` (branch `feature/inventory-map`). Never `make`, never touch
  `.env`/`.local`, never `git stash` (WIP commit instead), never touch the running Docker stack, never kill processes you
  did not start. UI checks: `ln -s ../../../../../services/admin-ui/node_modules services/admin-ui/node_modules` once
  (never commit it, never `pnpm install`).
- Format: `uv run --frozen ruff format <files>`, `pnpm format`. Commits end with `Co-Authored-By: Claude Opus 5.5
  <noreply@anthropic.com>`; never `git add` `.superpowers` or `node_modules`.
- No migration.
- Behaviour unchanged except the edge labels: the same nodes, edges, depths, roots, boundaries and documents as before
  for the same data (pin it with a before/after comparison test on a rich fixture).
- Full suites: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (known failures: code-graph tests in
  worktrees, test_deploy docs command, test_environments rollback); `cd services/admin-ui && pnpm exec vitest run &&
  pnpm exec tsc --noEmit && pnpm format:check` (rerun slow tests alone).

## Review Focus

1. The map from the models equals the map from today's loader on the same data (nodes, edges, counts, boundaries,
   documents), apart from the new labels.
2. Servers and services with no deployment still appear (unplaced) as before.
3. Cross-environment connections and external hosts are drawn as before.
4. Edge labels are correct for single and rolled-up edges (a rolled-up edge lists distinct labels, capped).
5. No SQL remains in `inventory_map.py`.

---

### Task 1: The map reads the models

**Files:** `inventory/store.py` (`load_project_models(conn, project_id) -> {models: [...], unplaced: {servers, services},
code_projects, documents}` — one query set for the whole project, then `build_model` per environment; documents attached
to entries as today), `servers/inventory_map.py` (`load` replaced by the store read; `placements`, `view_edges`,
`build_view` take the models; delete the old loader SQL), `servers/map_api.py` (wiring), tests
`tests/test_inventory_map_models.py` (a rich fixture: two environments, a cross-environment connection, an external
host, unplaced server and service, relations of each kind, documents; the view JSON before (captured from the current
code at the start of the task and stored as the expected value) equals after, for both views and depths 1–3).

- [ ] Step 1: write the before/after test (capture today's output first). Step 2: switch the loader. Step 3: the test
  passes; existing map tests pass. Step 4: full backend suite. Step 5: commit `refactor(map): the system map draws from
  the environment models`.

### Task 2: Connection labels; docs

**Files:** `servers/inventory_map.py` (connection edges carry `label` = `<protocol>:<port>`; a rolled-up edge carries the
distinct labels, at most 3 then `+N`), `servers/system_graph_render.py` (d2 and mermaid draw the label),
`services/admin-ui/app/features/servers/SystemMapView.tsx` / `inventoryMap.ts` types (show the label in the edge list or
tooltip if the map has one), tests; docs (`docs/user-guide.md` System map: one model, connection labels;
`docs/architecture.md` if it describes the map's sources).

- [ ] Step 1: failing tests (labels single and rolled up, both renderers). Step 2: fail. Step 3: implement. Step 4:
  backend + UI suites. Step 5: commit `feat(map): connection edges show protocol and port`.
