# Server profiles

Status: design approved 2026-10-04; awaiting spec review.

## Goal

Every server gets a versioned **profile**: an agent-readable description of the machine
(OS, services, containers, ports, logs, scheduled jobs, operational notes). A fixed,
read-only collector gathers facts over SSH and an LLM writes the profile as a **draft**;
people edit or extend the draft; an admin activates it as a new version. Every agent that
can use SSH on a server automatically receives that server's latest published profile in
its context.

Success criteria:

- An admin can refresh a server's profile and gets a draft written from real probe output,
  without the collector running any command that changes the server.
- People can propose changes by editing the single draft; an admin activates it as the next
  version, discards it, or restores an older version.
- The change history of the last two versions is visible.
- An agent run with SSH access to a server includes that server's latest published profile;
  drafts never reach agents.

## Context and roadmap

This is the second of four specs that build controlled server access:

1. Per-agent tool grants and SSH policies (`2026-10-04-tool-grants-design.md`, migration 0061).
2. **Server profiles** (this spec).
3. Who may use which agent (Reader Support tier, Keycloak group gating).
4. Runbooks built on tool grants and profiles.

"An agent can use SSH on a server" means the agent has an **enabled `ssh` grant** whose scope
includes that server, and the server is enabled and SSH-allowed (any SSH policy: full,
readonly or logs).

## Agreed decisions

| Decision | Choice |
| --- | --- |
| Storage | Dedicated tables: `ka.server_profiles` (published versions and the draft) and `ka.server_profile_history`. |
| Drafts | At most one draft per server. Anyone with admin access saves it; agents never read drafts. |
| Activation | Inserts a new published row with `version = max + 1`, deletes the draft. |
| History retention | Only history of the last two versions is kept. |
| Fact gathering | A fixed read-only collector (no LLM, no sudo); the LLM only writes text. |
| Agent refresh | The writer's result is saved **as the draft**; a person reviews and activates it. |
| People's input | People edit the draft; the next refresh takes the draft as input and verifies it. |
| Delivery to agents | Latest published profile auto-attached per server, capped at 12 000 characters, live per turn. |

## Data model — migration `0062_server_profiles`

```sql
CREATE TABLE ka.server_profiles (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    server_id     uuid NOT NULL,
    project_id    uuid NOT NULL,
    version       integer NOT NULL CHECK (version > 0),
    draft         boolean NOT NULL DEFAULT false,
    base_version  integer CHECK (base_version > 0),          -- drafts only; NULL when no published version exists
    content       text NOT NULL CHECK (length(content) BETWEEN 1 AND 40000),
    source        text NOT NULL CHECK (source IN ('agent','manual')),
    job_id        uuid REFERENCES ka.jobs(id),
    revision      integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_by    uuid REFERENCES ka.principals(id),
    updated_by    uuid REFERENCES ka.principals(id),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (server_id, project_id) REFERENCES ka.servers(id, project_id) ON DELETE CASCADE,
    CHECK (draft OR base_version IS NULL)
);
CREATE UNIQUE INDEX server_profiles_published ON ka.server_profiles(server_id, version) WHERE NOT draft;
CREATE UNIQUE INDEX server_profiles_draft ON ka.server_profiles(server_id) WHERE draft;

CREATE TABLE ka.server_profile_history (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    server_id  uuid NOT NULL REFERENCES ka.servers(id) ON DELETE CASCADE,
    version    integer NOT NULL CHECK (version > 0),
    action     text NOT NULL CHECK (action IN
               ('draft_saved','draft_refreshed','activated','draft_discarded','restored')),
    content    text NOT NULL,
    actor_id   uuid REFERENCES ka.principals(id),
    job_id     uuid REFERENCES ka.jobs(id),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX server_profile_history_server ON ka.server_profile_history(server_id, version DESC, id DESC);
```

- A draft's `version` is `max published version + 1` (1 when none exists) and its history
  rows use that number.
- `ka.jobs.kind` gains `server_profile`; a partial unique index allows at most one queued or
  running `server_profile` job per `server_id`.
