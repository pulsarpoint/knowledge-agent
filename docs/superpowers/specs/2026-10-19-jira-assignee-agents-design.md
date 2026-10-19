# Jira assignee → agent

Date: 2026-10-19. Status: agreed in conversation (2026-10-19), for review.

## Why

Event handlers can already match Jira events and start an agent, but a handler names one fixed agent and can only
match the assignee by display name (not unique, can change). There is no way to say "tickets assigned to this Jira
user are this agent's work".

## Decisions (user, 2026-10-19)

- **A mapped Jira user is the agent's inbox.** A Jira account (for example `ops-bot`) stands for an agent; assigning a
  ticket to that user hands the work to the agent, whose answer is posted back as a comment.
- **On assignment.** The agent starts when a ticket is created assigned to the user or reassigned to them, and when a
  person comments on a ticket while it is assigned to them. Other edits do not restart it.
- **Handlers stay the one routing mechanism.** A handler's target is a fixed agent or "the agent mapped to the
  assignee".
- **Testing uses the Jira mock only** (extended); no real Jira.

## Jira users → agents

- Table `ka.jira_user_agents`: `connection_id` (→ `ka.jira_connections`, cascade), `jira_user_key` (the stable Jira
  `key`), `jira_user_name` (display name, shown only), `agent_id` (→ `ka.agents`, cascade), audit columns;
  `UNIQUE (connection_id, jira_user_key)`. One Jira user maps to one agent; an agent may have several Jira users.
- API under the Jira connection: list, put (create or change a row), delete; a user search
  `GET …/users?query=` that returns `{key, name, display_name}` from Jira `/rest/api/2/user/search?username=<query>`
  (Data Center) merged with assignees already seen on pulled tickets (`ka.jira_tickets.assignee_id/assignee_name`).
- UI: a **Users and agents** section on the Jira settings page — rows "Jira user → agent", add with a user picker (search
  as you type; a typed key is accepted), change agent, remove. A row whose agent is disabled says so.

## Jira events

- The payload gains `assignee_id` (the Jira key; null when unassigned) next to `assignee` (display name).
  `changes.assignee` becomes `{from, to, from_id, to_id}`.
- New derived flag `payload.for_assignee` (bool), computed by a pure function from the ticket and its changes. True
  when the ticket is assigned and:
  - it is created (first seen) assigned, or
  - its assignee changed, or
  - a comment was added by someone other than the connection's own user (our posted replies never count — the existing
    loop protection decides which comments are ours).
  False otherwise (status, labels, description and other edits).
- Event types stay `jira.ticket.created` / `jira.ticket.updated`.

## Handlers

- New handler target: `agent_from` = `fixed` (today, uses `agent_id`) or `assignee` (`agent_id` null). Only for the
  `agent` action and only for handlers whose type pattern can match Jira events.
- Processing (`events/processor.py`): for `assignee`, look up `ka.jira_user_agents` by the event's connection
  (`source_id`) and `payload.assignee_id`. No mapping, no assignee, or a disabled agent → no run; the event records the
  reason (`No agent is mapped to Jira user <name> (<key>).` / `The ticket is not assigned.` / the existing disabled-agent
  text). The run is started exactly as a fixed-agent run (the agent's whole access, the rendered prompt, reply to Jira).
- Preview explains the target the same way (which agent would run, or why none).
- UI (`HandlerSheet.tsx`): the agent field offers the agents plus **Agent mapped to the assignee**; `payload.for_assignee`
  and `payload.assignee_id` join the field suggestions.
- The decision "which agent for this event" is a pure function (mapping rows + event → agent id or refusal text),
  tested in isolation.

## Unchanged

- Replies are posted as the connection's Jira user with the agent-name footer (per-agent Jira accounts: later).
- Every event starts a new run; the payload carries the ticket's comments, so the agent sees the conversation.

## Jira mock (deploy/jira-mock/server.py)

- Seeded users: `mock-admin` (the connection's user), `ops-bot`, `docs-bot`, `ana`, `marko`; some seeded tickets
  assigned to the bots and the people.
- `GET /rest/api/2/user/search?username=` (matches key, name or display name, case-insensitive).
- Control page: assignee select on the edit form (including unassigned), "comment as" select on add comment.
- JQL `assignee = <key>` keeps working; `assignee is EMPTY` if simple.

## Out of scope

- A real Jira in Docker (decided: mock only).
- Continuing an existing run when a person comments (each event starts a new run).
- Posting as per-agent Jira users.

## Testing

- Pure: `for_assignee` for created-assigned, created-unassigned, reassigned, unassigned-now, person comment, own reply,
  status-only change; the target choice for mapped, unmapped, unassigned, disabled agent.
- API: mapping CRUD (unique per connection, cascade on agent delete), user search merging Jira and seen assignees.
- Handler: `agent_from='assignee'` validation, preview text, processor outcome with and without a mapping.
- End to end with the mock: reassign a ticket to `ops-bot` → pull → event with `for_assignee` → the mapped agent's run →
  reply posted on the mock ticket; a person's comment starts another run; our reply does not.
- UI: Users and agents section (add, change, remove), handler target option.
