# Documentation administration

Docs is a separate admin section with Documents, Prompts, Generate documents and Folders.
It supports Markdown CRUD, version history, resource links, versioned prompts and
document generation by Claude Code agents. RAG publication remains a separate next stage.

## Storage and versions

`ka.documents` provides the stable ID, unique slug, optimistic revision, current
version and future published-version pointer. `ka.document_versions` stores the
canonical Markdown, SHA-256 hash, title, description, purpose, audience, tags,
origin, base version, creator, timestamp and public resource snapshot. The database
checks the hash against the exact UTF-8 Markdown bytes and rejects content updates.
Each save creates a complete immutable version. Loading a historical version into
the editor changes only the unsaved draft; saving it creates a new version based
on the current document revision. Stale saves return 409 without losing history.

`document_code_projects`, `document_services`, `document_servers` and
`document_deployments` and `document_environments` are explicit many-to-many foreign-key relationships to the
existing inventory. They represent current associations and support filtering
before list pagination. The selected deployment must belong to a selected service
and server. Historical versions retain inventory names and IDs in `source_snapshot`
even when inventory is subsequently renamed or removed. These are save-time
inventory descriptions, not evidence that a live server was inspected.

Removing an inventory record clears its current document links, not document
history. Editing a service keeps retained deployment IDs and their document links.
Deleting an unpublished document requires confirmation and its current revision;
versions and links cascade, shared inventory remains, and an audit event is kept.
The API refuses deletion of a document with a published version until an unpublish
workflow can clean its derived RAG data.

## Documentation prompts

`documentation_prompts` holds a name, description, input kind (`code`, `server`,
`service`, `mixed`), enabled state, revision, active version and version counter.
`documentation_prompt_versions` holds immutable text and its first activation time.
Changing prompt text creates a new draft; metadata-only edits preserve identical
latest text. Activation is explicit and atomic under the parent row lock. At most
one version is active. Inactive versions can be deleted with confirmation, without
reusing version numbers. Whole-prompt deletion removes its versions. Enabled state
and active version are separate: a disabled prompt may retain its active version.

These prompts are analysis/writing templates. Agent system prompts remain in the
existing agent prompt system. Generation snapshots the exact selected prompt
text and built-in step config, so later edits or deletions cannot change past run evidence.

Migration [41](../db/migrations/0041_app_docs_prompt.up.sql) seeds
**Application documentation (Diátaxis)** as an enabled code-analysis template with
version 1 active. It combines the v2 `plugins/app-docs` skill, authoring guide,
interview workflow and chunk contract into one self-contained prompt. It covers
source citations, tutorials, how-to guides, reference, architecture explanations,
focused requests, incremental updates and retrieval-friendly Markdown. It supports
both Git and non-Git code folders. The v2 branch/commit/index workflow is replaced
by proposed managed document versions for review; the template executes nothing.

The fixture backfills the existing application workspace. An insert trigger seeds
it when a fresh installation first creates that workspace. There is no runtime
fallback that resets edits or recreates a deleted prompt on page load. A template
with the same name is preserved. Edit and activate versions through **Docs →
Prompts**, using the same draft lifecycle as any other template. Rollback removes
only an untouched fixture and refuses to discard a customized one.

## API and pages

All APIs are under `/api/documentation` and require the existing administrator
role. Mutations also require an attributed user. No credential fields are returned.

| API | Behavior |
| --- | --- |
| `GET /resources` | Public code-project, service, server and deployment choices |
| `GET /documents` | List current documents; `q`, `code_project_id`, `service_id`, `server_id`, `deployment_id`; AND across filters; 50 per page with `offset` |
| `POST /documents` | Create document and first version atomically |
| `GET /documents/{id}` | Metadata, current Markdown version, version summaries |
| `PUT /documents/{id}` | Save a new complete version with expected `revision` |
| `GET /documents/{id}/versions/{version_id}` | Read historical Markdown and resource snapshot |
| `DELETE /documents/{id}` | Expected `revision` and `confirmed=true` required |
| `GET, POST /prompts` | List / create analysis prompts; first text is a draft |
| `GET, PUT /prompts/{id}` | Read / save metadata and changed text as a new draft |
| `POST /prompts/{id}/versions/{version_id}/activate` | Activate using expected parent `revision` |
| `DELETE /prompts/{id}/versions/{version_id}` | Remove inactive version; expected `revision`, confirmation |
| `DELETE /prompts/{id}` | Remove prompt and versions; expected `revision`, confirmation |

