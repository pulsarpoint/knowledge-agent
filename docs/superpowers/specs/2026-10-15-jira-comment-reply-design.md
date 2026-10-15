# Event handler answers as Jira comments

Date: 2026-10-15. Status: for review.
Builds on: event handlers (0089), agent runs for events and notifications (0090, `sessions/text.py`
`notify_event_run`), Jira pull events (0088, `jira/events.py`).

## Why

A Send-to-agent handler's answer lands only in Administration → Notifications. When the event came from a Jira ticket,
the people working the ticket should see the answer on the ticket. This is the "delivery to Jira" item of the events
roadmap.

Decided with the person using it:

1. The handler decides: a new option on Send to agent posts the answer as a comment on the ticket. It acts only on
   events that came from Jira.
2. The notification is always written, as today; it stays the record of every event run and shows whether the comment
   was posted.
3. Delivery state lives on the notification (no generic outbox table until a second channel exists).
4. The comment is posted after the run's transaction commits, by a retrying loop, never inside the transaction.

## Handler option

Migration 0109:

```sql
ALTER TABLE event_handlers ADD COLUMN reply_to_jira boolean NOT NULL DEFAULT false;
ALTER TABLE event_handlers ADD CONSTRAINT event_handlers_reply_to_jira CHECK (NOT reply_to_jira OR action = 'agent');
```

- `handlers_api.py`: `HandlerDraft.reply_to_jira: bool = False`; saving `reply_to_jira` with action `emit` is refused
  with 400 `Only Send to agent handlers can post to Jira.` (the CHECK is the backstop). The list and detail return it.
- The type pattern is not restricted: the option applies to an event whose `source = 'jira'` and does nothing for other
  events, so a handler for `*` keeps working.
- Handler form (`HandlerSheet.tsx`), under Send to agent: a checkbox *Also post the answer as a comment on the Jira
  ticket (Jira events only)*.

## The notification marks the delivery

Migration 0109 adds to `ka.notifications`:

| column | type | meaning |
|---|---|---|
| `jira_status` | text, CHECK in (`pending`,`posted`,`failed`), null | null: no comment for this notification |
| `jira_connection_id` | uuid | the connection that recorded the event (`events.source_id`) |
| `jira_issue_id` | text | `events.payload->>'issue_id'` (the id, not the key: a key changes when the ticket moves) |
| `jira_issue_key` | text | `events.subject`, for the link |
| `jira_comment_id` | text | set when posted |
| `jira_comment_created` | timestamptz | the comment's `created`, set when posted (the loop guard reads it) |
| `jira_attempts` | integer NOT NULL DEFAULT 0 | |
| `jira_next_attempt_at` | timestamptz | when the loop may try next; null unless pending |
| `jira_error` | text, ≤ 2000 characters | why the last attempt failed |

Constraints: `(jira_status = 'pending') IS TRUE = (jira_next_attempt_at IS NOT NULL)`,
`(jira_status = 'posted') IS TRUE = (jira_comment_id IS NOT NULL)`, and a `pending` or `posted` row has
`jira_connection_id` and `jira_issue_id` (a `failed` row from a simulated event has neither). Index for the loop:
`notifications_jira_due ON notifications(jira_next_attempt_at, id) WHERE jira_status = 'pending'`. Down drops the
columns, the index and the handler column.

