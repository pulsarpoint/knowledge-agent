# Ask agent on a document — design

Date: 2026-10-12
Status: approved in conversation. Second of two specs; builds on document drafts
(`2026-10-12-document-drafts-design.md`, migration 0098).

## Why

On a document's page a person asks an agent to change the document ("add a section on log rotation", "remove the old
backup steps"), choosing the model, the servers and other resources, the tools, and optionally experts and subagents.
Whatever the agent writes is a draft; a person promotes it. A conversation can go back and forth (questions, answers,
more changes) without the whole document being pushed through every turn.

## Decisions

- A run is a new kind of agent session, `revision`, reusing the session machinery (worker, chat page, Stop, follow-up
  messages, run events, tokens).
- The agent changes the document only through three tools that exist only in revision runs: `get_doc`, `save_doc`,
  `edit_doc`. Its answers are plain chat; a final answer never becomes the draft.
- Runs start through one universal endpoint, `/agent-sessions`, with a `kind` and a per-kind `target`. `revision` is its
  first kind; Generate documents and server discovery move onto it later, one at a time (not in this spec).
- Preview returns exactly what Start would save (the same function builds both).
- Experts and subagents are off unless chosen. The agent never promotes.

## Data — migration 0099

`ka.agent_sessions`:

- `purpose` also allows `revision`; the ad-hoc check (no `agent_id`) allows it.
- New column `target_document_id uuid`, `FOREIGN KEY (target_document_id, project_id) REFERENCES documents(id,
  project_id) ON DELETE CASCADE`; check `(purpose = 'revision') = (target_document_id IS NOT NULL)`; index on
  `(target_document_id)`. (`document_id` keeps its Generate documents meaning, `ON DELETE SET NULL`.) Deleting a
  document deletes its revision runs; the plan verifies that every table referencing `agent_sessions` lets that
  cascade (or the delete path removes them first).
- The run's choices are kept in its pinned configuration (`agent_config`): model, system prompt, resources, tools on and
  off, experts, subagents, plus `revision: {document_id}` (the capability switch, as `discovery` is for discovery runs).

Down refuses while revision sessions exist.

No new tables. Drafts the run writes are `ka.document_drafts` rows with `origin='agent'` and `session_id` = the run.

## Tools (revision runs only)

A new capability module (`capabilities/revision.py`), catalog entries with `needs='revision'` (offered only when the
pinned configuration has `revision_run` — `{session_id, document_id}`, not the agent version `revision`; never offered to subagents; not listed among the choosable tools).

The run's **working copy**: its latest draft with `status='open'` and `edited=false`; else the document's live version.

| Tool | Arguments | Result |
|---|---|---|
| `get_doc` | none, or `section` (heading text), or `start_line`/`end_line` | No arguments: title, description, length (characters, lines), the outline (headings with line numbers) and whether it reads a draft or the live version. `section`: that heading's section (to the next heading of the same or higher level), with line numbers. Lines: that range with line numbers. At most 50,000 characters per call; longer results are cut with a note to read on by line range. Unknown heading → the outline with "No heading named …". |
| `save_doc` | `markdown`, optional `title`, `description` | Saves the complete text as the run's draft. |
| `edit_doc` | `old_text` (non-empty), `new_text` (may be empty) | Replaces the one exact occurrence of `old_text` in the working copy and saves the result as the run's draft. Not found → "This text is not in the document. Read it again with get_doc." More than once → "This text appears n times. Include more of the surrounding text." |

Saving (both tools):

- Locks the session `FOR UPDATE`, then the draft (the worker's and Promote's order). A closed run → refused ("This run
  is closed."). The document gone → "This document no longer exists."
- Updates the working-copy draft, or creates a draft of the document (base = the live version, `origin='agent'`,
  `session_id` = the run, a `parent` source) when there is none.
- The run's previous draft was promoted or discarded → the new draft says so in the result ("Your earlier draft was
  promoted; this is a new draft based on version n."). It was edited by a person → "A person edited your earlier
  draft; this is a new draft, and get_doc now reads it."
- Content limits are `DocumentInput`'s; `edit_doc` refuses a result over the limit.
- Sources are recomputed from the run's tool calls as for Generate documents runs (`record_run_sources`).
- Result: "Saved draft (n lines)." plus the notes above.

The worker does not turn a revision run's final answer into a draft.

## API — `/agent-sessions` (`/agent-runs` already lists run records)

Input (`kind` discriminates `target`):

```
{ kind: 'revision', target: { document_id },
  instruction (1..20000), model_id, prompt_id?, system_prompt (1..20000),
  resources: { server_ids, service_ids, environment_ids, code_project_ids, collection_ids },
  tools: [names left on], expert_ids: [], subagents: false }
```

- `GET /agent-sessions/options?kind=revision&document_id=…` → enabled models (default: the model of the person's last run on
  this document, else the first); prompts tagged `document-revision` with their active text, plus a built-in default
  prompt when none exist; resources: every available server, service, environment, code project and collection, with
  the document's linked ones marked `linked` (the default selection) and servers that cannot be used (SSH not allowed,
  disabled, no key) marked with the reason; the choosable tools by group with what each needs; the experts.