The UI is at `/documents`, `/documents/new`, `/documents/{id}`,
`/documentation/prompts`, `/documentation/prompts/new`,
`/documentation/prompts/{id}`, `/documentation/generations` and `/documentation/folders`.
The shared Markdown renderer supplies preview; shadcn dialogs handle destructive
confirmation and navigation with unsaved edits.

## Generate documents

**Docs → Generate documents** starts a Claude Code agent session; the agent writes one document. The agent runs with
its own model, prompt, instructions, skills and Claude Code tools; the form has no model or prompt field.

`POST /api/claude-code/sessions` takes eight fields: `agent_id`, `code_project_ids`, `environment_ids`, `service_ids`,
`server_ids`, `instructions`, `document_name` and `folder`. At least one code project, environment, service or server is
required. Refusals (400): `NOTHING_CHOSEN` (nothing chosen), `NO_FOLDER` (Claude Code needs at least one working folder:
code projects, or environments or services linked to code projects, or an agent with code projects), `AGENT_MODEL` (the
agent has no enabled Claude model), and unknown or unavailable ids ("Select available ...", "Select an enabled Claude
Code agent.").

The session stores two snapshots. `prompt_snapshot` holds `system_prompt`, `claude_code_tools` and `agent_version`.
`context_snapshot` holds the chosen `code_projects`, `environments`, `services`, `servers` and `instructions`, plus
`folders` (the working folders, read-only, the first being the working directory) and `code_project_ids`. Credentials are
never included.

The first message is built from the context (`first_message` in `generate_documents.py`) and queued as the session's
first input:

```
Document the following.

## Code
- Payments API — /work/data/code/payments-api

## Services
- Payments — deployed on prod-1 (production); code: Payments API (/work/data/code/payments-api)

## Servers
- prod-1 (10.0.0.5): Payments host

## Instructions
Focus on the public API.

When you finish, reply with the complete document in Markdown as your final answer: it becomes the draft. After each
later message, reply again with the complete updated document.
```

`GET /api/claude-code/sessions/{id}/events/history` returns `{events, has_more}`: the latest page of events, oldest first,
or the page before an event id with `before` (`limit` 1 to 500, default 100). Events are redacted like the live stream; a
tool result keeps its preview and the full output stays in the run record.

Migration 0073 dropped `documentation_generations`, `documentation_workflows` and `documentation_workflow_jobs`, the
`documentation_generate` job kind and the `doc:*` run records, and added `documents.phase_metadata` (the service phase
export layout, read by `export_input`). Saved documents, versions and exports stay.

The canonical content stays in PostgreSQL. Every save also exports the current version to `DOCUMENT_OUTPUT_ROOT`, at `/work/data/<GENERATED_DOCS_FOLDER>` (default `generated-documents`) inside `DATA_ROOT`, replacing the previous export in place: `<document-slug>/README.md` (or `<folder>/<document-slug>/README.md` for a document in a lowercase documentation folder), `architecture-N.d2` and `metadata.json` (source commits), or `services/<service>-<id8>/<phase>/<phase>.md` for code documentation phases. Renaming a slug removes the old folder's files; deleting a document removes its export. On startup admin-api re-exports any document whose current export is missing (for example after upgrading from the old `<slug>/v<version>/` layout, whose folders are left on disk but not indexed). File export failure does not discard database versions and is shown in the save or job result. `POST /api/documentation/documents/{id}/export` retries export of the current saved version. `GET /api/documentation/documents/{id}/bundle` downloads the same document and diagram sources as a ZIP folder. The collection that holds `generated-documents` (`documents` on upgraded installs; create it on a fresh install) indexes this folder, so documents become searchable after **Refresh** in RAG → Collections; there is no separate publish step.
