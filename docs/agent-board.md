# Multi-agent board

Open **Agents → Multi-agent board** at `/agent-board`. Enter one **Main task**,
select one or more **Subagents**, and click **Start task**. A dedicated coordinator
handles every task, even with only one specialist selected. It can delegate work,
review results, and request follow-up work. A summary agent then produces the final
answer from that conversation. It preserves clarification requests and reports
missing evidence or failed work.

The protected **Coordinator** appears first in **All agents**, marked with a
Coordinator badge. Open it to edit its name, purpose, model, notes, and execution
limits and final summary prompt in **General**. **Prompts** uses the same draft/active version workflow as
other agents. Its current model and system prompt are preserved during migration.
Notes are added to the active prompt at execution. These settings are snapshotted
when submitting a task, so later edits and activations apply to new tasks.

The board's **Coordinator settings** link opens that agent page. The coordinator
cannot be deleted or changed into a specialist; both the API and PostgreSQL enforce
this. It is excluded from the subagent picker and is permanently enabled.
Migration 32 enforces this in PostgreSQL; only specialists have an enable switch.

The board shows live coordinator messages and Queued / Running / Results columns
for delegated tasks. **View work** opens a specialist's activity and Markdown
answer. **View trace** opens retained execution history in Phoenix. **Task history**
switches between tasks; **Use task again** copies the task and team without executing
it. Status and results survive page refreshes. Each new submission starts a new
conversation; the board does not yet provide a continuing chat for a task.

## Execution

`knowledge_admin.coordinator.coordinator()` uses the OpenAI **Agents SDK**. It receives
an explicit model, model settings, system and summary prompts, task, specialist
tools, limits, and activity callback. It has no PostgreSQL dependency. It streams
the orchestrator, passes `run.to_input_list()` to a tool-free summary agent, and
streams the final response. Both use the coordinator's selected model.

`knowledge_admin.board.execute_task()` loads every selected specialist's saved
configuration and resolves its credentials. It opens each native `Agent` through
`knowledge_admin.agents.create_agent()` and exposes it with `Agent.as_tool()`.
The coordinator receives one named tool per specialist, described by that agent's
name, description, and “when to use” instructions. Only the selected team is exposed.

Each native agent retains its own prompt, model, instructions, enabled resources,
skills, SSH settings, and MCP connections. A board invocation boundary supplies
that specialist's private context, an isolated temporary workspace, a separate
usage counter, concurrency/subtask limits, and persisted task lifecycle. The
native SDK executes the nested agent; there is no generic agent-ID dispatcher.
Repeated or parallel calls to the same specialist use separate invocation contexts.

The coordinator receives each specialist's status, result, and safe error message
as a tool result in the same Agents SDK conversation. It can include earlier
findings in follow-up assignments. The subtask limit is checked before executing each delegation, including
parallel calls in the same model response. Credentials and resource availability
are rechecked when queued work starts. Configuration is captured when the board
starts; edits apply to new board tasks. Individual failed subtasks remain visible, and
the final answer can explain partial findings or missing evidence.

Coordinator and specialist models use OpenAI Responses/Chat Completions directly, or the Agents
SDK's `LitellmModel` for native Anthropic and Bedrock connections. Bedrock uses the
saved region and bearer key. Both use the same model connection code, with no
intermediate Responses proxy. No additional worker service is introduced.

The FastAPI lifespan owns a dispatcher elected with a PostgreSQL advisory lock.
Up to three coordinators and three specialists can run at once, with at most 30
unfinished main tasks and 10 selected specialists per task. Coordinator defaults
are eight orchestration model turns, 20 subtasks, and 30 minutes overall; these are editable.
The summary adds one model call within the same overall timeout.
Each specialist has a five-minute timeout. **Stop task** cancels the coordinator
and unfinished specialists while retaining completed results. External actions
already completed are not rolled back. After restart, interrupted tasks fail and
their queued subtasks are cancelled; only unstarted main tasks resume.

## Storage and API

Migrations 27–29 create the board tasks, selected team, and trace references.
Migration 30 introduced standalone coordinator settings and nullable root task
identities. Migration 31 adds one protected `ka.agents.is_coordinator` row and moves
its model and prompt into the normal agent/model and prompt-version records.
`ka.coordinator_settings` retains the linked agent ID, notes, limits, and edit
metadata. New root tasks reference the coordinator agent; existing history remains
readable. Downgrading migration 31 is blocked after coordinator edits or new run
history to avoid losing data. Migration 35 adds the editable summary prompt;
it is snapshotted with the other settings when submitting a task.

Rows retain configuration snapshots, model/prompt metadata, results, usage, and
timestamps. Credentials stay encrypted in their original records and are decrypted
only on the backend for execution. Snapshots contain references and safe metadata.
Known secrets are redacted from activity/results/traces; raw provider errors are
not returned. Submission, cancellation, and coordinator edits are audited.

Admin-only endpoints:

- `GET /api/agent-board/settings`: dedicated coordinator settings.
- `PUT /api/agent-board/settings`: notes, summary prompt, limits, and current revision. Stale
  revisions return HTTP 409. Identity/model edits use the normal `/api/agents/{id}`
  routes, and prompt drafts/activation use `/api/llm/prompts/{id}/versions`.
- `GET /api/agent-board/tasks?offset=0`: 20 main tasks, including their selected
  team and subtasks, plus `has_more`.
- `GET /api/agent-board/tasks/{id}`: a task and its team/results.
- `GET /api/agent-board/tasks/{id}/activity`: live SSE activity for the whole team.
- `POST /api/agent-board/tasks`: `{id: UUID, subagent_ids: [agent_id, ...], task: string}`.
  Returns HTTP 202. Repeating the same ID, actor, team, and task returns the existing
  task; conflicting reuse returns HTTP 409. No coordinator agent ID is accepted.
- `POST /api/agent-board/tasks/{id}/cancel`: stop a task and unfinished specialists.

The board and per-agent Test/chat endpoints execute real agents. Direct tests use
the same specialist constructor, without a coordinator or summary agent.

## Tracing

One native SDK `with trace('Agent board task'):` covers both orchestration and
summary. The SDK creates the nested agent, model, and tool spans automatically;
the OpenInference integration exports them to the existing Phoenix project.
`agent_tracing.py` configures that exporter and filters credentials and raw errors.
The default OpenAI trace exporter is disabled. The string passed to `trace()` is
the workflow name, not its ID.

The board stores the OpenTelemetry trace and span IDs from SDK lifecycle hooks
for its Phoenix links. Main tasks link to the whole trace, including the summary;
completed specialists link to their agent span. SDK IDs and OpenTelemetry IDs
are different. `SdkActivity` handles bounded UI previews only; it does not create
tracing spans. PostgreSQL retains task status, cancellation, and results, since
tracing alone does not provide those application features.
