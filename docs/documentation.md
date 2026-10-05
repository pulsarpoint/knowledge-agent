# Documentation administration

Docs is a separate admin section with Documents, Prompts, and Generate documents.
It supports Markdown CRUD, version history, resource links, versioned prompts and
background documentation generation. RAG publication remains a separate next stage.

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
| `POST /generations` | Queue environment or service documentation with immutable prompt and resource snapshots |
| `GET /generations` | Read recorded generation history, 50 per page |

The UI is at `/documents`, `/documents/new`, `/documents/{id}`,
`/documentation/prompts`, `/documentation/prompts/new`,
`/documentation/prompts/{id}`, and `/documentation/generations`.
The shared Markdown renderer supplies preview; shadcn dialogs handle destructive
confirmation and navigation with unsaved edits.

## Migration and next stage

Migration 39 retires the unused v2-style document-set/plugin/RAG-collection schema.
It refuses to run if legacy document sets, collections or their jobs contain data.
It leaves the current folder RAG tables, chunks, vectors and processing jobs intact.
Migration 40 creates the managed-document schema. Its down migration refuses to
discard documents, prompts or generation history; empty-schema rollback is tested.

`documentation_generations` reuses `ka.jobs` with `documentation_generate` kind.
Such jobs have no single inventory target: one run may use multiple sources and
propose multiple document versions. The generation row pins create/update mode,
user instructions, agent configuration, prompt texts and source evidence. Its
input snapshot is immutable and must reference a queued documentation job.
The generation endpoint and worker are described below.

The publication stage will export a selected immutable version into the fixed
RAG mount, map derived files to document/version IDs and use the existing content
hash pipeline. Draft edits must not alter published search content. Publishing,
unpublishing and deletion will need coordinated cleanup and entity-scoped search.
No credentials may be included in generation or document snapshots.


## Documentation workflows

**Docs → Generate documents** offers two scopes:

- **Whole environment:** an architecture document covering all environment members,
  their recorded dependencies, linked code projects and deployment inventory. The
  **Full environment architecture** explains system purpose, functional
  relationships, end-to-end request/event/data flows, logical implementation,
  deployment, networking, reliability and investigation paths. A connection matrix
  and relationship catalogue connect these abstractions with cited evidence. Three
  D2/TALA views show functional dependencies, deployment/runtime mapping and recorded
  endpoints. Logical dependency arrows never imply verified network reachability,
  traffic direction or default ports. This synthesis step has no SSH or source tools. Select **Server infrastructure architecture**
  separately to inspect one environment member server over SSH. It documents the
  host's runtime layers, storage, networking, configuration locations, logging,
  monitoring and operational boundaries. Generate either or both documents; each
  has its own editable active prompt. Infrastructure requires exactly one server
  with saved global SSH access and never connects onward to other hosts.
- **Service:** independently select a D2/TALA component diagram, code analysis,
  and deployment documentation. Each selection produces a separate document.
  Code analysis requires a Custom service and linked projects with mapped folders.
  A standalone diagram can inspect selected custom code. When code analysis is
  also requested, the diagram step reuses its findings without rescanning source.
  Built-in/external service diagrams use recorded inventory instead of source.
  Deployment requires an environment containing the service and selected servers;
  it uses each selected server's saved global SSH user, private key and sudo setting.

These are fixed application workflow specialists using the existing Agents SDK
runtime, not profiles in `ka.agents`. Select one enabled compatible text model
from LLM Settings for the run. No configurable AI agent or agent model assignment
is required. Each step shows its active documentation prompt and an editable copy.
Edits apply only to that run; use **Manage saved prompt versions** to create and
activate a reusable version in Docs → Prompts. The API pins the active version ID
and rejects stale submissions if it changed while the form was open.

Migration 46 adds document–environment associations and the initial code, service,
deployment and diagram prompt fixtures. Migration 47 adds **Environment architecture**
and upgrades the untouched diagram fixture to support standalone inspection.
Customized prompts are preserved; rollback refuses to discard customized fixtures.
Migration 48 adds the active **Server infrastructure architecture** prompt.
Migration 49 upgrades the untouched Environment architecture fixture to version 2
with a comprehensive multi-view document contract, retaining the old version.
Existing app-docs and service-purpose templates remain available. Fixtures are
seeded for existing and new application workspaces, never reset on page load.

`POST /api/documentation/generations` accepts `scope`, `model_id`, optional
`service_id`/`environment_id`, `code_project_ids`, `server_ids`, `units` (`kind`,
`prompt_id`, `prompt_version_id`, optional `prompt_override`), `instructions`, and
`update_existing`. Jobs snapshot the actual prompt, original active text, model,
source inventory and scoped tool configuration. Credentials are encrypted at rest;
snapshots contain references, and only the worker resolves their values.
`knowledge-doc-worker` executes code → diagram → deployment, or environment
steps in the order infrastructure → architecture. When both are selected, synthesis
receives the server findings from that run. It also receives pinned current document
versions: documents associated only with this environment, and shared code/diagram
documents linked to its services or code projects. Other environments and previous
architecture outputs are excluded from supporting evidence. Markdown, version IDs,
hashes and timestamps are snapshotted at queue time. Architecture generation has no
SSH tools even when requested together with infrastructure. Diagram output uses a validated component/connection schema;
the application serializes D2, rendered with TALA in an inert SVG image.
Documentation runs request up to 16,384 output tokens per model response so long
documents are not limited by the provider adapter's smaller fallback. The selected
model must support that output limit. Other agent runs retain their existing limits.
Background document jobs use complete structured responses; job progress and tool
hooks remain available. Interactive agents continue to stream their responses.

All selected outputs save in one transaction; an intervening manual edit aborts
that save with a conflict. Code and diagram documents link to the service and
selected code projects across environments. Deployment documents link to the
service, environment, selected servers and matching deployment records. Whole
overview documents link all member services/servers and their code projects.
Infrastructure documents link the environment, selected server and services/code
projects with recorded deployments on that server in that environment. Newly
observed runtimes are documented as unassigned rather than silently added to
inventory. Each environment/server pair has an independent document and version
sequence; generating infrastructure for another server does not overwrite it.
Deployment environment labels are still text and use exact environment-name
matches. Membership alone does not prove deployment. Dependencies remain shared
service records filtered to the selected environment's members.

Prompts require source citations, uncertainty, targeted read-only SSH inspection,
and sanitized configuration metadata. Read-only SSH behavior is an instruction
to the existing tool, not an OS-enforced remote account restriction.

The canonical content stays in PostgreSQL. Generated versions also export to `DOCUMENT_OUTPUT_ROOT`, mounted at `/work/generated-documents` from `DATA_ROOT/generated-documents` (or `GENERATED_DOCS_PATH`). Folders use `<document-slug>/v<version>/README.md` and `architecture-N.d2`. File export failure does not discard database versions and is shown in the job result. `POST /api/documentation/documents/{id}/export` retries export of the current saved version. `GET /api/documentation/documents/{id}/bundle` downloads the same document and diagram sources as a ZIP folder. Editing and deletion in administration affect canonical records; exported version folders are retained snapshots. **Publish to knowledge base** (`POST/DELETE /api/documentation/documents/{id}/publish`) copies the current version into a RAG folder as `<folder>/<slug>.md` and keeps it on the latest version.
