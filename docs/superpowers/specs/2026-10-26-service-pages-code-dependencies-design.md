# Service Pages and Code Project Dependencies — Design

Approved direction: 2026-10-26 (conversation).

## Problem

- A service is edited in one large side-sheet form (`ServiceForm.tsx`) that stacks its details, code projects,
  relationships (checkbox lists) and deployments; the service page shows Overview / Documents. The UX is poor and
  relations are hard to manage.
- Libraries: a library is its own code project, used by the code of many services, with no service of its own. Nothing
  records that a code project uses a library.

## Decisions (from the conversation)

1. **The service page becomes routed sub-pages** under one header, like the agent and code project pages:
   View, Edit, Code, Depends on, Used by, Part of, Parts, Connections, Documents.
2. **Library usage is a code-level fact:** a code project depends on another code project. Services stay related to
   code only through **built from**; which libraries a service uses, and which services a library affects, are derived.
3. Service relations are unchanged (`depends_on`, `part_of` between services; `built_from` to a code project).

## 1. Code project dependencies

- Migration `0143_code_project_dependencies`: `ka.code_project_dependencies` (`id`, `project_id`,
  `from_code_project_id` → repositories, `to_code_project_id` → repositories, `description` (≤ 4000), `status`
  draft/confirmed like the other inventory tables, `revision`, `created_by`, `created_by_agent`, `session_id`,
  timestamps; unique (from, to); no self-dependency; both FKs `ON DELETE CASCADE`). Down: drop the table.
- Cycles are allowed in the data (they happen in real code) but shown; no transitive closure is stored.
- Store (`inventory/store.py`): create / update (description) / delete with revisions and audit, drafts like the other
  kinds (confirm and discard reach rules: a draft dependency needs nothing else confirmed; discarding a code project is
  out of scope — code projects are not inventory drafts).
- REST: `GET /api/code-projects/{id}/dependencies` → `{depends_on: [...], used_by: {code_projects: [...], services:
  [...]}}` where `used_by.services` are the services built from the dependent code projects (one level: direct
  dependents; the service list is derived), plus create / delete endpoints.
- Agent tools: `save_relation` (or a new kind on it) accepts `kind: "code_depends_on"` from a code project to a code
  project, as a draft; `remove_inventory_item` removes drafts of it; `get_resource` for a code project lists its
  dependencies and dependents. Tool descriptions say how ("a repository that imports a library repository: record it
  when you read package.json, go.mod, pyproject …").
- Map: the services view shows code projects with `depends on` edges between them (built-from edges exist already).

## 2. The service page as sub-pages

- Parent `/services/:serviceId`: header ("All services", name, type and role badges, external address when set; Chat —
  the resource chat side panel with its Conversations sheet; Discover…), tab-styled links with counts.
- Children:
  - `view` (index): read-only summary — description, owner, address, tags, where it runs (environments, servers,
    deployments with Edit / Remove as the workspace's service column), and counts linking to the other tabs.
  - `edit`: the service's own fields (name, type, role, owner, description, address for external, tags) edited in
    place; Save / Discard; the shared `LeaveGuard` for unsaved changes.
  - `code`: the code projects it is built from (Add — a right-side Sheet listing code projects with search — Remove
    with confirmation), and under each, that project's libraries (read-only, linked).
  - `depends-on`, `used-by`, `part-of`, `parts`: one list each of services with optional descriptions; Add (Sheet
    with search over services), Remove (confirm). `used-by` adds create `depends_on` from the other service to this one;
    `parts` adds create `part_of` from the other service to this one.
  - `connections`: network connections in and out (the workspace service column's connections section).
  - `documents`: as today.
- `/services/new`: the Edit page empty; Save creates the service and opens its View.
- The Services list: a row opens `view`; its Edit action opens `edit`; **New service** opens `/services/new`. The
  side-sheet `ServiceForm` goes, with whatever only it used.
- Old links (`/services/:id`, `?view=…`) redirect to `view` (or the matching sub-page); in-app links updated.
- Drafts: entries that are drafts show the Draft badge with Confirm / Discard, as elsewhere.

## 3. Code project page

- A new sub-page `dependencies` (`/code-projects/:id/dependencies`): **Depends on** (code projects, Add via a Sheet
  with search, Remove with confirmation, optional description) and **Used by** (code projects that depend on it, and the
  services built from them, linked).

## Not in scope

- Transitive dependency graphs; versions of dependencies; detecting dependencies automatically from files (agents may
  draft them).

## Testing

- Migration up/down; store and API (create, duplicate refused, self refused, delete with revision, drafts); derived
  used-by services; agent tool kind as a draft; get_resource; map edges.
- UI: service sub-pages render with the header; view summary; edit save/discard and leave guard; each relation tab
  add/remove (used-by and parts create the reverse relation correctly); code tab with libraries; new service; list
  links; redirects; code project Dependencies page.