`notify_event_run` (written in the transaction that ends the run's first turn) joins the session's handler and event
and sets, in the same INSERT:

- the run succeeded, `reply_to_jira`, `events.source = 'jira'` and `source_id` not null → `pending`, connection, issue
  id and key, `jira_next_attempt_at = now()`;
- the same but `source_id` is null (a simulated event from Events → New event) → `failed`,
  `jira_error = 'This event did not come from a Jira connection.'`;
- otherwise (the run failed, the option is off, the event is not from Jira, the handler or event was deleted) → null.

The handler's option is read when the notification is written, so editing the handler while a run is going applies.

## Delivery loop

New module `events/jira_replies.py`, a thread in repository-worker started next to the event processor
(`code_projects/worker.py`), polling every 5 s:

1. Claim one due row: `SELECT … FROM ka.notifications WHERE jira_status='pending' AND jira_next_attempt_at <= now()
   ORDER BY jira_next_attempt_at, id LIMIT 1 FOR UPDATE SKIP LOCKED`, joined to the agent's name.
2. Load the project's Jira connection (`jira_sync.credentials`). When it is missing or its id is not
   `jira_connection_id` (deleted or replaced), fail at once: `The Jira connection that recorded this event no longer
   exists.`
3. `JiraClient.add_comment(issue_id, body)`: POST `issue/{issue_id}/comment` with `{"body": body}`; returns the
   comment's `id` and `created`.
4. Success: `jira_status='posted'`, comment id and created, `jira_next_attempt_at=NULL`, `jira_error=NULL`.
5. Failure (HTTP error, timeout, refused): `jira_attempts += 1`, `jira_error` = the message. After attempt 1 retry in
   1 minute, after attempt 2 in 5 minutes; attempt 3 failing sets `failed`, `jira_next_attempt_at=NULL`. A 4xx other than
   429 (no permission, issue gone) fails at once.

The HTTP call is made while the row is locked in an open transaction (one row at a time; bounded by the client's
timeout), so two workers never post the same comment.

**Body:** the notification's body, then a blank line and `— <agent name> (Knowledge Agent)`. Posted as text: Jira Data
Center renders wiki markup, and Markdown is not converted. A body longer than 32,000 characters is cut and ends with
`… (truncated; the full answer is in Knowledge Agent)`.

**`JiraClient`** gains `post(path, json)` with the same bounds and error handling as `get` (timeouts, response size,
JSON check, redacted errors) and `add_comment(issue_id, body) -> dict` (`id`, `created`). It is the first write to
Jira: the connection's personal access token needs the Add Comments permission on the project; `docs/jira.md` says so.

## Loop guard

Posting a comment moves the ticket's `updated`; the next scheduled pull would record `jira.ticket.updated` and the
same handler would answer its own comment again.

`jira/events.record` skips an update (records no event) when both hold:

- `changes` is empty (no compared field, label or description changed), and
- a notification of this project has `jira_status='posted'`, the same connection and issue id, a
  `jira_comment_created` later than the previous recorded state's `updated`, and the ticket's new `updated` is at most
  2 seconds after that `jira_comment_created`.

The next real change is compared with the last recorded event, as today. A person commenting within 2 seconds of the
agent's comment, with no other change, is not recorded: accepted.

## Notifications view

The list and detail (`notifications_api.py`) return the `jira_*` fields. `NotificationsView.tsx` shows, for a
notification with a Jira status:

- `posted`: *Posted to KEY* linking to `<base_url>/browse/<KEY>?focusedCommentId=<id>`;
- `pending`: *Posting to KEY…* with the attempt count after a failure;
- `failed`: *Not posted to KEY: <error>*.

## Error handling

- Saving: `reply_to_jira` on an emit handler → 400.
- Run time: any delivery failure stays on the notification; the event, handler run and agent run are unaffected.
- The loop survives a lost database (logs and retries, like the event processor) and never stops on one bad row: an
  unexpected error marks that row failed with `Posting to Jira failed unexpectedly. See the repository-worker log.`

## Testing

- Migration 0109 up/down; the CHECKs (emit with `reply_to_jira`; pending without next attempt; posted without id).
- Handler API: create/edit with `reply_to_jira`; refused on emit.
- `notify_event_run`: pending for a succeeded Jira run with the option; failed for a simulated Jira event; null for a
  failed run, the option off, and a manual event.
- Delivery loop with an `httpx.MockTransport`: posted (id and created stored, request body with footer); truncation at
  32,000; retry after 1 and 5 minutes, then failed; 403 fails at once; missing or replaced connection fails at once;
  two claimers never take the same row.
- Loop guard: an update with no changes right after our comment records nothing; the same with a field change records
  the event; a comment-only update with no comment of ours records the event as today.
- UI: the handler form's checkbox is saved; the notifications row shows each state.
- Full backend and admin-ui suites.

## Docs

`docs/jira.md` (the comment, the PAT permission, the loop guard), `docs/user-guide.md` (handler option, notification
states), `docs/migrations.md` (0109).

## Out of scope

- Retrying a failed post by hand.
- Converting Markdown to Jira wiki markup.
- Slack, email or other channels; a generic deliveries table.
- Comments for failed runs.
