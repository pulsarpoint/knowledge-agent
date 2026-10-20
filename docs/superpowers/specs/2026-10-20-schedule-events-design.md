# Schedules that start agents (through events)

Date: 2026-10-20. Status: agreed in conversation (2026-10-20), for review.

## Why

The only schedule target is the Jira pull. People want an agent to run at set times with a specific task — a morning
summary, a nightly check. The events roadmap (2026-10-09) says new timed work is a schedule target and decisions live in
handlers; the Jira work kept handlers as the one thing that starts agents from outside a chat.

## Decision (user, 2026-10-20)

A schedule can **emit an event**; an event handler sends it to an agent with a prompt. The Schedules page makes the
pair quick to set up (**Add handler**) and shows which handler runs a schedule. Answers go to Notifications (as event
runs today) and the run shows in Agent runs.

## Emit event target

- New schedule target `event.emit` (label **Emit event**). `target_id` is the schedule's own id (each emit schedule is
  its own target; the `UNIQUE (project_id, target_kind, target_id)` rule still holds). Migration widens the
  `target_kind` CHECK.
- Each firing (scheduled or Run now) records one event:
  - `source = 'schedule'`, `source_id = <schedule id>`, `type = 'schedule.fired'`, `subject = <schedule name>`,
    `title = <schedule name>`;
  - payload `{schedule_id, name, fired_at, last_run_at, trigger}` (`last_run_at` = the previous successful firing or
    null; `trigger` = `scheduled` or `manual`);
  - `dedupe_key = schedule:<schedule id>:<fired_at>` so a retried firing never records twice;
  - the schedule run's message: `Recorded event <id>.`
- The event goes through the event processor like every event (status, handlers, history in Administration → Events).
- Nothing else on the schedule (no agent, no prompt): those live on the handler.

## Handlers for a schedule

- The handler sheet's event type field offers **Schedule: <name>** for each emit schedule; choosing it sets type
  pattern `schedule.fired` and the condition `payload.schedule_id equals <schedule id>` (shown and editable like any
  condition). Prompt variables: the usual `event.*` (`event.payload.fired_at`, `event.payload.last_run_at`, …).
- Agent target: a fixed agent (the assignee target is Jira-only). Reply in Jira and give back stay Jira-only.
- No new handler fields.

## Schedules page

- New schedule → target **Emit event** (name, timing). The list shows the target as `Emit event`.
- For an emit schedule the row shows `Handled by: <handler name>` (linking `?handler=<id>`; several → `Handled by: 3
  handlers`) — a handler counts when its type pattern matches `schedule.fired` and it has the condition
  `payload.schedule_id equals <id>` — or `No handler runs this schedule.` with **Add handler**: opens the handler sheet
  prefilled (name `<schedule name>`, type `schedule.fired`, the condition, action Send to agent; the person picks the
  agent and writes the task).
- Deleting a schedule leaves its handlers (they simply never match again); deleting a handler leaves the schedule (it
  keeps recording events). Both say so in their confirm text.

## Out of scope

- A direct "run an agent" schedule target (rejected: one routing mechanism).
- Delivery beyond Notifications (Slack, email, documents).
- Passing a resource subset per firing (the handler's agent runs with its whole access, as event runs do).

## Testing

- Target: firing records one `schedule.fired` event with the payload, `last_run_at` from the previous firing, Run now →
  `trigger: manual`; a retried firing with the same `fired_at` records nothing new; the schedule run message.
- Processor: a fixed-agent handler with the schedule condition starts one run; another schedule's events do not match.
- API: create/edit/delete an emit schedule; target_id = own id; the CHECK; handled-by lookup (none, one, several).
- UI: New schedule with Emit event; Handled by / Add handler (prefilled sheet and saved body); handler sheet Schedule
  picker sets type and condition.