- The down migration drops both tables and the job index and restores the previous
  `jobs_kind_check` constraint; it therefore fails while `server_profile` job rows exist,
  which protects their history.

## Lifecycle

All writes are admin-only, revision-checked (stale revision → 409 "The draft changed.
Reload before saving.") and recorded in `ka.audit_events`.

| Action | Effect |
| --- | --- |
| Save draft | Upsert the draft row. On creation `base_version` = current published version (NULL if none) and `version` = max + 1. `source = 'manual'`. History: `draft_saved` with the new content. |
| Refresh (job result) | Overwrite the draft (create it if missing) with the writer's output, `source = 'agent'`, `job_id` set. History: `draft_refreshed`. A person's previous draft text remains in history. |
| Activate | One transaction: lock the server's profile rows; refuse while a refresh job for this server is queued or running ("Wait for the refresh to finish."); if `base_version` is lower than the current published version, require `confirm_stale: true`; insert a published row with `version = max + 1` and the draft's content and source; delete the draft; history `activated`; delete history rows with `version < new_version - 1`. |
| Discard draft | Delete the draft; history `draft_discarded`. |
| Restore version N | Insert a published row with N's content (`source = 'manual'`), `version = max + 1`; history `restored`; prune as in Activate. An existing draft is kept (it becomes stale). |

## Refresh job

**Start:** `POST /api/servers/{id}/profile/refresh` with `{model_id}` (an enabled chat model).
Refused with 409 when a refresh for the server is already queued or running, or when the
server has SSH disabled or no saved user and key ("Configure SSH access first.").

**Worker:** the existing documentation worker (`knowledge-doc-worker`) also claims
`server_profile` jobs, using the same lease, retry-free failure and token-usage recording as
documentation jobs.

**Step 1 — collector** (`knowledge_admin/server_profile_collector.py`)

- Connects with the server's own SSH settings (user, port, key, trusted host keys) through the
  existing SSH helper; never uses sudo or `as_root`.
- Runs a fixed probe list; each probe has a 20 s timeout and output capped at 16 KiB:
  - system: `cat /etc/os-release`, `uname -a`, `uptime`, `nproc`, `free -m`,
    `df -hT -x tmpfs -x devtmpfs`, `lsblk`, `timedatectl`
  - services: `systemctl list-units --type=service --state=running --no-pager`,
    `systemctl list-unit-files --state=enabled --no-pager`, `systemctl --failed --no-pager`
  - containers: `docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'`,
    `docker compose ls`, `podman ps`
  - network: `ss -tlnp`, `ip -brief addr`
  - software: `java -version`, `python3 --version`, `node --version`, `go version`,
    `nginx -v`, `crontab -l`, `ls /etc/cron.d /etc/systemd/system`
  - logs: `ls -la /var/log`, `journalctl --disk-usage`
  - inventory hints: for each deployment on this server, `systemctl status <unit> --no-pager`
    or `docker inspect <container>`, and `ls -la <log location>`; names and paths are
    shell-quoted.
- A probe that fails, times out, or whose program is missing is recorded as unavailable and
  the run continues. A connection or authentication failure fails the job with the existing
  friendly SSH error.
- Each probe's command, exit code, duration and redacted output is written to `job_events`.

**Step 2 — writer** (one model call, no tools)

- Input: probe results (including the unavailable list), the current published content, the
  current draft content, and the server's inventory record (name, hostname, tags,
  description, deployments).
- Prompt: a seeded documentation prompt **"Server profile"** (`input_kind = 'server'`),
  versioned and editable under Docs → Prompts like the other documentation prompts.
- Required output sections: Summary; System; Services & containers; Network; Logs;
  Scheduled jobs; Operational notes & cautions; Unverified items.
- Draft claims the probes confirm are kept; claims they cannot check are kept and marked
  *(from people)*; claims they contradict move to Unverified items with the reason.
- The output is validated (non-empty, ≤ 40 000 characters, all required headings present);
  otherwise the job fails with "The model returned an incomplete profile."
- The result is saved as the draft (Lifecycle: Refresh).

