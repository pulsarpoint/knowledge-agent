# Jira Tickets From Agent Output — Design

Approved direction: 2026-10-24 (conversation). Builds on structured output (`run.output` events, merged 7d2e289),
"Include the previous output" (30a4860), the Jira assignee → agent mapping (`ka.jira_user_agents`, 0117) and Jira
replies (comments through `jira/client.py`).

## Problem

A checker agent runs on a schedule and finishes with findings (e.g. the built-in "Server health check" schema). Nothing
turns those findings into Jira tickets, and every write to Jira today uses the one integration account, so an agent's
tickets and comments cannot appear as the agent's own Jira user.

## Decisions (from the conversation)

1. **Tickets are created deterministically by a handler action**, never by the agent: the agent reports, a rule a person
   configured decides which findings become tickets, where, and who reviews them.
2. **One ticket per finding**, deduplicated by a fingerprint; repeats stay quiet; a finding whose ticket a person closed
   comes back as a new "Reoccurred" ticket; a mute label silences a fingerprint; a cap per hour stops floods.
3. **Agents act in Jira as their own Jira user** when that user has credentials in the mapping; otherwise the
   integration account writes and the text says which agent reported.
4. Defaults: `worth_checking` findings get tickets at a lower priority; the daily "still present" comment is an option,
   off by default. Automatic resolution (a passing check closes the ticket) comes later.

## 1. Agent identities in Jira (credentials per mapped user)

- `ka.jira_user_agents.credential_id uuid` (nullable, → `ka.credentials`, migration `0139_jira_tickets`): the mapped
  Jira user's personal access token, encrypted like the connection's (`kind 'jira_pat'`, AAD
  `<project>:<credential>:jira_pat:v1`). Never returned by the API (only `has_credentials`), never in Debug, logs or the
  model's context.
- Jira settings, the user → agent mapping row: **Credentials** (token, write-only, "set" when present, Remove) and **Test
  connection** (calls `/rest/api/2/myself` with that token; shows the account it resolves to, and warns when it is not
  the mapped Jira user).
- `jira/client.py` gains a way to act with a given token (a client per identity); a small resolver
  `identity_for(agent_id) -> token | None` picks the mapped user with credentials for that agent (the first one when
  several, by creation).
- Replies and comments from an agent's runs (the existing reply-to-Jira path) use the agent's identity when it has
  one; otherwise the integration account as today, with the line "Reported by agent <name>." appended.

## 2. The "Create Jira tickets" handler action

- A third handler action next to "Start agent" and "Emit event": `action = 'jira_tickets'` with `jira_tickets jsonb`
  (migration 0139), configured in the handler editor:
  - **items**: the path of the array in the output whose elements become tickets (e.g. `findings`); empty: the whole
    output is one item;
  - **item filter**: conditions on item fields (field, op `equals` / `in`, value), e.g. `severity in [issue,
    worth_checking]`;
  - **fingerprint**: fields that identify "the same problem" — item fields or output top-level fields with a `$.` prefix
    (default for the health schema: `$.server`, `service`, `category`, `title`);
  - **ticket**: project key, issue type, priority (a fixed value or a map from an item field, e.g. `severity`: issue →
    High, worth_checking → Medium), assignee (a Jira user from the pulled users), labels (always plus `agent-finding`);
  - **summary** and **description** templates: `{field}` for item fields, `{$.field}` for top-level fields, plus
    `{run_url}`, `{agent}`, `{handler}`; the description always ends with a link to the run;
  - **create as**: the agent of the run (its Jira identity, falling back to the integration account) — or a chosen
    mapped identity, or the integration account;
  - **still present comment**: off / at most once a day;
  - **cap**: new tickets per hour for this handler (default 5).
- It runs on `run.output` events (the handler's event filter narrows to the schema and the source handler as usual) and
  needs no agent and no model.
- Validation when saving: the item path and fields are checked against the output schema the source handler uses, when
  the filter names one (warnings, not errors, when the filter does not pin a schema).

## 3. What happens to each item

- `ka.jira_finding_tickets` (migration 0139): `handler_id`, `fingerprint` (a hash of the fingerprint fields' normalised
  values, plus the readable values), `jira_key`, `jira_issue_id`, `last_severity` (or the priority source value),
  `first_seen_at`, `last_seen_at`, `last_comment_at`, `reoccurs` (the previous ticket key, when this one is a
  reoccurrence), unique on (`handler_id`, `fingerprint`, `jira_key`).
- Before deciding, the action refreshes the state of the tickets it is about to touch from Jira (status category and
  labels) — the pulled tickets may be minutes old.
- Per item that passes the filter:
  - **new fingerprint, or no ticket of this handler for it** → create a ticket;
  - **open ticket** → quiet; comment only when the priority source value goes up (e.g. worth_checking → issue: "Severity
    is now issue: <details>"), or — when the option is on — "Still present (last seen <time>)" at most once a day;
  - **ticket closed (status category done) by a person** → a new ticket, summary prefixed "Reoccurred:", linking the old
    key in its description (and a Jira issue link "relates to" when the project allows it);
  - **ticket has the label `agent-muted`** (on an open or closed ticket) → nothing; `agent-muted-until-YYYY-MM-DD` mutes
    until that date.
- **Cap**: when the handler has created its cap of tickets in the last hour, further new tickets are not created; once
  per hour the action creates a single ticket "Too many findings from <handler>" (to the same assignee) listing the
  skipped fingerprints, then stays silent until the hour passes.
- Items not passing the filter (e.g. `ok` findings) do nothing now; automatic resolution comes later.
- The handler run records its outcome per item: `created OPS-91`, `commented OPS-87 (severity up)`, `quiet OPS-87`,
  `muted OPS-80`, `capped`, `failed: <Jira error>`; the events log shows them. A Jira error on one item does not stop the
  others; a failed create is retried by the next run's output (no queue).

## 4. Jira client and mock

- `jira/client.py`: `create_issue(project, issue_type, summary, description, priority?, assignee?, labels)`,
  `issue_state(keys)` (status category and labels), `link_issues(a, b)` (best effort); comments already exist.
- `deploy/jira-mock/server.py`: `POST /rest/api/2/issue` (creates a ticket in the mock's store, author from the token),
  labels on tickets, status changes on the control page (to close a ticket), per-user tokens (a token → user map the
  control page shows), `GET /rest/api/2/myself` per token.

## Not in scope

- Automatic resolution (comment or close when the check passes); agents creating tickets through a tool; Jira custom
  fields beyond priority, labels and assignee; per-project field mapping screens.

## Testing

- Identities: token stored encrypted, never returned; Test connection; resolver; replies use the agent identity or the
  integration account with the line.
- Action config: validation against the output schema; templates.
- Decisions (pure): new / open quiet / severity up / still-present daily / closed → reoccurred / muted / mute until /
  cap and the summary ticket.
- End to end with the mock: run.output → tickets created as the agent's user → the next output is quiet → close in the
  mock → the next output reoccurs → mute label silences.
- Migration up/down.
