# Admin CRUD with TODO execution endpoints

Status: implemented after user approval.

## Agreed scope

- Keep FastAPI, the current React/shadcn interfaces, PostgreSQL, Keycloak and Phoenix.
- Use **golang-migrate** for PostgreSQL migrations; no Go backend rewrite.
- Keep agent definitions, prompts, skills, model assignments, server settings, per-agent SSH overrides and resource selections as database configuration.
- Keep tools, chat UI, Test tabs and existing execution URLs.
- Execution endpoints return the plain text **TODO**. They do not create agents, run tools, invoke models, start tasks or create run records.
- Keep Git sync, Jira import/export, RAG processing/search and connection-test buttons working.
- Remove SDK agent runners and execution orchestration. Remove dependencies only when retained tools, interfaces and integrations do not need them.
- Preserve existing records, credentials, historical runs, files, checkouts and Docker volumes.
- Keep the existing required prompt, draft/active versions, independent agent settings, skills_agents relation, secret encryption, admin access and revision checks.
- Do not add a replacement framework, generic dispatch layer, new project, or unnecessary module renames.

## Design

Configuration remains a direct flow: admin form → FastAPI endpoint → PostgreSQL. Configuration routes receive the existing database store directly instead of reaching it through OperationsAgent.

Agent execution becomes a direct endpoint response: request → existing validation/access checks → TODO. The chat/test interface displays TODO and returns to idle. It must not expect a run ID, poll for completion, create a fake session, or present the result as completed agent work. Existing history remains readable.

Tools stay in their existing folder. Agent endpoints do not import or call them. This task does not finish or redesign the interrupted tool refactor.

The existing migration runner uses a custom checksum ledger named public.schema_migrations. Adopt golang-migrate using a separate migration-version table and a one-time verified baseline for an existing database. A fresh database runs the converted SQL migrations normally. Never replay initial CREATE statements against populated tables.

## Verification

Verify CRUD and prompt/credential behavior, retained integrations, exact TODO responses with no execution side effects, chat/test loading completion, clean installation, and migration adoption on a copy of existing data. See `docs/runtime-verification.md` for the deployed result.
