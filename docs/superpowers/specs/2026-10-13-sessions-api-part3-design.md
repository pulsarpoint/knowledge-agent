# One sessions API, part 3: writing a new document with an agent — design

Date: 2026-10-13
Status: approved in conversation, section by section. Builds on parts 1 and 2 (`2026-10-13-sessions-api-design.md`,
merged b1042cc; `2026-10-13-sessions-api-part2-design.md`, merged d8a560a).

## Why

Generate documents is the last ad-hoc start with its own API and page: a form (Docs → Generate documents → New) builds a
session through `POST /api/generations/sessions`, the code appends an instruction to the first message ("reply with the
complete document in Markdown as your final answer: it becomes the draft"), and the worker turns each finished turn's
final answer into a new-document draft. That is code-driven behaviour and a second way of producing drafts. Part 3
makes writing a new document an ordinary agent run: the new document is a resource the run may edit, the agent writes
it with `save_document`, and the person starts it from Documents → New in the same chat as Ask agent.

## Decisions

- The agent writes the new document through `save_document` / `edit_document`; its chat answers are just chat. The
  final-answer rule and its appended instruction go away; how to work is the prompt's text.
- A person starts it from Documents → New ("Write with an agent"); the separate Generate documents form, list and API go.

## Backend

**A new document as a resource.**
- `resources.documents` accepts `{new: {folder, name}, editable: true}` next to `{id, editable}`. At most one new
  document per start.
- It is pinned in `document_access` as `{id: "new:<random uuid>", title: <name>, folder: <normalised folder>, slug:
  <document_slug(name)>, editable: true, new: true}`.
- `save_document` / `edit_document` on that id: the first save creates the run's new-document draft (`create_draft` with
  `document_id=None`, the folder and slug, `origin='agent'`, `session_id`), later saves update it. A folder and slug that
  already name a document make it a draft of that document (today's `create_draft` rule), and later saves keep updating
  that draft. `edit_document` before the first save answers "Write the document with save_document first."
- `get_document` on that id reads the run's draft, or answers "Not written yet." until the first save.
- The same `allowed()` check: no other id can be created or written.

**Starting point and purpose.**
- `started_from` gains `{new_document: {folder, name}}` (exactly one of `document_id` / `new_document`). The run records
  purpose `documents` with `folder` and `document_name` (existing columns); `target_document_id` stays NULL.
- `GET /api/sessions/options?new_document=1`: models, prompts tagged `documentation` (default: the seeded "Write a
  document"), all resources (none linked), the tools, the experts. A request with both `document_id` and `new_document`
  is a 400.
- Validation: a name with letters or digits (`document_slug`) and a valid documentation folder (`documentation_folder`),
  with today's messages.

**Removed.**
- The code-appended `FINAL_ANSWER` instruction (`session_text.py`).
- The worker's documents branch: the final answer no longer becomes the session's `draft`, and `save_run_draft` /
  `save_run_draft_safely` go (the source refresh after each turn stays — it covers every open run draft).
- `POST /api/generations/sessions` and `POST /api/generations/context`; modules `generations_api.py` and
  `generate_documents.py` (what `discovery.py` or others still import from them moves to where it is used).

**Migration 0101.**
- Closes open Generate documents runs (purpose `documents`, not closed) with "Closed by an update; start a new chat."
  and discards their queued messages; their drafts stay.
- Seeds the prompt "Write a document" (tags `documentation`, `new-document`) in every workspace and new ones: write a
  complete, accurate document from the resources you were given; read them before writing; write it with
  save_document and change it with edit_document; never include secrets; ask in plain chat when the request is unclear;
  when done, say in a few sentences what you wrote. Down: refuses while it is customized, removes it.
- Other `documentation` prompts are unchanged; the docs say a custom prompt should tell the agent to write with
  `save_document`.

## UI

**Documents → New: "Write with an agent".**
- The New document page (Title and Folder fields) gets a header button **Write with an agent** that opens the same chat
  and settings sheet beside the empty editor, in the full-width 2:1 layout.
- The first message starts the run with the Title and Folder as the new document; both must be filled ("Give the
  document a title and folder first."). The pane shows "Writing: `folder/title`"; changing the fields afterwards does not
  change the open run.
- One conversation per page visit: no Earlier runs; New chat starts over.
- The settings sheet is Ask agent's: prompts tagged `documentation` (default "Write a document"), all resources, tools,
  experts; remembered in this browser under `agent-settings:new-document`.
- After the agent's first save the draft bar shows the draft: **Compare** opens the draft page, **Promote** creates the
  document and opens `/documents/:id`.

**The run page `/sessions/:id`:** a run with a document draft shows its draft link (Open draft; Open document once
promoted), whatever its purpose; old runs with a final-answer `draft` text still show it.

**Removed:** Docs → Generate documents in the sidebar; `/documentation/generations` and `/documentation/generations/new`;
`DocumentGenerationForm`, `GenerationsView`, `lib/generations.ts` (the form's generated Context text included — the
agent gets the chosen resources as facts). Earlier runs stay readable at `/sessions/:id` and in Agent runs.

## Edge cases

- The title and folder name an existing document: the agent's saves become a draft of that document (today's rule); the
  draft bar shows it.
- The draft is promoted, discarded or edited by a person during the run: the next save starts a new draft (today's
  draft rules, as for Ask agent).
- The page is left before the agent saves: nothing is created; the run stays in Agent runs.

## Tests

- Backend: a new-document start (purpose, folder, document_name, pinned `new:` entry); the first `save_document`
  creates the new-document draft and later saves update it; an existing document's folder and name make a draft of it;
  `get_document` before the first save; `edit_document` before the first save; the allowlist refuses other ids; options
  with `new_document`; 0101 up (prompt seeded, open documents runs closed, others untouched), down, up again; the removed
  routes answer 404; Ask agent and saved-agent starts unchanged.
- UI: Write with an agent opens the chat; missing title or folder blocks the first message; the request body
  (`started_from.new_document`, `resources.documents[{new}]`); the draft bar after a save; Promote opens the document;
  the sidebar has no Generate documents; `/sessions/:id` shows a run's draft link.

## Not in scope

Discovery (part 4); event handlers (part 5); several new documents in one run from the UI; rewriting other
`documentation` prompts.
