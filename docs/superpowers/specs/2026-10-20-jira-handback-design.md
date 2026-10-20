# Jira: automatic assignee handler and hand-back

Date: 2026-10-20. Status: agreed in conversation (2026-10-20), for review.

## Why

Mapping a Jira user to an agent does nothing until someone builds the right event handler by hand. And after the agent
answers, the ticket stays assigned to the bot: nobody human owns it, the bot's queue fills with answered tickets, and
there is no signal that a person should look.

## Decisions (user, 2026-10-20)

- **One shared handler, created automatically** with the first mapping of a connection (if it has no assignee handler).
- **Hand back after replying** is the default for that handler: after the agent's comment is posted, the ticket is
  assigned back to whoever gave it to the agent.

## The automatic handler

- When a Jira user mapping is saved and the project has no event handler with `agent_from = 'assignee'`, one is created
  in the same transaction:
  - name `Jira assignee agents`, enabled;
  - type pattern `jira.ticket.*`, condition `payload.for_assignee equals true`;
  - action `agent`, `agent_from = 'assignee'`;
  - `reply_to_jira = true`, `after_reply = 'give_back'`;
  - a default prompt (Jinja) with the ticket key, summary, description, status, reporter and comments, asking the agent
    to work on the ticket and answer as a Jira comment.
  - created by the person saving the mapping; audited as `event_handler.created` with `{automatic: true}`.
- It is an ordinary handler (edit, disable, delete). Later mappings reuse it; nothing overwrites it. Removing mappings
  never removes it.
- The Users and agents section shows `Handled by: Jira assignee agents (enabled|disabled)` with a link, or, when no
  assignee handler exists, `No handler runs these agents.` with a **Create handler** button (the same defaults).

## Hand-back

- New handler setting `after_reply`: `keep` (the ticket stays assigned to the agent's Jira user) or `give_back`. Only for
  handlers with `reply_to_jira`. Existing handlers keep `keep`; the automatic handler and new handlers created in the
  sheet with Reply in Jira on default to `give_back`.
- The hand-back target is decided when the reply notification is created, from the event: the previous assignee
  (`payload.changes.assignee.from_id`) when the ticket was reassigned to the agent's user, otherwise the reporter
  (`payload.reporter_id`, added to the payload next to `reporter`). Stored on the notification (`give_back_key`,
  `give_back_name`); none when neither exists (then the ticket stays).
- The reply worker, after the comment is posted successfully, assigns the ticket: it looks up the user's login with
  `GET /rest/api/2/user?key=<key>` and calls `PUT /rest/api/2/issue/<key>/assignee` with `{"name": <login>}` (Data
  Center). The comment ends with `Assigned back to <name>.` above the agent footer when a hand-back will follow.
- Only after a successful comment. A failed comment keeps the ticket with the agent's user (the failure stays visible).
  A failed assignment is recorded on the notification (`give_back_status` `failed` with the error) and shown with the
  reply status; it is retried with the comment's retry schedule; the comment is never posted twice.
- The reassignment comes back on the next pull as an ordinary `jira.ticket.updated` event; `for_assignee` is true for
  the person, who is not mapped, so no run starts.

## Jira mock

- `GET /rest/api/2/user?key=<key>` and `PUT /rest/api/2/issue/<key>/assignee` (`{"name"}`; `null` name unassigns),
  bumping `updated`.

## Out of scope

- Moving the ticket's status instead of (or besides) reassigning.
- Reading Jira's changelog to find who made the assignment (previous assignee, else reporter, covers the usual cases).

## Testing

- Automatic handler: created with the first mapping (fields, audit), not duplicated by later mappings, not created when
  an assignee handler exists, not removed with mappings; Create handler button path.
- Hand-back target: reassigned to the bot → previous assignee; created assigned → reporter; neither → none.
- Worker: comment then assign (login looked up by key); comment failure → no assignment; assignment failure → recorded
  and retried without a second comment; `keep` → no assignment; the comment note.
- End to end with the mock: map `ops-bot`, create a ticket assigned to Ops Bot by Ana, pull → run → comment → ticket
  assigned to the reporter; next pull records the update and starts nothing.
- UI: handler sheet After replying setting; Users and agents handler line and Create handler.
