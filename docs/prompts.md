# Agent system prompts

In the **Agents** sidebar section, select **Search**, **SSH**, or **Code search**, then open
**Settings → System prompt**. Model selection and the prompt editor now live in
the same agent settings. **Test** contains the testing interface and a link back
to the active prompt. The old `/llm-settings/prompts?agent=...` links redirect to
the corresponding agent settings.

Each agent has exactly one active version. Create a new draft from any version,
edit its title and instructions, and save it without affecting live requests.
**Set active** opens a confirmation dialog. Activation publishes the saved content
and switches the active pointer in one PostgreSQL transaction. Running requests
keep their existing instructions; new requests load the active version without a
restart, including requests in existing conversations.

Published prompt content cannot be edited. Select an archived version and activate it to
restore its exact content. Draft edits and activation use revision checks to
prevent overwriting concurrent changes. Configuration events record the actor,
agent and version, without copying prompt content into audit metadata.

Use **Delete** beside a draft or archived version to remove it permanently after
confirmation. The active version cannot be deleted. Deleted instructions cannot
be restored or reactivated; deletion audit events remain. Version numbers are
never reused. Concurrent activation or edits cause stale deletion requests to be
rejected. Migration `0014_prompt_version_deletion.up.sql` enables inactive deletion
and stores the next version number on each agent.

The editable text describes agent behavior and answer style. The runtime still
adds available tools, source restrictions, citation/output contracts, and the
selected document catalog. These are not template variables; no string formatting
or code execution is performed on saved prompts. Source repositories and documents
remain untrusted evidence.

Migration `0013_agent_prompts.up.sql` creates `ka.agent_prompts` (the active pointer)
and `ka.agent_prompt_versions` (draft and published content). Initial active
versions preserve code-analysis behavior and make search diagrams explicitly
optional. Native standalone agents use the bundled prompts; the Docker deployment
uses the database through the existing `LLM_SETTINGS_SOURCE=database` setting.

## Backoffice API

All endpoints require an authenticated administrator.

| Method | Route | Body / purpose |
| --- | --- | --- |
| GET | `/api/llm/prompts` | Agents, active pointers and version history |
| POST | `/api/llm/prompts/{agent}/versions` | `{title, content}`; always creates a draft |
| PUT | `/api/llm/prompts/{agent}/versions/{id}` | `{title, content, revision}`; drafts only |
| POST | `/api/llm/prompts/{agent}/versions/{id}/activate` | `{revision, prompt_revision}`; publish or reactivate |
| DELETE | `/api/llm/prompts/{agent}/versions/{id}` | `{revision, prompt_revision, confirmed: true}`; inactive only, returns 204 |

`agent` is `search`, `operations`, or `code`. Status is derived as `active`, `draft`, or `archived`.
Prompt content is limited to 40,000 characters.

## Mermaid validation

Search answers default to prose; diagrams are optional. The admin UI defers diagram
rendering until streaming completes, checks syntax with Mermaid **11.16.0** before
rendering, preserves code fences during citation formatting, and shows an English
message plus escaped source if rendering still fails. Browser layout/rendering
failures can still occur even for syntactically valid diagrams. Diagrams use the
same available font for measurement and display, outside the Markdown code-block
wrapper, so inherited monospace styles do not clip labels.

The server-side Node parser (`packages/mermaid-validator`, `make build-mermaid`,
`make test-mermaid`) was removed together with the Reader application. The search
prompt still mentions a `validate_mermaid` tool, but no agent currently registers it.
