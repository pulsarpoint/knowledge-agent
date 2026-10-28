# Jira panel and isolated local testing

The standalone admin has two pages:

- [Jira Settings](http://localhost:8183/jira/settings): save the server base URL,
  Jira login username and personal access token; run **Test connection**.
- [Jira Tickets](http://localhost:8183/jira/tickets): choose 1–200 tickets and
  select **Fetch tickets**. The default is 25, ordered by updated time descending.
  Add JQL to filter at Jira, then use text, project and status filters on the saved
  list. Tickets stay in the database: the pull, the saved list, events and replies use them there.

This adapter targets Jira Data Center. PATs use `Authorization: Bearer`, rather
than username/password Basic auth. The saved username is checked against
`/rest/api/2/myself`; the test also checks `/rest/api/2/search` access. See
[Atlassian PAT documentation](https://confluence.atlassian.com/enterprise/using-personal-access-tokens-1026032365.html),
[current-user REST reference](https://developer.atlassian.com/server/jira/platform/rest/v11002/api-group-myself/),
and [search REST reference](https://developer.atlassian.com/server/jira/platform/rest/v11002/api-group-search/).

Include any context path in the base URL, for example `https://jira.internal/jira`.
The API runs in Docker: use `http://host.docker.internal:8080/jira` for a Jira
installation on the Docker host, or its reachable DNS name/IP for another server.
TLS certificates are verified; requests do not follow redirects or forward tokens
to redirected hosts. The token is encrypted with the existing credential key and
is not returned by settings, ticket responses, validation errors, or audit events.
Leaving the token input blank preserves it; the explicit removal checkbox deletes
it. A URL or username change requires re-entering the token.

## Local mock

`make up` builds and starts the mock with the rest of the stack (and `make down` stops it). To rebuild or restart
only the mock:

```sh
make jira-mock-up
```

Use these intentionally non-secret test values in Jira Settings:

| Field | Value |
| --- | --- |
| Server URL | `http://jira-mock:8080/jira` |
| Username | `mock-admin` |
| Token | `jira-mock-token` |

Save, test the connection, then fetch tickets. The mock has 75 synthetic `DEMO-*`
tickets and clamps every search page to 10 results, exercising pagination. Fetch
25 to cross multiple pages, 5 to replace the visible selection, or 100 to verify
the list stops at the 75 available tickets.

For error testing, save a token of `mock-forbidden` (403), `mock-rate-limited`
(429), or any other value (401), then test the connection. Restore
`jira-mock-token` afterward. The mock is an API fixture, not a Jira web application;
its issue links do not lead to real Jira issue pages. No Jira license or external
Atlassian account is needed to run this fixture.

```sh
make jira-mock-down
```

The `jira-mock` Compose profile (started by `make up`) has no forwarding code and makes no outgoing
connections. Only the admin API (manual fetch) and the repository worker (scheduled auto pull) share its internal
`jira-test` network. Its control page is published on `127.0.0.1` only (port `JIRA_MOCK_PORT`, default 8184, through
its own `jira-mock-page` network).
The mock runs with a read-only filesystem as a non-root user; its state (edits, new tickets, comments) is saved in
the `jira-mock-data` volume and survives a restart or rebuild. Native API tests use
an HTTP mock transport and a disposable PostgreSQL database, separate from the
running application database.

## Mock control page

Open `http://127.0.0.1:8184/` (or your `JIRA_MOCK_PORT`). It has no login; only this machine can reach it.

- **Agent comments**: every comment posted through the API (by Knowledge Agent), newest first, with its ticket, time,
  id and full text. The same list as JSON: `GET /mock/comments`.
- **Ticket page** (`/mock/DEMO-3`): fields and comments, the agent's highlighted. **Edit** the summary, description,
  status or **assignee** (one of the mock users, or unassigned), or **add a comment** as *Jira User* (the default) or
  as one of the mock users. Both move the ticket's updated time, and a search `ORDER BY updated DESC` lists it first.
- **Users**: `mock-admin` (Mock Administrator, the connection's user), `ops-bot` (Ops Bot), `docs-bot` (Docs Bot),
  `ana` (Ana Petrović) and `marko` (Marko Ilić). DEMO-4, DEMO-8, DEMO-12 and DEMO-16 are assigned to `ops-bot`,
  `docs-bot`, `ana` and `marko`; the other tickets whose number is divisible by 4 are unassigned, the rest belong to
  `mock-admin`. `GET /rest/api/2/user/search?username=<text>` finds users by key, name or display name.
- **New ticket**: a summary, a description and an **assignee** (one of the mock users, or unassigned — choose `ops-bot`
  or `docs-bot` to hand it straight to a mapped agent); the next pull records it as `jira.ticket.created`. The ticket
  list shows each ticket's assignee.
- **Comment posting**: for the next 1-10 comment posts, *Fail with 500* (the worker retries after 1 and 5 minutes),
  *Refuse with 403* (fails at once) or *Save the comment but answer 500* (the retry must find the comment on the ticket
  instead of posting it again). It returns to normal afterwards.
- **Reset**: back to the 75 original tickets (`POST /mock/reset`). `GET /mock/state` returns everything as JSON.

Like Jira, a posted comment sets the ticket's updated time to the comment's creation time, so the loop guard below
is exercised for real. The notification's **Posted to KEY** link points at `http://jira-mock:8080/...`, which a browser
cannot open; see the comment on the control page.

### Testing answers as comments

1. `make up` (or `make jira-mock-up` for the mock alone), then save the mock connection in Jira Settings (above) and fetch tickets once.
2. Create a Jira auto pull schedule (Administration → Schedules → New schedule → Jira tickets, with its own JQL and
   number of tickets if needed); its first run records `jira.ticket.created` for the
   tickets it pulls, before any handler exists.
3. Add a Send to agent handler for `jira.ticket.updated` with **Also post the answer as a comment on the Jira
   ticket**.
4. On the control page, edit DEMO-3 or add a comment as a person, then run the schedule (or wait for it).
5. The agent's answer appears under **Agent comments** and on DEMO-3, and Administration → Notifications shows
   **Posted to DEMO-3**. The next pull records no new event for DEMO-3: the only change is our own comment.

## Persistence and scope

One `primary` Jira connection is managed for the single workspace. Settings use
optimistic revisions. The URL and encrypted token are read together under a shared
row lock, so concurrent edits cannot pair one server with another server's token.
No database lock is held during Jira HTTP requests.

The fetched selection is stored in `ka.jira_tickets`, upserted by connection and
stable remote issue ID. Ticket IDs survive repeated fetches. Only after all pages
validate and the transaction succeeds is the visible list replaced; a failed or
stale fetch preserves the previous selection. Older cached records are marked
outside the current selection, not treated as remotely deleted. Changing URL or
username clears the old cache; replacing/removing the token hides the old
selection until another successful fetch. These rules prevent results from one
server or credential scope being presented as results from another.

Reads return the saved snapshot and its fetch timestamp, requested count and
total matching the successful JQL query and visible to the Jira user. Fetches are bounded to 200 tickets, 4 MiB per page,
5-second connect/8-second I/O timeouts and a 30-second operation deadline checked
between requests and received chunks. Errors and redirects are returned as safe
operator messages; rate limiting requires a later retry.

All panel routes require the admin gateway and the `docs-admins` group. Ticket
access follows admin access; Jira issue and comment restrictions are not copied.
Jira issues are never created or modified (posting an agent's answer as a comment is the only write, below). Scheduled background sync, attachment
downloads and Cloud authentication remain future work.

| Admin API | Behavior |
| --- | --- |
| `GET /api/jira/settings` | Public configuration and token-presence flag |
| `PUT /api/jira/settings` | Save URL, username and optional write-only token; current revision required on edits |
| `POST /api/jira/test` | Check token owner and issue-search access; save the test result |
| `GET /api/jira/tickets` | Read the last successful local selection; the response is `{connection, tickets}` |
| `POST /api/jira/tickets/fetch` | Fetch and save matching tickets using `{ "limit": 25, "jql": "project = DEMO ORDER BY updated DESC" }`; returns the same `{connection, tickets}` |

## Mock filtering and comments

The mock supports equality for `project`, `status`, `assignee` (by user key), `key` and `labels`, `assignee is EMPTY`,
`text ~ "words"`, and clauses joined with `AND`. Values may be double-quoted.
Optional sorting is `ORDER BY updated DESC` or `ORDER BY updated DESC, key DESC`.
Other JQL syntax is deliberately rejected; the real adapter forwards full JQL to
Jira without applying this mock grammar.

Try `project = DEMO AND status = "Open" ORDER BY updated DESC` (25 matches) or
`key = DEMO-1`. Every mock issue has five comments, with comment pages capped at
two results. The fifth comment contains **retry jitter resolved the payment
timeout**, making it easy to verify that every comment page was read.
Rebuild the fixture after updating its code:

```sh
docker compose --env-file .env -f deploy/compose.yaml --profile jira-mock up -d --build jira-mock
```

## Answers as comments

A Send to agent handler with **Also post the answer as a comment on the Jira ticket** posts the agent's answer on the
ticket of a Jira event (events recorded by a pull, manual or scheduled; simulated events from Events → New event cannot be answered).
The answer is posted as text (Jira renders wiki markup; Markdown is not converted) with the footer
`— <agent name> (Knowledge Agent)`, cut to 32,000 characters. A run that fails (after its automatic retries of model
connection errors, timeouts, rate limits and server errors: 30 seconds, 1 minute, 2 minutes) posts `The agent could not
answer: <short reason>. Reassign the ticket or ask in Knowledge Agent to try again.` instead, once, through the same queue; a hand-back
follows it as it follows an answer. A stopped or closed run, and an empty answer, post nothing. A person's **Retry** of
a failed run posts its answer as a normal reply.

The comment is posted by repository-worker after the run's first turn ends for good, as the user of the Jira connection's
personal access token, which needs the **Add Comments** permission in the ticket's project. A failed post is tried
again after 1 and 5 minutes; Jira refusing it (no permission, issue gone) or a deleted or replaced connection fails
at once. The notification shows **Posted to KEY** (a link to the comment), **Posting to KEY…** or **Not posted to KEY**
with the reason.

A retry first looks for the comment on the ticket (an earlier attempt may have reached Jira without us recording it)
and records the one it finds instead of posting again, so a reply is posted once. A comment Jira took on the third
(last) failed attempt can still appear once on the ticket while the notification says **Not posted**.

Posting a comment moves the ticket's updated time. The pull takes each ticket's comments with the search (`fields`
includes `comment`; when Jira sends only some, the rest come from the ticket's comment endpoint), in pages of 20
tickets, and every Jira event carries all of them (`payload.comments`, `payload.comment_count`) and the ids of the new
ones (`payload.changes.comments.added`). The next pull records no `jira.ticket.updated` for a ticket whose
only change is new comments that are all our own answers: a comment whose id a posted reply recorded, or, while a reply
is still being posted, a comment by the connection's Jira user with exactly the text being posted. Any other new
comment — a person's, even in the same second as ours — is recorded, together with ours.

### Giving the ticket back

A handler with **Agent mapped to the assignee** and **Also post the answer as a comment on the Jira ticket** can also
**Give the ticket back after replying** (`give_back`; the API refuses it without `reply_to_jira` or with a fixed
agent). Who gets the ticket is decided when the run's notification is written, from the event: the previous assignee
(`payload.changes.assignee.from_id`) when the ticket was reassigned to the agent's user, otherwise the reporter
(`payload.reporter_id`) — never a Jira user mapped to an agent (two bots would pass the ticket back and forth); when
neither is left, the ticket stays with the agent's user. The comment then ends with `Assigned back to <display
name>.` above the agent footer.

The ticket is assigned only after the comment is posted: a failed comment keeps the ticket with the agent's user.
Before posting, the worker reads the ticket's assignee: when someone else took the ticket meanwhile, the comment has
no note and the hand-back is skipped (`Not assigned back: the ticket was reassigned before the reply.`). Otherwise it
looks up the person's login (`GET /rest/api/2/user?key=<key>`) and assigns the ticket (`PUT
/rest/api/2/issue/<key>/assignee` with `{"name": <login>}`), as the connection's user, which needs the **Assign
Issues** permission. A failed assignment is retried on the comment's schedule (after 1 and 5 minutes) without posting
the comment again. The notification shows the hand-back next to the comment's state: **Assigning back to <name>…**,
**Assigned back to <name>**, **Not assigned back to <name>** with the reason, or why it was skipped. The reassignment
comes back on the next pull as an ordinary `jira.ticket.updated`; the new assignee is not mapped, so no run starts.

Every Jira event carries the assignee's Jira key next to the display name (`payload.assignee_id`, null when
unassigned); a reassignment shows both (`payload.changes.assignee` = `{from, to, from_id, to_id}`).
`payload.for_assignee` is true when the event is work for the ticket's assignee: the ticket is assigned and it is new,
its assignee changed (by key), or a comment was added that is neither one of our own answers (as above) nor written
by the connection's own Jira user. Status, label, description and other edits leave it false. Matching uses the Data
Center user `key`: an assignee without one (Jira Cloud's `accountId`) leaves `for_assignee` false.

## Users and agents

Jira → Settings → **Users and agents** maps Jira users to agents for the saved connection: a ticket assigned to a
mapped user is that agent's work. Type in **Jira user** to search (Jira's user search, `GET
/rest/api/2/user/search?username=`, merged with the assignees already seen on pulled tickets; the token goes only to
the configured Jira, and only key, login name and display name come back), pick a user or keep a typed user key,
choose an **Agent** and **Add**. Each row shows the display name and key, the agent (change it in place), **Agent
disabled** when the agent is turned off, and a remove button. A Jira user maps to one agent; an agent may have several
users. Mapping and matching use the stable user `key`; the display name is only shown. Deleting an agent or the Jira
connection removes its rows. The API: `GET /api/jira/user-agents`, `PUT /api/jira/user-agents/{key}` (`{agent_id,
name}`), `DELETE /api/jira/user-agents/{key}`, `GET /api/jira/users?query=` (`{users: [{key, name, display_name}],
warning}`; a failed Jira search still returns the seen assignees, with a warning).

Saving the first mapping, when the project has no handler sending to **Agent mapped to the assignee**, also creates
one (audited as `event_handler.created` with `automatic: true`): **Jira assignee agents**, enabled, event type
`jira.ticket.*`, the condition `payload.for_assignee equals true`, a prompt with the ticket's key, summary, description,
status, reporter and comments, **Also post the answer as a comment on the Jira ticket** and **Give the ticket back
after replying**. It is an ordinary handler (edit, turn off or delete it in Events → Handlers); later mappings reuse it
and removing mappings never removes it. The section shows `Handled by: <handler> (enabled|disabled)` with a link that
opens the handler, or `No handler runs these agents.` with **Create handler** (the same handler; `POST
/api/jira/user-agents/handler`, 409 when an assignee handler exists or a handler already has the name).
`GET /api/jira/user-agents` and `PUT` return `{user_agents, handler: {id, name, enabled} | null}`.

An event handler's **Agent** can be **Agent mapped to the assignee** (`agent_from: "assignee"`, no `agent_id`; Send
to agent handlers for Jira event types only). For each matching event it runs the agent mapped to the ticket's current
assignee (`payload.assignee_id`) on the event's connection, exactly like a fixed-agent run (the agent's whole access,
the prompt, **Also post the answer as a comment on the Jira ticket**). An unassigned ticket or one assigned to an
unmapped user is not for the handler: no run and nothing recorded. A disabled mapped agent fails the handler run with
`Agent is disabled.`. Add the condition `payload.for_assignee` **equals** `true` (the form offers it in one click) so
the agent starts only when the assignee has something new, not on every edit. **Preview** says which agent would run
(`Runs <agent>`) or why none would (`No run: The ticket is not assigned.` / `No run: No agent is mapped to Jira user
<display name> (<key>).`).

### Testing with the mock

1. In Jira → Settings, save the mock connection and let the Jira auto pull record the tickets once (the seeded DEMO-4
   is already assigned to `ops-bot`, so a handler that exists at the first pull answers on DEMO-4 too).
2. Under **Users and agents** add `ops-bot` → an agent: this creates the **Jira assignee agents** handler
   (`Handled by: Jira assignee agents (enabled)`).
3. On the control page set DEMO-3's assignee to Ops Bot and run the schedule: the mapped agent answers on DEMO-3 with
   `Assigned back to <previous assignee>.` and the ticket goes back to that person (to the reporter for a ticket
   created assigned to Ops Bot). A comment as Jira User on a ticket still with Ops Bot starts another run; the agent's
   own reply and the hand-back do not.
