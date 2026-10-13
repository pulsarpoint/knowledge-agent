# One sessions API — design (cleanup step 2, part 1)

Date: 2026-10-13
Status: approved in conversation, section by section. Part of the project cleanup (step 1, remove leftovers, merged
05a4fa0). Builds on Ask agent on a document (`2026-10-12-document-agent-design.md`) and its chat pane
(`2026-10-12-document-agent-pane-design.md`).

## Why

Every agent conversation is one row in `ka.agent_sessions`, run by the same worker, but it is started and operated
through three APIs: the generic run operations live under `/api/generations/sessions/{id}` (named after Generate
documents), Test runs under `/api/agents/{id}/sessions`, Ask agent under `/api/agent-sessions`, and discovery and
event handlers insert sessions in their own code. Five code paths build a run's configuration, the worker branches on
`purpose` after each turn, and run links point to `/documentation/generations/:id` even for runs that are not about
documentation.

An agent request is always the same thing: a prompt, instructions, tools and what the agent may use, chosen from where
it is started; a saved agent (`agent_id`) adds its own configuration. So there is one start, one run API and one run
page. Where a run is started decides its defaults, not its behaviour.

## Principles

- **One request shape.** `{agent_id?, model_id, prompt_id?, system_prompt, context?, message, resources, tools,
  expert_ids, subagents}` (`skills` joins with fixed agents). Without `agent_id` the run is ad-hoc.
- **Behaviour comes from the prompt.** Code appends nothing that tells the agent how to behave. The start site adds only
  facts (`context`, e.g. "Document: Runbook, id …"); tool descriptions say what a tool does and takes; how to work is
  the prompt's text, which a person can read and change in the prompt library.
- **What a run may use is an allowlist, enforced by the server.** One tool per action (no per-resource tools); the
  run's resources are pinned when it starts, and each tool checks its argument against the pinned list.
- **The server keeps only guarantees**: saves go to a draft, never live; the model must be enabled; a server must
  allow SSH; a fixed agent's request can only narrow. These are checks, not instructions.
- **`purpose` is a label** (where the run was started, for lists and audit). It does not select behaviour.

## Roadmap of this step

This spec is **part 1**: the generic API, the allowlist, document tools as plain tools, Ask agent moved onto it, and
the shared run page. Each later part moves one start flow onto the generic start and deletes its old endpoint:

