# Document Tools Without Proposals — Design

Approved direction: 2026-10-23 (conversation). Follows the inventory drafts (`2026-10-23-inventory-drafts-design.md`,
merged 7ff2abc): agents write drafts into the real data, people publish.

## Problem

- An agent writes documents two ways. `save_document` / `edit_document` save the run's draft of a document; a person
  publishes it through the existing draft flow. But `propose_document` writes a separate proposal record, reviewed on
  the Proposals pages, that becomes a document only on approval.
- The draft tools work only on documents the run was given as *editable*, and only with the **Content editor** role. So
  a resource chat can neither create a document for its server, service or environment nor update one, and the
  proposal path needs the same role.
- The Proposals pages, the proposals table and the Propose wizard's proposal steps exist only for this.

## Decisions (from the conversation)

1. **Every agent write to a document is a draft** in the real documents: a new document is a new draft, a change is the
   run's draft of the document. A person publishes it through the existing draft flow. Nothing is proposed separately.
2. **The Content editor role goes.** Every run may write drafts within its access.
3. **A run may draft any document it may read,** and new documents for any server, service or environment in its
   access. The per-document *editable* pick goes.
4. **Two write paths for an existing document stay:** the whole content, or one exact passage.
5. **Proposals go entirely:** `propose_document`, `ka.discovery_proposals`, the Proposals pages and links, and the
   proposal parts of the Propose wizard.

## 1. The document tools

| Tool | What it does |
|---|---|
| `search_documents(query)` | Unchanged. |
| `list_documents(resource)` | New. The documents linked to a server, service or environment (`resource`: `{type, id}` or `{type, name}`): id, title, short description, tags, its inventory document first, and whether this run already has a draft of it. |
| `get_document(document_id)` | Unchanged: this run's draft of the document if it has one, else the live version. Replaces `read_document` where they overlap (keep one reader; see §4). |
| `new_document(resource, title, content, description?, tags?)` | New. Creates a new document as a draft, linked to the resource it is for (`built_for`, the existing rule). Folder and slug come from the resource, as discovery's document approval does today. A title already used by a document linked to the same resource is refused with that document's id and "update it with update_document instead". Returns the draft's document id. |
| `update_document(document_id, content, title?, description?, resource?)` | Renamed `save_document`: saves this run's draft of the document with the complete content. `resource` adds a link from the document to that resource; the link is stored with the draft and added to the document when the draft is published. |
| `edit_document(document_id, old_text, new_text)` | Unchanged: replaces one exact passage (it must occur exactly once) in this run's draft (or the live text when there is no draft yet) and saves the draft. An empty `new_text` removes the passage. |

- A run has one draft per document; every `update_document` / `edit_document` updates it. `new_document` returns the id
  that the later updates use.
- Every reply that writes says the document is a draft waiting for a person to publish it, and where (the document's
  page).
- Tool descriptions state the purpose, the drafts rule, every field with its format (Markdown content up to 50,000
  characters; title up to 240; resource as `{type: "server"|"service"|"environment", id}` or with `name` instead of `id`),
  and a short example, as the inventory tools do.

## 2. Access

- Reading and drafting: a document is usable when it is in the run's access or linked to a resource in it (today's
  read rule). `update_document` and `edit_document` need exactly that; `new_document` needs the resource in access.
- Removed: `access.is_content_editor`, `roles.content_editor` (run start, narrowing, experts, subagents, the profiles API,
  the agent wizard, the Content editor switch, Debug), the `editable` flag on document entries (access objects, the
  agent settings sheet, Debug) and the refusal texts that mention them. Stored access objects that still carry
  `content_editor` or `editable` load without error (unknown keys ignored).
- Subagents and experts keep the read tools only (`search_documents`, `list_documents`, `get_document`).
- Runs started for a document (the document page's Ask agent, a new document) keep working: their document is in
  their access as today, without the editable flag.

## 3. Proposals go

- Migration `0130`: drop `ka.discovery_proposals` (and anything that only served it). Down: recreate the table empty in
  its 0129 shape.
- Remove `propose_document`, the proposal API (`servers/discovery.py` review / approve / reject), the run tag
  `proposals` handling, the Proposals pages and routes (`/servers/:id/proposals`, `/services/:id/proposals`,
  `/environments/:id/proposals`), their links in page headers, `ProposalsPage.tsx`, `ProposalSheet.tsx`, the proposal
  types.
- The Propose wizard becomes **Discover…**: it starts the run from the chosen prompt; its steps that only shaped
  proposals go. The seeded "Server discovery" prompt gets a new version (only where it still equals the seeded text, as
  0129 did) that tells the agent to draft inventory with `save_inventory_draft` and documents with `new_document` /
  `update_document`.

## 4. Where people see document drafts

- Unchanged and enough: the Documents page and each resource's Documents tab already show drafts marked "Draft — not
  published", and the document page publishes them. A resource's Documents tab lists its draft new documents too.
- The environment page header's "N drafts to review" counts document drafts linked to the environment and its
  servers and services as well, linking to the Documents tab when only document drafts are waiting.
- `read_document` vs `get_document`: keep `get_document` (draft-aware) and remove `read_document` if it is only a
  subset; otherwise keep both and say the difference in their descriptions (decided in the plan from the code).

## Not in scope

- Agents publishing documents.
- Who may publish: unchanged.
- Changing the draft or publish flow itself.

## Testing

- Rules: the access checks for each tool (in access, linked to a resource in access, resource in access for new);
  no role needed; old access objects load.
- Tools: `new_document` creates a draft linked to the resource, refuses a duplicate title with the existing id;
  `update_document` and `edit_document` write one draft per run; `resource` adds a link on publish; `list_documents`
  per resource with draft flags.
- Removal: no `content_editor`, `editable`, `propose_document` or proposals routes left (grep); migration up/down.
- UI: the settings sheet without editable picks, no Proposals links, Discover… wizard, the header count with document
  drafts.