- `POST /agent-sessions/preview` → `{config, first_message}`: the configuration Start would pin (credentials as references
  only) and the first message.
- `POST /agent-sessions` (201) → the session detail; it inserts the session (`purpose='revision'`, title = the instruction's
  first line, at most 120 characters), the first input (the instruction) and the audit event.
- `GET /agent-sessions?document_id=…` → that document's runs: id, title, status, model, who started it, last activity, its
  open draft (id) if any; open runs first, then by last activity.
- Messages, Stop, Close, history and the event stream stay on `/generations/sessions/{id}/…`, which must accept
  revision sessions.

Building the configuration (one function for preview and start):

- `ad_hoc_config` narrowed to `resources` (as Generate documents).
- Tools: the chosen names on, every other choosable tool off; `get_doc`, `save_doc`, `edit_doc` always on.
  `run_subagent` on only with `subagents: true`. Experts: only `expert_ids` (default none).
- System prompt: `system_prompt` followed by the run context: the document's title, folder, description, purpose,
  audience, linked resources and outline (not its text), and the rules: read with `get_doc`; change the document only
  with `save_doc` or `edit_doc`; ask in plain chat when unsure; a save creates a draft a person reviews; never claim the
  change is live.
- First message: the instruction.

Refusals (400 unless noted): document not found (404); disabled or unknown model; unknown resource (as Generate
documents); unknown or non-choosable tool; a tool whose need is not met ("ssh_run needs at least one server."); unknown
or unavailable expert; an unusable server.

Audit: `agent_run.created` with kind, document, model, tools switched off, experts, subagents. Draft saves use the
existing `document.draft.created` / `document.draft.updated`.

## UI

- **Document page:**
  - **Ask agent** button beside Save draft opens a side panel: Instruction (large, focused); Model; Servers and
    resources (linked ticked, "Add more" for the rest, unusable servers disabled with their reason); Tools (groups
    that apply to the ticked resources, all ticked; read and save document shown fixed); Agents (Allow subagents switch,
    expert picker); Advanced, collapsed (prompt and its system prompt text, editable for this run); **Preview** (the
    JSON in the panel) and **Start** (opens the run page).
  - **Agent runs** tab beside Drafts and Connections: instruction (first line), status, model, started by, last
    activity, link to its draft; open first.
- **Run page** `/documents/:documentId/agent/:runId`: the existing run workspace (conversation, tool calls, Stop,
  follow-up messages); header with the document's title (link back), **Show config** (saved JSON) and **Close**;
  side panel **Draft**: the run's current draft, base version, stale badge, **Open draft** (the Drafts tab view with diff,
  Promote, Discard). A finished turn with no successful save shows "No draft saved in this turn".
- **Drafts tab:** a draft written by a revision run links to that run's page.

## Tests

- Migration: up/down; `revision` requires `target_document_id`; deleting the document deletes its runs; down refuses
  with revision runs.
- API: preview equals what start pins; defaults are the linked resources; tool needing a missing resource refused;
  unknown tool/expert, disabled model refused; the three document tools cannot be switched off; experts only when
  chosen; subagents off by default; runs listed per document, open first; session routes accept revision runs.
- Tools: `get_doc` outline, section, unknown heading, line range, 50,000-character cut; `save_doc` creates then updates
  one draft; `edit_doc` not found, found twice, removal; promoted, discarded and edited drafts make the next save a new
  draft with the note; sources recorded; closed run and deleted document refused; the final answer never becomes a
  draft.
- UI: panel defaults and dependent tool groups; unusable server disabled; Preview shows the JSON; Start opens the run
  page; Agent runs tab; run page Draft panel and the no-save note; Drafts tab links the run.

## Not in scope

Moving Generate documents and discovery onto `/agent-sessions`; an agent promoting a draft; several documents in one run;
inline suggestions in the editor; choosing tools for Generate documents runs.