2. Test (a fixed agent; the narrowing rule below; Review as a fixed-agent extra).
3. Generate documents (a folder resource and a create-document tool).
4. Server discovery (a server resource, proposals; discovery's document access revisited).
5. Event handlers (a fixed agent, started internally).

## API (`knowledge_admin/sessions/`, router `/api/sessions`)

| Endpoint | Replaces |
|---|---|
| `GET /sessions/{id}`, `POST /{id}/messages`, `/{id}/stop`, `/{id}/close`, `GET /{id}/events`, `/{id}/events/history` | the same routes under `/api/generations/sessions/{id}`, moved unchanged |
| `GET /sessions?document_id=` (runs started from a document), `GET /sessions?purpose=documents` (the Generate documents list) | `GET /api/agent-sessions?document_id=`, `GET /api/generations/sessions` |
| `GET /sessions/options` (`document_id` optional: ticks that document's linked resources and lists it as editable) | `GET /api/agent-sessions/options?kind=revision&document_id=` |
| `POST /sessions/preview`, `POST /sessions` | `/api/agent-sessions/preview` and start |

- **Start input**: `{message, model_id, prompt_id?, system_prompt, context?, resources, tools, expert_ids, subagents,
  started_from?}`. `resources` adds `documents: [{id, editable}]` to the existing servers, services, environments,
  code projects and collections. `started_from` is `{document_id}` for now: a label for lists, never read by the agent
  or the tools. `kind` and `target` (the old Ask agent shape) go away; `instruction` becomes `message`.
- **`agent_id`** is refused in part 1 ("Saved agents start from their Test tab for now."); part 2 accepts it.
- Preview and start share one builder, so they cannot differ; preview stores nothing.
- **Removed**: `/api/agent-sessions/*`; the generic routes under `/api/generations/sessions/{id}`; "Save as draft"
  (`POST /api/generations/sessions/{id}/document`) and its button (every finished turn saves its draft by itself since
  0098).
- **Unchanged until their part**: Generate documents' `POST/GET /api/generations/sessions` and `/context` (part 3),
  `/api/agents/{id}/sessions` and its review (part 2), discovery's start (part 4).

## Enforcement (where the allowlist holds)

All in the agent session worker and the start endpoint, against the configuration pinned when the run started; the
model only supplies tool arguments, and later messages only add inputs.

1. **Start pins the allowlist.** `POST /api/sessions` checks each requested resource (it exists in the workspace, is
   usable: a server allows SSH and has a connection, a project is not archived, the document exists) and pins the
   result in `agent_config`. The run never re-reads the request.
2. **Only offered tools run** (`tools/sdk.py`: a tool not in the run's offered list is refused).
3. **Each tool checks its argument** with one helper, `allowed(config, kind, id, write=False)` in
   `capabilities/context.py`, returning the pinned entry or raising "This {kind} is not available in this run." The
   document tools use it in part 1; `ssh_run` and the other per-resource checks switch to it in the same change, so
   every kind uses one rule.
4. **The tool schema lists only the allowed choices**: when the worker binds a run's tools, `document_id` in the
   document tools is an enum of the pinned ids, with their titles in the description. This only keeps the model from
   guessing; check 3 is the guarantee. Above 50 items the enum is left out and check 3 alone applies.

## Fixed agents can only narrow (built in part 2)

With `agent_id`, the agent's saved configuration is the ceiling:

| Part of the request | Ad-hoc | With `agent_id` |
|---|---|---|
| Message, context | yes | yes (context as facts, never instructions) |
| Resources | anything usable | per-run agents: a subset of the saved selection (`run_resources.resolve`); fixed agents: none |
| Tools | any that apply | switch the agent's tools off, never on |
| Experts, subagents | any offered | a subset of the agent's experts; subagents only if the agent allows them |
| Model, system prompt, skills | chosen | the agent's own |

One function, `narrow(config, request)`, used by start and preview; options for an `agent_id` return only what may be
chosen.

## Tools and pinned configuration

- **`get_document(document_id, section?, start_line?, end_line?)`**: one reader (the existing inventory tool, extended).
  It reads the run's own open draft of that document when there is one, otherwise the live version. Allowed for
  documents in the run's resources, and — unchanged for now, read-only — documents attached to inventory entries.
  Without `section` and lines it returns the outline with line numbers and the length.
- **`edit_document(document_id, old_text, new_text)`** and **`save_document(document_id, title?, description?,
  markdown)`**: the old `edit_doc` and `save_doc`, renamed, with the same semantics and stale checks. Allowed only for
  documents pinned as editable; each saves the run's draft of that document and refreshes the draft's sources (moved
  from the worker, whose `revision` branch goes away; its `documents` branch stays until part 3).
- They are ordinary catalog tools: `needs='editable_document'` (applies only when the run has an editable document,
  as `ssh_run` needs a server). The `revision` run-only need goes away; `discovery` stays.
- **Pinned config**: `document_access: [{id, title, editable}]` (the existing `documents` key is the RAG collections
  setting); `revision_run` goes away. The start's `context` is stored in `prompt_snapshot` and added to the system
  prompt under a `# Context` heading; nothing else is appended.
- Removed: `get_doc`, `RULES`, `revision_context` and the code that appends them.

## Data and migration 0100

- **Built-in Ask agent prompt**: seeded into the prompt library for each workspace (existing ones, and new ones through
  the workspace seed function), tagged `document-revision`, with the old default text plus the editing rules written
  for the renamed tools. The options make it the default; `DEFAULT_REVISION_PROMPT` leaves the code.
- **Open Ask agent runs** (purpose `revision`, not closed) are closed with the message "Closed by an update; start a new
  chat." Their pinned configuration uses `revision_run` and the old tool names. Closed runs stay readable.
- `target_document_id` keeps its name and constraint; its comment says it records where the run was started.
- **Down**: removes the seeded prompts (refuses if one was customized, as migration 41 does); closed runs stay closed.

## UI

- **`lib/sessions.ts`**: get, send, stop, close, events, history, list, options, preview, start, and
  `sessionPath(id)`. It replaces the generic half of `generations.ts` and all of `agentSessions.ts`. `generations.ts`
  keeps only the Generate documents form calls (part 3); `testSessions.ts` stays (part 2).
- **Ask agent**: the settings sheet lists the document under Resources (editable, locked on) and the document tools
  with the other tools (on by default, applying only with an editable document). The prompt's editing rules show under
  Advanced. Preview shows the context lines. The pane lists runs with `GET /api/sessions?document_id=`; its behaviour,
  the draft bar and one conversation per document are unchanged. Browser-saved settings in the old shape are dropped
  and recomputed from the options.
- **Run page `/sessions/:id`**: any run, with `RunWorkspace`; heading with title, status, model and "Started from"
  (a link to the document when there is one). Replaces `/documentation/generations/:id` and `GenerationView`, without
  a redirect. Links go to it: the Generate documents list, a draft's "Agent run" link (Ask agent drafts still open the
  document's pane), Activity, and `DiscoveryRunView`'s fallback for a run that is not a discovery run.
- **History**: "No draft saved in this turn" recognises both the old and the new save tool names.
- Unchanged: the Generate documents form and list page, the discovery run page, the agent's Test tab.

## Edge cases

- A tool call on a document outside the run's resources, or a save on a read-only one: the tool answers "This document
  is not available in this run." and the agent continues; the run view shows the error on that call.
- The started-from document is deleted: its runs go with it (unchanged).
- A document in the resources is deleted during the run: `get_document` answers "Document not found"; saves fail the
  same way.
- An editable document's live version moves during the run: the stale checks of `edit_doc`/`save_doc` carry over
  unchanged.
- A start naming a document twice, or both read-only and editable: one entry, editable wins.

## Tests

- **Backend**: preview pins what start pins; ad-hoc start; `agent_id` refused in part 1; unusable or unknown resources
  refused by name; the editable document must exist; context under `# Context` and nothing else appended; edit and
  save refused outside the allowlist or on a read-only document; `get_document` reads the run's draft; the schema enum;
  `allowed()` per kind; run operations under `/api/sessions` (existing tests moved); list by `document_id` and by
  `purpose=documents`; migration 0100 up (prompt seeded in existing and new workspaces, open Ask agent runs closed,
  others untouched), down, up again; removed routes answer 404.
- **UI**: pane and sheet on `lib/sessions.ts` with the request body pinned (`resources.documents` with `editable`);
  old browser settings dropped; `/sessions/:id` for an ad-hoc and a document run, "Started from", no Save as draft, a
  refused tool call shown as an error; links to `/sessions/:id`; old and new save tool names in history.
- Done means the suites, the type check and the route check pass (apart from the known code-graph worktree failures
  and the two failures already on main), and a search finds none of the removed names and paths.

## Not in scope

Moving Test, Generate documents, discovery and event handlers (parts 2–5); skills in the request; several editable
documents in one Ask agent conversation from the UI (the API allows it); renaming `target_document_id`; discovery's
document access.
