# Generate documents with a blank agent — design

Date: 2026-10-08. Status: approved in conversation.

## Goal

Docs → Generate documents → New (`/documentation/generations/new`) no longer asks for a saved agent. Every generation
runs an **ephemeral native agent** that exists only in its session: the user writes its short system prompt, sees (and
may edit) the context generated from what they chose to document, picks a saved documentation prompt or writes their
own, and names the document. Nothing is written to `ka.agents`.

User decisions:
- No agent picker. A session has no `agent_id`; it is identified by its name (the document name). Ephemeral agents
  are instantiated during processing, like subagents, and disappear with the session.
- Native runtime only. The model is chosen in the Agent context panel. Tools = all tools of the kinds selected;
  resources = exactly the selection.
- The system prompt and the generated context form the agent's **system prompt**; the chosen/written prompt is the
  **first message**.
- Document name and folder stay as they are.
- The "run a saved agent" path is removed from the API now, not kept. Consequence accepted: Claude Code agents can no
  longer start sessions (their Test tab only linked here; Test sessions are native-only). Existing Claude Code
  sessions and the Claude Code worker keep working; removing Claude Code entirely is a later step.

Out of scope: Claude Code runtime for blank agents, per-tool checkboxes, saving a run's setup as a reusable agent,
filtering prompts by `input_kind`.

## Page

Top to bottom:

1. **Agent context** — a button that toggles a panel. Collapsed, it shows the model name and the first line of the
   system prompt. Expanded:
   - **Model**: select of enabled native (`openai` runtime-compatible, `model_fits('openai', …)`) models; defaults to
     the first one by name. With none: "Add a model in Models first." and Generate stays disabled.
   - **System prompt**: textarea (max 20,000), prefilled with the default:
     > You write technical documentation for software systems. Read the code, documents and servers you were given
     > before you write. Cite evidence as file:line, document path or command. Say what you could not verify.
   - **Context**: textarea (max 100,000) with the text generated from the selection (see Live context).
2. **What to document** — the five kinds with their checklists as today (Code, Documents, Environment, Services,
   Servers). The per-agent "accepts" filtering and its notes are removed.
3. **Prompt** — select: the enabled saved documentation prompts (label `name · input kind`), and "Write my own".
   Choosing a saved prompt fills the **Prompt** textarea (max 100,000) with its active version's content; editing it
   changes only this run. "Write my own" leaves the textarea empty. Switching to another saved prompt after editing
   asks for confirmation ("Replace your edited prompt?").
4. **Document name** and **Documentation folder** — unchanged (including the folder default from the first code
   project).

Generate is enabled when: a model is chosen, at least one entry is selected, system prompt and prompt are non-empty,
name and folder are valid.

The `?agent=` query parameter is removed. A Claude Code agent's Test tab card says "Claude Code agents can no longer
start sessions. Use a native agent." with no button.

### Live context

- While the Context textarea has not been edited by hand, every selection change refreshes it from the server
  (debounced 300 ms; responses for an older selection are ignored). While it loads, the textarea shows its last text
  and a small "Updating…" note.
- After a manual edit, selection changes no longer replace it; a note says "Edited — no longer follows your
  selection" with **Regenerate from selection**, which confirms ("Replace your edits?") and then refreshes and resumes
  following.
- With nothing selected the context is empty.
- What the textarea holds at submit is exactly what the agent gets.

## Backend

### `resource_context` (`generate_documents.py`)

`resource_context(context: dict) -> str` returns the Markdown describing the selected subjects: the `## Code`,
`## Documents`, `## Environments`, `## Services` and `## Servers` sections that `first_message` builds today (same
lines, same one-line flattening of names and descriptions), headed by `# What you are documenting`. Empty selection →
`''`. `first_message` has no caller left and is removed; `subjects()` no longer reads `instructions`.

### Context endpoint

`POST /api/claude-code/context` with `{code_project_ids, collection_ids, environment_ids, service_ids, server_ids}`
(same limits and de-duplication as `SessionInput`; an empty selection is allowed) → `{text}`. It runs `subjects()` and
`resource_context()`; unknown ids are 400 as in session creation. Read-only, no audit.

### `SessionInput`

Drops `agent_id`; adds:
- `model_id: UUID`
- `system_prompt: str` (1–20,000, non-blank)
- `context: str` (0–100,000)
- `prompt: str` (1–100,000, non-blank)
- `prompt_id: UUID | None` (recorded only; the text sent is `prompt`)

