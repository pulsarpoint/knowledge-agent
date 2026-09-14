# V2 inventory and implementation sequence

Inspected v2 at commit `2f0507fd52774b3cc163907962761c410e7c5874`, clean working
tree, under `/Users/graovic/fincore/knowledge-agent-v2`. V3 is a separate sibling
project. V2 remains unchanged. V3 now has its own runtime sources, lockfiles,
tests and complete Compose stack, copied selectively from that clean commit.

The runtime port includes the three preserved Python services, a standalone admin API, two React/shadcn apps, catalog
extractor, plugin/marketplace, service Dockerfiles, nginx, LiteLLM configuration
and Keycloak realm definition. It excludes v2's real `.env`, user data, virtual
environments, node_modules, build products, Git history, old Ansible deployment
and historical design documents. V3 replaces deployment configuration with
independent volumes, loopback ports, generated credentials and startup migrations.
Tests tied to the old Compose layout were updated for the new bundle.

This delivers an executable baseline, not the new PostgreSQL-backed settings
pages. The inherited reader/session/RAG code still uses its original local data
stores until those management workflows are implemented.

## Preserve and adapt

| Existing component | V2 source | V3 treatment |
| --- | --- | --- |
| Keycloak and authenticated gateway | `deploy/docker-compose.yml`, `deploy/keycloak/realm-docs.json`, `deploy/nginx.conf` | Preserve identity provider, SSO entry point, admin group and SSE behavior; add project authorization |
| Phoenix tracing | `services/docs-wiki/src/docs_wiki/tracing.py` | Preserve Phoenix/OpenInference and failure isolation; add job and generation trace correlation |
| Reader UI | `services/docs-wiki-ui/app/` | Keep Markdown, Mermaid, syntax highlighting, navigation, themes, freshness, search and chat in the reader; admin audit views now live in the independent `services/admin-ui` app |
| Retrieval | `services/rag/src/rag/` | Keep FTS5/sqlite-vec hybrid search, chunk/anchor contracts, HTTP/MCP APIs and lexical fallback; resolve collection/generation from PostgreSQL |
| Index orchestration | `services/rag/src/rag/workspace.py`, `workspace_worker.py` | PostgreSQL settings, durable folder jobs, worker leases and incremental hash-based processing |
| Source question service | `services/code-agent/src/code_agent/` | Keep citation verification, timeouts, read-only permissions and existing Claude/Codex backends |
| Grounded chat | `services/docs-wiki/src/docs_wiki/agent.py`, `rag_client.py`, `code_client.py`, `llm_client.py` | Preserve docs/code tools, escalation, source badges and model endpoint behavior |
| Chat history | `services/docs-wiki/src/docs_wiki/sessions.py` | Port capped model context, full persistent history, orphan-tool-result filtering and authorized sharing to PostgreSQL-backed session storage |
| User/session audit | `services/docs-wiki/src/docs_wiki/audit.py` | Move append-only transcript to `chat_turns`; preserve user/session drilldown after live chat deletion |
| Documentation gaps | `services/docs-wiki/src/docs_wiki/gaps.py` | Move gap records and matched code answers to `documentation_gaps`; export compatible input for plugin update runs |
| Documentation plugin | `plugins/app-docs/` | Copied unchanged now; add and verify admin SDK invocation contract in generation phase |
| Model routing | `deploy/config/litellm.yaml` | Preserve configurable chat/embedding routing; provider credentials remain deployment secrets |
| Deployment operations | `Makefile`, `deploy/ansible/deploy.yml`, service Dockerfiles | Rebuild unified v3 Compose/health/migration flow after runtime ports; preserve restart resilience and private internal services |

Current v2 storage is split between collection SQLite indexes, LadybugDB graph
state, SDK session SQLite, audit SQLite, JSONL gaps, and backoffice SQLite. There
is no existing relational model for Git/Jira/server settings to migrate.

## Implementation sequence and acceptance

1. **Database foundation — implemented here.** SQL schema, checksummed
   migrations, isolated tests, plugin preservation, admin/page/workflow design.
