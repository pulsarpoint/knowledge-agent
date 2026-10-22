# Inventory Drafts — Design

Approved direction: 2026-10-23 (conversation). Builds on the inventory model (`2026-10-22-inventory-model-design.md`)
and the environment workspace (`2026-10-22-environment-workspace-design.md`, merged 899cd9c / da00bcc).

## Problem

- A chat started from an environment, server or service page is an ad-hoc run. It is never an inventory editor, so
  `save_connection` refuses; its fallback `propose_service` refuses too (content editor off; server runs only).
- Two ways for an agent to suggest inventory (the Inventory editor role writing directly, and discovery's service
  proposals reviewed on a Proposals page) are more machinery than needed, and a direct write reaches the real inventory
  without a person seeing it.

## Decisions (from the conversation)

1. **Every agent write is a draft.** Agents add and update draft entries in the inventory itself; only a person turns
   a draft into a real entry (**Confirm**) or drops it (**Discard**).
2. **Agents never change confirmed entries.** They may add new drafts and update or remove drafts; confirmed entries
   are changed only by people.
3. **The Inventory editor role goes.** Every agent run may write drafts within its access.
4. **Service proposals go.** `propose_service`, the service part of the review sheet and service approval are removed;
   discovery writes drafts with the `save_*` tools. Document proposals (`propose_document`) stay as they are.

## 1. Draft status

- Migration `0128`: `status text NOT NULL DEFAULT 'confirmed' CHECK (status IN ('draft', 'confirmed'))` on
  `ka.servers`, `ka.services`, `ka.service_deployments`, `ka.connections`, `ka.service_relations` and
  `ka.environment_servers`. Existing rows are confirmed. The down migration deletes drafts, then drops the columns.
- What people add or change in the UI and REST API is confirmed, except that editing a draft keeps it a draft.
- Uniqueness is unchanged: a draft that duplicates an entry (draft or confirmed) is refused with the existing message.

## 2. What agents may do

- `save_server`, `save_service`, `save_deployment`, `save_connection`, `save_relation`:
  - **new** entry → created as a draft (author: the session's person, agent and session, as today);
  - **existing draft** → updated (revision as today);
  - **existing confirmed entry** → refused: "This <type> is confirmed; only a person can change it. Add a new draft
    instead, or describe the change in your answer.";
  - `save_server(environment_id)` adds a draft membership; a draft deployment adds a draft membership when the server
    is not a member yet.
- `remove_inventory_item` removes drafts only (same refusal for a confirmed one).
- Access: unchanged — the run's access (and entries created by this session) decides what an agent may name. The
  role check is gone; subagents and experts still get only the read tools.
- Read tools (`get_environment_model`, `list_*`, `get_resource`) return every entry with its `status`, so an agent sees
  drafts (its own and others') and does not repeat them.
- Removed: `access.is_inventory_editor`, `roles.inventory_editor` (run start, narrowing, experts, profiles API, the
  agent wizard, the Inventory editor switch, Debug), the refusal texts that mention it. Stored access objects that
  still carry the key are read as before (a missing or unknown role is ignored).

## 3. Confirm and Discard (people only)

- `POST /inventory/{kind}/{id}/confirm` and `POST /inventory/{kind}/{id}/discard`, `kind` in server, service,
  deployment, connection, relation, membership (`membership` id = `<environment_id>:<server_id>`), with the revision
  read (409 when stale).
- **Confirm** confirms the entry and every draft it needs: a deployment → its server, service and membership; a
  connection → its deployments (and what they need) and its external service; a relation → both services; a membership
  → its server. The response lists what was confirmed. Audit: `confirmed` per entry, by the person.
- **Discard** deletes the draft and the drafts that depend on it (a draft deployment → its draft connections; a draft
  service → its draft deployments, connections and relations; a draft server → its draft memberships and
  deployments). It never deletes a confirmed entry (a confirmed entry cannot depend on a draft: confirm cascades).
  Audit: `discarded`.
- Store: `confirm(conn, project_id, kind, id, revision, author) -> list[dict]` and
  `discard(conn, project_id, kind, id, revision, author) -> list[dict]`; pure rules list the dependants.

## 4. Where people see drafts

- **Workspace:** drafts are listed with a **Draft** badge and **Confirm** / **Discard** buttons (servers, services,
  deployments, connections, relations); Confirm shows what else it will confirm first. A filter above the columns:
  **All** / **Confirmed** / **Drafts**.
- **Environment page header:** "N drafts to review" when the environment has drafts, linking to the workspace with the
  Drafts filter.
- **Map:** draft nodes and edges are dashed; a **Show drafts** switch (on by default).
- **Server and service pages:** the same badges and buttons in the service panel and services table.
- The Proposals pages keep document proposals only.

## 5. Discovery

- The discovery prompts and `propose_document` stay; the tool descriptions say that inventory findings are saved as
  drafts with the `save_*` tools and documents are proposed with `propose_document`.
- Existing pending service proposals are test data: the migration deletes service proposals (`kind = 'service'`);
  the `kind` check becomes document only.

## Not in scope

- Agents proposing changes to confirmed entries (pending edits with before/after).
- Who may confirm: every person who may edit the inventory today may confirm and discard.
- `propose_document` still needs the content editor role.

## Testing

- Rules: dependants for confirm and discard (each kind), the refusal on confirmed entries.
- Store: create/update draft, confirm cascade, discard cascade, stale revision, audit rows, migration up/down.
- Tools: each write tool creates/updates drafts, refuses confirmed, no role needed; reads carry status; matrix rows.
- Removal: no `inventory_editor` left (grep), no `propose_service` left; old access objects still load.
- UI: badges, Confirm (with the list) and Discard, the filter, the header count, dashed map entries.
