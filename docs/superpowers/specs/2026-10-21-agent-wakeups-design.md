# Agents continue their own work later (wake-ups)

Date: 2026-10-21. Status: agreed in conversation (2026-10-21), for review.

## Why

An agent working on something slow — a deployment, a ticket waiting for a reply, a nightly job — has no way to say
"look again in 30 minutes". Today a session only gets a new turn when a person sends a message.

## Decisions (user, 2026-10-21)

- **A delayed message in the agent's own session**, not a schedule and an event: the agent queues a message for itself
  with a "not before" time; when it is due the session continues with its full context.
- **One-off, chainable:** a wake-up fires once; when woken the agent may set the next one, so it stops by itself.
- **A session with a pending wake-up stays open** (not idle-closed); closing it by hand cancels its wake-ups.
- **Limits:** at most 3 pending wake-ups per session; a delay of 1 minute to 30 days; subagents and experts do not get
  the tools.

## Storage

- `ka.agent_session_inputs` gains `not_before timestamptz` (null = deliver as soon as possible, as today) and
  `kind text NOT NULL DEFAULT 'message' CHECK (kind IN ('message','wakeup'))`. A wake-up row has `kind='wakeup'`,
  `not_before` set, `actor_id` = the session's starting person (inputs require an actor; the run then acts as the person
  who started the session, as every turn of that session does), and `status 'queued'`. Cancelling sets `discarded`.
- Migration **0123** (columns, a CHECK that `kind='wakeup'` requires `not_before`, an index on queued wake-ups by
  `not_before`).

## Tools (every agent run with a session; not subagents, experts or internal calls)

- `schedule_wakeup(message, in_minutes | at)` — exactly one of `in_minutes` (1 … 43200) or `at` (ISO time, 1 minute to
  30 days ahead); `message` 1 … 2000 characters (what the agent wants to be reminded of). Refused with a clear text when
  the session already has 3 pending wake-ups, when the time is out of range, or outside a session. Returns
  `{id, due_at}`.
- `list_wakeups()` — the session's pending wake-ups `{id, due_at, message}`.
- `cancel_wakeup(id)` — cancels a pending wake-up of this session (refused for another session's or a delivered one).
- Each tool has a pure check (`check_<tool>`) like every tool; the access matrix covers them.

## Delivery

- The session worker claims a session for a queued input only when its `not_before` is null or past. A session whose only
  queued inputs are future wake-ups is not claimed.
- A due wake-up is delivered when the session is not working (it starts a new turn); if the session is working, it waits
  for that turn to end (it is never delivered as a mid-turn comment).
- The delivered turn's input reads `Scheduled wake-up (set <set time>): <message>`, so the agent knows why it woke.
- The turn runs exactly like a person's next message: same session, same pinned access, history and model. Event-run
  sessions continue too; their Jira reply/hand-back (first turn only) is not repeated.

## Idle close, Stop and Close

- Idle close (24 h) skips sessions with a pending wake-up.
- **Stop** stops the current turn and leaves pending wake-ups.
- **Close** discards the session's pending wake-ups.

## UI

- The session page shows pending wake-ups above the message box: `Wakes up at <time>: <message>` with **Cancel**.
- The run record shows the wake-up turn like any turn (its input is the wake-up text).

## Out of scope

- Repeating wake-ups (chain instead).
- Wake-ups that start a new session or another agent (that is a schedule + handler).
- Per-agent limits.

## Testing

- Tools: schedule with in_minutes / at, the range and count limits, outside a session, list, cancel (own, other
  session's, delivered); access-matrix rows.
- Worker: a future wake-up is not claimed; a due one starts a turn with the wake-up text; a due one during a working turn
  waits for the turn to end; person messages are unaffected.
- Idle close skips a session with a pending wake-up and closes it once none is pending; Close discards wake-ups; Stop
  keeps them.
- Chaining: a woken turn can schedule the next wake-up.
- UI: pending list and Cancel on the session page.