**Failures** leave the draft unchanged: connection/authentication error, model or provider
error, invalid output, or an expired lease ("Worker stopped. Refresh again to retry.").
Token usage is recorded even for failed writer calls.

## Delivery to agents

- Helper `attach_server_profiles(conn, config) -> config` (in `knowledge_admin/server_profiles.py`):
  for each server in the scope of the agent's enabled `ssh` grant (`config['tools']`) that is
  enabled and SSH-allowed, it sets `server['profile']` to the latest published
  content, cut at 12 000 characters with the suffix `\n\n(profile truncated)`, or to `None`.
- Called when each turn starts: in the shared turn service (`start_turn`, for both new and
  pinned configurations), the multi-agent board's specialist runs, and the documentation
  worker's deployment/infrastructure runs. It is never written into agent versions or
  session snapshots, so an activation reaches the next turn of an existing conversation.
- `get_prompt` includes each host's `profile` in the SSH server resource context it already serialises,
  with the line: "Each SSH server includes its current profile; use it before investigating,
  and treat it as possibly outdated."
- `agents.agent()` and `agents.create_agent()` signatures are unchanged.

## API

| Method and path | Purpose |
| --- | --- |
| `GET /api/servers/{id}/profile` | Published latest, draft (if any), revisions, running refresh job (if any) |
| `GET /api/servers/{id}/profile/versions` | Published versions (number, source, author, date) |
| `GET /api/servers/{id}/profile/versions/{version}` | One published version's content |
| `GET /api/servers/{id}/profile/history` | History entries for the last two versions |
| `PUT /api/servers/{id}/profile/draft` | Save draft `{content, revision?}` |
| `DELETE /api/servers/{id}/profile/draft` | Discard draft `{revision}` |
| `POST /api/servers/{id}/profile/activate` | Activate `{revision, confirm_stale?}` |
| `POST /api/servers/{id}/profile/versions/{version}/restore` | Restore as new version |
| `POST /api/servers/{id}/profile/refresh` | Start refresh `{model_id}` → job |

`GET /api/servers` rows gain `profile: {version, has_draft, refreshing}` for the list column.

## UI

**Servers page (`/servers`):** a **Profile** column (`v4`, `v4 · draft pending`,
`Refreshing…`, `No profile`) linking to the profile page, and a **Profile** row action.

**Profile page (`/servers/:serverId/profile`):**

- Header: server name and hostname; active version, activation time, author, source;
  **Refresh profile** (model selection dialog, then live progress from job events and token
  usage; disabled while refreshing or without SSH credentials, with a link to SSH settings);
  **Edit draft**.
- Tabs:
  - **Active** — rendered Markdown with the note "Agents with SSH access to this server
    receive this version."
  - **Draft** — Write / Preview / **Compare with active**; last editor and source;
    **Save draft**, **Activate as vN**, **Discard draft**; stale-base warning requiring
    confirmation before activation.
  - **Versions** — published versions with **View** and **Restore as new version**.
  - **History** — entries for the last two versions with content and a diff against the
    previous entry.

## Testing

Test-first; no real model or SSH host (reuse the admin-api SSH test server fixture and the
fake Responses API model).

- Migration: one draft per server; unique published versions; `base_version` only on drafts;
  cascade on server delete; up/down.
- Lifecycle: draft upsert and history; activation atomicity, numbering, draft deletion and
  pruning to the last two versions; stale-draft confirmation; refusal during refresh;
  discard; restore; 409 on stale revision; audit events.
- Collector: exact probe list, timeouts, output caps, no sudo, unavailable probes skipped,
  quoting of inventory names, redaction.
- Writer: inputs include draft and active version; validation of required headings; result
  saved as draft; failures leave the draft unchanged; token usage recorded.
- Delivery: profile attached only for usable SSH servers; cap and truncation marker;
  activation reaches the next turn of an existing session; signatures unchanged.
- UI: Servers column; page tabs; draft editing and compare; refresh dialog and progress;
  stale confirmation; conflict message.

## Out of scope

- Reader / Support-tier access to profiles.
- Runbooks.
- Scheduled automatic refreshes.
- Exporting profiles to the RAG knowledge base.
