# One sessions API, part 2: saved agents and the Test tab — design

Date: 2026-10-13
Status: approved in conversation, section by section. Builds on part 1 (`2026-10-13-sessions-api-design.md`, merged
b1042cc), which made `/api/sessions` the one start for ad-hoc runs and refused `agent_id` for later.

## Why

Part 1 left saved agents on their own API: the Test tab starts runs with `POST /api/agents/{id}/sessions`, lists them
and asks for tuning suggestions under `/api/agents/{id}/sessions…`, and has its own chat page, resource picker,
History and Show config panels. An agent run is the same thing wherever it starts; a saved agent only adds its own
configuration (`agent_id`). Part 2 moves saved agents onto the same start, and the Test tab onto the same chat panel
and settings sheet as Ask agent.

## Decisions

- The Test tab uses the same chat panel and settings sheet as Ask agent.
- Testing a saved agent can only narrow its configuration: switch its tools off, choose a subset of its experts, turn
  subagents off, and (a per-run agent) choose resources from its saved selection. Its model and system prompt are its
  own; to try another prompt, clone the agent (as today).

## API

**Start with `agent_id`.** `POST /api/sessions` and `/preview` accept `agent_id` (part 1 refused it). The saved
agent's configuration (as the Test tab loads it today, `get_agent_config`) is the ceiling:

| Part of the request | Rule with `agent_id` |
|---|---|
| `tools` | a subset of the agent's tools that are on; a tool not on for the agent is refused ("{tool} is not one of this agent's tools.") |
| `expert_ids` | a subset of the agent's experts ("This expert is not available: {id}.") |
| `subagents` | true only if the agent allows subagents ("This agent does not allow subagents.") |
| `resources` | per-run agents: a subset of the saved selection (`run_resources.resolve`, with its messages); fixed agents: none (`resolve`'s FIXED message) |
| `model_id`, `prompt_id`, `system_prompt` | left out, or equal to the agent's own; otherwise "A saved agent uses its own model and prompt." |
| `message`, `context` | as for ad-hoc runs; `context` is added under `# Context` (facts only) |
| `started_from` | optional with `agent_id` |

- One function, `narrow(conn, project, config, request)`, builds the pinned configuration; start and preview share it.
  Unmet tool needs after narrowing are refused as for ad-hoc runs ("ssh_run needs at least one server.").
- A start with `agent_id` records purpose `test` (`started_from` absent) and the audit action `agent_run.created` with
  `agent_id`, the tools switched off, the experts and the resources.
- **Options for a saved agent:** `GET /api/sessions/options?agent_id=…` returns only what may be chosen: the agent's
  model and prompt as read-only facts, its tools that are on, its experts, whether it allows subagents, its revision,
  and for a per-run agent its saved resources with usable flags (a fixed agent: none, and `resources_fixed: true`).
- **List:** `GET /api/sessions?agent_id=…` lists the agent's Test and event runs, newest first.
- **Review:** tuning suggestions move to `POST /api/sessions/{id}/review` (`{comment, turn?}`), for runs of a saved agent
  (purpose `test` or `event`), with today's behaviour and messages.
- **Removed:** `POST /api/agents/{id}/sessions`, `GET /api/agents/{id}/sessions`, `POST /api/agents/{id}/sessions/{sid}/review`.
- **Event handlers** keep the internal start function until part 5; it is rebuilt on `narrow` with nothing narrowed, so
  there is one way to pin a saved agent.

## UI

**One chat component, `AgentChat`** (today's `AgentPane`, generalised), with a scope: a document or a saved agent.
Shared: the conversation, **Settings** (the sheet), **New chat**, **Earlier runs** (replaces the Test tab's History),
Close, the first message starting a run, one open conversation at a time (the latest open run).

| | Document (Ask agent) | Saved agent (Test tab) |
|---|---|---|
| Where | the pane beside the document | the Test tab, full width |
| Start | ad-hoc, the document editable | `agent_id`, narrowed |
| Extras | the draft bar (Compare, Promote) | **Add feedback** under each answer, the **Suggestions** panel and **Review and apply** (today's `SuggestionApply`) |
| Cannot start because | unsaved document text (asks first) | the agent is disabled, its configuration is loading, or the configuration tabs have unsaved changes (today's messages) |
| The run in the URL | `/documents/:id/agent/:runId` | `/agents/:id?view=test&session_id=…` (as today) |

**Settings sheet for a saved agent:** Model and Prompt as read-only text ("Change them in General."); Tools: the agent's
tools that are on, untick to switch off for this run; Experts: a subset; Allow subagents only when the agent allows it;
Resources: a per-run agent's selection to choose from (today's picker moves here), a fixed agent says "This agent's
resources are fixed."; Preview shows the exact configuration (replaces Show config). While a run is open the sheet is
read-only, with Start new chat with these settings.

**Remembered per agent** in this browser (`agent-settings:agent:{id}`, guarded); dropped when the agent's revision
changes. Ask agent's `ask-agent-settings:*` keys keep working.

**Removed:** `lib/testSessions.ts`; the Test tab's own resource picker, History and Show config panels; the review
call moves to `lib/sessions.ts` as `reviewSession(id, comment, turn?)`.

## Existing runs and data

No migration: Test and event runs already pin the agent's full configuration; narrowing switches entries off in the
pinned copy. Open Test runs keep working; earlier ones are listed. The old audit action `agent.test_session.created`
stays readable.

## Edge cases

- The agent changes during a run: the run keeps its pinned configuration; the sheet shows the run's settings.
- The agent is disabled or deleted: a new start is refused with today's messages; earlier runs stay readable.
- Narrowing removes what a tool needs: refused as for ad-hoc runs.
- Saved settings name a tool, expert or resource the agent no longer has: dropped (and all of them when the revision
  changed).

## Tests

- Backend: `narrow` within the ceiling, and each refusal above it (an added tool, a foreign expert, subagents not
  allowed, a resource outside a per-run agent's selection, any resource for a fixed agent, another model or prompt);
  preview equals start; options for an agent (fixed and per-run); the list by `agent_id`; review at the new route; the
  old routes answer 404; event handlers still start runs; the existing Test-session tests move to the new routes.
- UI: the Test tab shows the shared chat; the first message's request body (`agent_id`, tools switched off); the
  sheet's read-only model and prompt, unticking a tool, the fixed-resources line; Add feedback, Suggestions and
  Review and apply; the cannot-start reasons; Earlier runs; settings dropped on a revision change.

## Not in scope

Generate documents (part 3), discovery (part 4), moving event handlers onto the HTTP start (part 5); trying another
prompt in a Test run; narrowing the model.