`instructions` is removed (the prompt replaces it). All text: no null characters, stripped. Session creation no
longer checks code folders on disk (that was Claude Code's working directory); `context_snapshot.folders` still lists
the resolved code projects' folders and `code_project_ids` the chosen ones, so Save document links them as today.

### Ephemeral agent (`knowledge_agents/ephemeral.py`)

`ephemeral_config(conn, project, *, model, system_prompt) -> dict` builds an AgentConfig in the same shape
`get_agent_config` returns, without an agents row:
- `id: None`, `name: 'Ad-hoc agent'`, `runtime: 'openai'`, `resources: 'per_run'`, the given model (with its
  `api_key_ref`), `system_prompt.content` = the given text, empty instructions, skills off, MCP off;
- capabilities documents, code, SSH and services on with "everything allowed" lists (all collections, all code
  projects, all servers, all services of the project), so that `run_resources.resolve` narrows them to the selection
  exactly as it does for per-run agents (environments bring their code projects, servers and services);
- tools: every catalog tool `on` (never `run_subagent`); after `resolve`, a capability whose list is empty is
  switched off, so the runtime's `needs_met` offers exactly the tools of the selected kinds (`report_not_covered`
  never, since grounded is off).

`create_session` then: checks the model (exists, enabled, `model_fits('openai', model)`, else 400 "Choose an enabled
model."), resolves the selection, builds the session context, and pins:
- `agent_config` = the resolved config with `system_prompt.content` = `system_prompt + '\n\n' + context` (context
  omitted when empty);
- `run_resources` = the resolved run resources;
- `prompt_snapshot` = `{system_prompt, context, prompt_id}`;
- `agent_id` = NULL, `runtime` = `'openai'`, `model_id`, `title`/`document_name` = the document name;
- first input = `prompt + '\n\n' + FINAL_ANSWER`.

Audit: `claude_code.session.created` with `{runtime: 'openai', ephemeral: true, model_id, prompt_id}`.

The native session worker already runs from the pinned `agent_config` (`load_credentials` skips the agent check
when no agents row matches). `ka.agent_run_events.agent_id` is NOT NULL text without a foreign key: ephemeral turns
record `'ad-hoc'` (`AD_HOC_AGENT`), and the worker's claim query names them "Ad-hoc agent".

### Migration `0082_ephemeral_sessions`

- `ALTER TABLE ka.agent_sessions_cc ALTER COLUMN agent_id DROP NOT NULL`;
- check: `purpose <> 'test' OR agent_id IS NOT NULL` (Test sessions keep their agent);
- down: delete sessions with NULL `agent_id` (inputs cascade) and restore NOT NULL.

### Queries

`SUMMARY`/`DETAIL` use `LEFT JOIN ka.agents`; `agent_name` is `coalesce(a.name, 'Ad-hoc agent')`. Any other code that
joins sessions to agents (Test sessions, reviews, run records, agent deletion) is checked for inner joins that would
hide ephemeral sessions.

## UI elsewhere

The generation list and session page show "Ad-hoc agent · <model>" where they showed the agent name; links to the
agent are omitted when `agent_id` is null.

## Errors

| Problem | Behaviour |
| --- | --- |
| No model / disabled / not native | 400 "Choose an enabled model." |
| Nothing selected | 400 (existing `NOTHING_CHOSEN`) |
| Empty system prompt or prompt | 422 validation, form prevents it |
| Text too long / null characters | 422 validation |
| Unknown selection id | 400 as today |
| Context endpoint fails | Form keeps the last text and shows the error under Context; Generate still works |

## Testing

- `resource_context`: each section, empty selection; `first_message` output unchanged.
- Context endpoint: selection → text, empty selection, unknown id.
- `ephemeral_config` + resolve: resources narrowed to the selection (environment and service expansion), tools of the
  selected kinds only, no skills/MCP/`run_subagent`, model reference.
- `create_session`: NULL `agent_id`, pinned config with combined system prompt, run resources, first input, audit;
  model errors.
- Migration up/down; Test sessions still require an agent.
- List/detail queries return ephemeral sessions with "Ad-hoc agent".
- Form: no agent picker; live context refresh and stale-response handling; edit lock and Regenerate; prompt prefill,
  replace confirmation, Write my own; submit payload; Generate enablement.
