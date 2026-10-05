# Jira panel and isolated local testing

The standalone admin has two pages:

- [Jira Settings](http://localhost:8183/jira/settings): save the server base URL,
  Jira login username and personal access token; run **Test connection**.
- [Jira Tickets](http://localhost:8183/jira/tickets): choose 1–200 tickets and
  select **Fetch tickets**. The default is 25, ordered by updated time descending.
  Add JQL to filter at Jira, then use text, project and status filters on the saved
  list. Select tickets and **Export selected** to write Markdown into a RAG collection.

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

From the v3 repository:

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

The optional `jira-mock` Compose profile has no published host port, no external
network connection, and no forwarding code. Only the admin API shares its internal
`jira-test` network. Ordinary `make up` does not start the mock on a clean stack.
The mock runs with a read-only filesystem as a non-root user. Native API tests use
an HTTP mock transport and a disposable PostgreSQL database, separate from the
running application database.

## Persistence and scope

One `primary` Jira connection is managed for the single workspace. Settings use
optimistic revisions. The URL and encrypted token are read together under a shared
row lock, so concurrent edits cannot pair one server with another server's token.
No database lock is held during Jira HTTP requests. Export briefly holds a shared
connection lock while publishing to the local RAG API, preventing a settings change
from racing the final write.

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
access follows admin access. Exported content follows the existing RAG access
policy; Jira issue and comment restrictions are not copied as RAG permissions.
Jira issues are never created or modified. Scheduled background sync, attachment
downloads and Cloud authentication remain future work.

| Admin API | Behavior |
| --- | --- |
| `GET /api/jira/settings` | Public configuration and token-presence flag |
| `PUT /api/jira/settings` | Save URL, username and optional write-only token; current revision required on edits |
| `POST /api/jira/test` | Check token owner and issue-search access; save the test result |
| `GET /api/jira/tickets` | Read the last successful local selection |
| `POST /api/jira/tickets/fetch` | Fetch and save matching tickets using `{ "limit": 25, "jql": "project = DEMO ORDER BY updated DESC" }` |
| `POST /api/jira/tickets/{ticket_id}/export` | Export one saved ticket using `{ "collection": "jira", "connection_revision": 1 }`; returns collection, path, status, hash and comment count |

## Markdown export and RAG processing

1. Fetch a JQL query, optionally narrow the saved list, and select tickets. Changing
   a saved-list filter clears the selection; **Select all filtered tickets** selects
   only the currently displayed rows.
2. Choose a **Target collection** (default `jira`). Only collections whose source
   lives under `rag-data` accept files; create one in **RAG → Collections** if none
   is listed. Select **Export selected** and keep the page open. Each ticket is an
   independent export; successes remain saved if another ticket fails, and failed
   tickets remain selected for retry. Leaving the page stops the remaining requests;
   an in-flight server request may still finish. A collection that is missing
   returns 404, and a read-only one returns 400.
3. Open **RAG → Collections** and **Refresh** the collection. This uses the existing
   incremental processing and embedding pipeline. Exports do not automatically
   refresh the collection or configure an embedding provider.

The API refetches the issue by its stored remote ID and retrieves every page from
`/rest/api/2/issue/{id}/comment`. Only comments visible to the configured Jira user
can be exported. The issue's embedded comment preview is never treated as a full
comment list. A changing, incomplete, duplicate or oversized response fails the
current ticket before publishing. Exports allow up to 10,000 visible comments and
8 MB of comment bodies, subject to the existing request deadline and the RAG
Settings file-size limit (2 MB by default). Nothing is silently truncated.

`<ISSUE-KEY>.md` contains the issue metadata, source link, description and comments
with author and timestamps. Jira wiki markup is converted with `jira2markdown`;
attachment binaries and embedded images are not downloaded. Stable ordering and
content, without a local export timestamp, keep identical exports byte-for-byte
unchanged. An unchanged file is not rewritten. Edited/deleted comments are reflected
on the next export, then in search after the next collection refresh.

Files carry a source marker identifying the Jira server and remote issue ID.
Existing files from another source are preserved with a conflict error. Refreshing
an export replaces the complete generated file, including any manual edits below
its marker. RAG compare-and-set hashes also protect changes made during publishing.
Exporting a smaller selection never removes previously exported files; use RAG file
or collection file deletion to remove them and clean their search data. Ticket key changes
require fetching the list again; any export under the previous key remains until
explicitly removed. `jira.ticket_exported` audit events record path, result and
comment count, without ticket bodies or tokens.

## Mock filtering and comments

The mock supports equality for `project`, `status`, `assignee`, `key` and `labels`,
`text ~ "words"`, and clauses joined with `AND`. Values may be double-quoted.
Optional sorting is `ORDER BY updated DESC` or `ORDER BY updated DESC, key DESC`.
Other JQL syntax is deliberately rejected; the real adapter forwards full JQL to
Jira without applying this mock grammar.

Try `project = DEMO AND status = "Open" ORDER BY updated DESC` (25 matches) or
`key = DEMO-1`. Every mock issue has five comments, with comment pages capped at
two results. The fifth comment contains **retry jitter resolved the payment
timeout**, making it easy to verify the final page reached Markdown and RAG search.
Rebuild the fixture after updating its code:

```sh
docker compose --env-file .env -f deploy/compose.yaml --profile jira-mock up -d --build jira-mock
```