2. **Authenticated API and admin shell — partially implemented.** Separate
   reader/admin apps, SSO, Phoenix, repository settings and encrypted GitHub tokens
   are present. Service assets link applications, repositories and deployments;
   server inventory has a separate page for name, hostname/IP, description, tags
   and search, and can be registered before SSH setup. Project scoping, revision
   conflicts and secret exclusion are tested. Jira settings and bounded ticket
   retrieval are implemented. Project membership, document-set and full
   server-access settings remain planned.
3. **Repository sync and durable workers — initial implementation complete.**
   PostgreSQL claims, typed targets, idempotency, versioned checkouts, failed-pull
   preservation and expired-lease recovery are implemented. GitHub App auth,
   cancellation UI, event replay and checkout cleanup remain future extensions.
4. **Documentation files and generation.** Implement immutable blob writes,
   atomic upload/delete batches and actual Claude SDK plugin execution. Verify
   plugin init advertises `app-docs`, required tools/settings are available,
   interactive intent questions resume, and uploads cannot be silently replaced
   by a stale generation. Keep chunk validation separate from live indexing.
5. **RAG replacement and reader port.** Adapt v2 collection resolution to the
   database. Test real embeddings plus failure/cancel/restart, upload during
   indexing, deleted-file removal, old-reader retention and fresh citations.
   Port source cards and all existing search/MCP contracts.
6. **Jira Data Center integration — initial implementation complete.** Separate
   settings/ticket pages, encrypted PAT, identity/search checks, 1–200 recent-ticket
   fetches, pagination and safe errors are implemented and tested with an isolated
   Docker mock. Repeat fetches retain ticket IDs and failures preserve the prior
   selection. Real-instance acceptance, custom JQL/ticket-key imports, incremental
   watermarks and background reconciliation remain future work.
7. **Chat, audit, gaps and operational cutover.** Port/import existing histories,
   verify source badges/escalation and Phoenix traces, then deploy alongside v2
   on separate ports/volumes for acceptance before switching the gateway.

Server configuration and connection tests belong in this scope. Executing remote
diagnostics or arbitrary SSH commands needs its own feature design and tests.

## Existing-data migration

Implement an explicit, repeatable importer before cutover; this foundation does
not contain an importer yet.

- Back up v2 and retain its original volumes. Import one v2 app as a v3 project;
  register source repositories and copy Markdown into immutable revisions.
  Preserve file paths, front matter, and analyzed commits.
- Preserve Keycloak realm/users rather than duplicating passwords. Resolve stable
  subjects from trusted identity data; do not auto-link historical records using
  email alone. Preserve unmatched historical email as an audit snapshot.
- Import full SDK session payloads with deterministic ordering and keep the
  existing 40-item prompt-context cap in the session adapter. Validate tool-call
  pairs. Existing capability links need an explicit compatibility mapping or
  announced replacement; v3 stores capability hashes rather than raw tokens.
- Import audit events independently of live sessions so deleted session history
  does not erase the audit. Preserve historical timestamps and source flags.
- Fold `gaps.jsonl` code-answer entries onto the last preceding matching gap,
  following v2's question/session-ref matching rule. Record malformed and
  unmatched import records for review.
- Use an importer ledger or deterministic namespaced UUID mapping in the importer
  migration to prevent duplicate imports. Validate counts before and after each
  source; do not infer successful import from a zero process exit alone.
- Rebuild derived RAG vectors into v3 generation folders. V2 query
  service continues using its existing indexes during this process. Never point
  v3 workers at writable v2 directories.
- Keep Keycloak and Phoenix state in their own service stores and backup plans.
  Existing traces stay in Phoenix; new trace project/job attributes identify v3.

## Validation boundary

Database tests verify relational workflow rules, atomic pointer publication,
immutable histories, and migrations using PostgreSQL 17.10. The copied service,
UI and Go suites verify the inherited behavior. The new `make smoke` additionally
tests real Keycloak login through the gateway, admin identity, local indexing,
reading/search and code analysis, plus database/Phoenix availability.
Git sync, real model calls/embeddings, Jira and SSH execution, and the planned
management pages still require their own implementation-phase acceptance checks.
