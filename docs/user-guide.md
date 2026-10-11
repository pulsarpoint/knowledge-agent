# Knowledge Agent v3 — User Guide

This guide explains how the application is used, from first start-up to day-to-day
work. It describes the behavior of the **current code** (including uncommitted work
on Generate documents, code graphs and the sandboxed shell worker), not the
intended design. Where existing documentation in `docs/` disagrees with the code,
the differences are listed in [Appendix B](#appendix-b-where-older-docs-disagree-with-the-code).

- [1. What the application is](#1-what-the-application-is)
- [2. Install, start and sign in](#2-install-start-and-sign-in)
- [3. Recommended first-time setup order](#3-recommended-first-time-setup-order)
- [4. The Administration application (port 8183)](#4-the-administration-application-port-8183)
  - [4.1 Navigation](#41-navigation)
  - [4.2 LLM Settings](#42-llm-settings)
  - [4.3 RAG: collections, processing, settings, search](#43-rag-collections-processing-settings-search)
  - [4.4 Code projects](#44-code-projects)
  - [4.5 Servers](#45-servers)
    - [Server discovery](#server-discovery)
  - [4.6 Environments](#46-environments)
  - [4.7 Services](#47-services)
  - [4.8 Jira](#48-jira)
  - [4.9 Skills](#49-skills)
  - [4.10 MCP servers](#410-mcp-servers)
  - [4.11 Agents](#411-agents)
  - [4.12 Generate documents](#412-generate-documents)
  - [4.13 Documents](#413-documents)
  - [4.14 Storage](#414-storage)
  - [4.15 Agent runs](#415-agent-runs)
  - [4.16 Schedules](#416-schedules)
  - [4.17 Events](#417-events)
  - [4.18 Event handlers](#418-event-handlers)
  - [4.19 Notifications](#419-notifications)
  - [4.20 System map](#420-system-map)
  - [4.21 Prompts](#421-prompts)
- [5. End-to-end workflows](#5-end-to-end-workflows)
- [6. Operations](#6-operations)
- [7. Current limitations](#7-current-limitations)
- [Appendix A: Agent tools and safety](#appendix-a-agent-tools-and-safety)
- [Appendix B: Where older docs disagree with the code](#appendix-b-where-older-docs-disagree-with-the-code)

---

## 1. What the application is

Knowledge Agent v3 is a self-hosted platform for building a knowledge base about
your software systems and running AI agents over it. It consists of one web
application (Administration), signed in through Keycloak and backed by PostgreSQL:

| Application | URL | Who | Purpose |
| --- | --- | --- | --- |
| **Administration** | http://localhost:8183 | Members of the `docs-admins` group only | Configure models, content, code, inventory, agents; generate documentation |
| Keycloak console | http://localhost:8181 | Keycloak master `admin` | Manage users and groups |
| Phoenix | http://localhost:8182 | `admin@localhost` | Inspect agent traces |

What you can actually do today:

- **Curate a searchable knowledge base** (RAG): review **collections**, each holding
  paths under `DATA_ROOT` that sources add (documents, Jira exports or source code),
  index them into a full-text (and optionally vector) index with **Refresh**, and
  search them — from the admin RAG pages and agents.
- **Map source code**: register local folders or clone Git repositories, keep them
  pulled on a schedule, and build a **code graph** (symbols, relationships, routes).
- **Keep an operational inventory**: services, their code, environments, servers,
  deployments and service-to-service dependencies.
- **Generate documents with an agent**: pick a model and what to document
  (code projects, environments, services, servers), follow the session and save the draft.
- **Configure and test AI agents** with prompts, skills, documents, code, SSH servers
  and MCP tools, and run them interactively (agent **Test** chat).
- **Import Jira tickets** into the knowledge base as Markdown.

Generated documents are searchable through a collection that holds `generated-documents` (`documents` on upgraded installs), after a **Refresh** or with Auto-sync in RAG → Collections
(see [4.3](#43-rag-collections-processing-settings-search)).

### Architecture at a glance

```
Browser ──► admin-oauth2-proxy :8183 (docs-admins only) ──► admin-web (nginx, Admin UI) ──► admin-api
                                                                                    │
     admin-api ──► rag, PostgreSQL, shell-worker (sandboxed bash), LLM providers, SSH hosts, Jira, MCP servers
     workers:  rag-worker (indexing) · repository-worker (git clone/pull, code graph)
               agent-session-worker (Generate documents, Test sessions, event-handler agent tasks)
```

`agent-session-worker` runs Generate documents and Test sessions, and the event-handler agent tasks (one-shot; each
answer goes to Notifications). Like `admin-api` it reaches
RAG, PostgreSQL, MCP servers, model providers and SSH hosts, and runs `shell_run` in `shell-worker`; it reads `DATA_ROOT`
read-only.

All ports bind to `127.0.0.1` only. Internal services have no host ports.

---

## 2. Install, start and sign in

Requirements: Docker with Compose, Python 3.10+ (for the setup script). No model
credentials are needed to start.

```sh
make up          # generate .env secrets, build images, migrate DB, start everything
make smoke       # verify admin login, gateway isolation, search, DB migrations, Phoenix (no model calls)
```

- The first time, choose where the application keeps its files:
  `make setup DATA_ROOT=/opt/knowledge-agent` (an absolute path; create it with `sudo`
  and `chown` it to your user if needed). `make up` refuses to start without it.
- `make up` runs `scripts/setup.py`, which creates `.env` (mode 0600) from
  `.env.example`, fills empty secrets with random values (never overwriting existing
  ones), records `DATA_ROOT`, and creates `code/`, `code-index/`, `docs/`,
  `generated-documents/` and `jira/` inside it (the names come from the `*_FOLDER`
  variables). It warns about any other entry in `DATA_ROOT`, because RAG can see every
  folder there; it never moves or deletes them.
- The bootstrap admin is `v3-admin` with the password `APP_ADMIN_PASSWORD` from `.env`.
  Change it in Keycloak afterwards, not in `.env`.
- Additional admins: create the user in the Keycloak console and add it to the
  `docs-admins` group.
- Users and group changes are managed in the Keycloak console
  (http://localhost:8181, user `admin`, password `KEYCLOAK_ADMIN_PASSWORD`), realm `docs`.
  Only `docs-admins` members can open Administration. After adding someone to a group
  they must sign out and back in.

Stop with `make down` (data volumes are preserved). `make status` and `make logs`
show state and logs.

---

## 3. Recommended first-time setup order

Most features depend on earlier ones. A sensible order in the Administration app:

1. **LLM Settings** → add a chat model and **Test connection**. Optionally add an
   embeddings model (AWS Bedrock Titan v2) for vector search.
2. **RAG → Settings** → select the embeddings model if you added one.
3. Create collections in **RAG → Collections** and add folders: `generated-documents` for documents, `jira` for Jira, `code/<folder>` for code; then **Refresh**, or switch on **Auto-sync**.
4. **Code projects** → map local folders or clone Git repositories; **Generate graph**.
5. **Servers** → register hosts; configure **SSH settings** if agents or deployment
   documentation should inspect them.
6. **Environments** → group servers and services (e.g. `production`, `staging`).
7. **Services** → describe applications, link code projects, add deployments and dependencies.
8. **Docs → Generate documents** → **New**: choose what to document → **Generate**.
9. **Agents** → create agents, assign a model, enable capabilities, test them.

---

## 4. The Administration application (port 8183)

Requires membership in `docs-admins`; otherwise you see "Administration unavailable"
with a **Sign out** button. `/` redirects to **RAG → Collections**.

General conventions throughout the admin app:

- **Revision-checked saves**: if someone else changed a record since you opened it,
  saving fails with a 409 such as "…changed. Reload before saving." Reload and redo the edit.
- **Secrets are write-only**: API keys, tokens and SSH private keys are encrypted
  (AES-GCM with `CREDENTIAL_ENCRYPTION_KEY`) and never shown again. A blank secret
  field keeps the saved value; use the explicit **Remove saved …** control to clear it.
- **Deletes ask for confirmation**; several records (LLM models, skills, code
  projects) cannot be deleted at all — disable them instead.
- Configuration changes are recorded in an internal audit table.

### 4.1 Navigation

| Group | Items |
| --- | --- |
| Administration | Storage · Prompts · Skills · MCP servers · Agent runs · Schedules · Events · Notifications |
| Agents | All agents |
| LLM Settings | Models |
| Jira | Tickets · Auto pull · Settings |
| Environments | Environments · Services · Servers · System map |
| Code | Projects · Code graphs |
| Docs | Documents · Generate documents · Folders |
| RAG | Collections · Processing jobs · Settings · Search |

Footer: theme toggle and **Sign out**. Legacy URLs `/docs/*` and `/github-projects`
redirect to their current pages.

### 4.2 LLM Settings

`/llm-settings` — named model connections used by agents
and RAG embeddings.

**Add model** fields:

| Field | Notes |
| --- | --- |
| Connection name | Unique |
| Model type | **Chat / agents** or **Embeddings** — fixed after creation |
| Provider | OpenAI compatible · Anthropic · AWS Bedrock (Anthropic not available for embeddings) |
| Model ID | Provider's model identifier |
| Base URL / AWS region | Base URL for OpenAI-compatible/Anthropic; region (e.g. `eu-central-1`) for Bedrock |
| API format | OpenAI chat only: **Responses** or **Chat Completions** |
| Embedding dimensions | Bedrock Titan v2 only: 256, 512 or 1024 |
| API key | Encrypted. Required for Anthropic and Bedrock (Bedrock uses a bearer API key, not IAM) |
| Enabled | Disabled models cannot be selected or tested |

**Test connection** sends "Reply with OK." (or one embedding) with a 35 s limit.
Changing provider, URL or region requires re-entering or removing the key. There is
no delete — disable a model instead.

> **Compatibility rule.** Agents and documentation generation require a "code-compatible" model: OpenAI
> **Responses** API, Anthropic, or Bedrock `anthropic.claude*`. OpenAI
> **Chat Completions** models can be assigned only as the Search model in LLM Settings.

### 4.3 RAG: collections, processing, settings, search

A **collection** is a named search index with its own settings and one or more folders
(under `DATA_ROOT`). RAG indexes the files where they already are — nothing is copied.
Everything is by whole collection: agents are granted collections, not subfolders.
Administrators create collections and connect folders here; sources only write files.

**Workflow**

1. **New collection** → name, description, settings and, optionally, **Folders**
   (**Choose a folder**, browse or type, **Add to selection**; several are allowed) →
   **Create collection** connects them and queues a sync at once. If a folder cannot be
   added, the collection is kept and opened with the error; fix its folders there.
   `?folder=<path>` (used by Docs → Folders) opens New collection with that folder
   selected.
2. **Add folder** on an existing collection's page: browse the folders or type a path
   (`generated-documents`, `jira`, `code/<folder>`).
3. **Refresh**, or switch on **Auto-sync** in Edit.
4. When embeddings are on, watch **Embedded** go from `0 / N` to `N / N`.

**Collections** (`/rag/collections`)

- The table shows name and description, folders, files, embedded chunks, last synced,
  status, a **Sync** column (**Auto · every N min** or **Manual**) and a **Refresh** button.
- **Status**: *Up to date*; *Needs refresh* (never synced, the settings changed, a folder was
  added or removed, or files in the folders were added, removed or changed since the last
  sync); *Syncing*; *Failed* (the previous index stays); *Folder missing* (any folder is
  missing; Refresh is refused and the index is kept); *Too large to sync* (over 20,000
  files, over 200,000 entries walked or over 200 MB of text; Refresh is disabled and
  Auto-sync queues nothing).
- **Refresh all stale** queues every collection that needs it.
- **Auto-sync** checks the folders every N minutes (1–1440, default 5; off by default) and
  waits until a change has been quiet for 2 minutes before it syncs. Automatic jobs show
  the mode *Auto* in Processing jobs.
- **Edit collection** shows the settings: description, search mode (Full text /
  Embedding / Hybrid), embeddings on/off, include and exclude patterns (default include
  `**/*.md`, `**/*.markdown`, `**/*.txt`; matched relative to each folder), chunk size
  (default 2000), overlap (default 200), **Auto-sync** and **Check every**. The name
  cannot be changed. Changing settings (not Auto-sync) makes the collection need refresh.
- **Collection detail**: the folders, each with **Remove** (confirmed; the files stay on
  disk), the status counts, and the **file log**: every file with its status, filtered by
  Indexed, Skipped, Failed or Removed, with a path search, paging, and the chunks of
  indexed files. There is no upload or edit.
- **Remove** (the collection) is always available, with confirmation. It removes the
  index rows, folders, file log and agent grants and keeps the files on disk.
- Skipped files never fail the sync. Binary files, files over the maximum size and files
  that cannot be opened are listed in the file log as skipped; symbolic links, special
  files, unreadable entries and invalid names are only counted ("Not listed: …" on the
  detail page). `.git`, `node_modules` and `.env*` are always ignored. A file that cannot
  be parsed is logged as failed, and one failing file no longer fails the sync. A
  collection with more than 20,000 files, 200,000 entries walked or 200 MB of text is
  *Too large to sync* — narrow the include patterns or folders. Code is indexed as plain
  text.

**Processing jobs** (`/rag/jobs`) — every sync job, newest first, in a paged table
(10/25/50/100 rows). Search by collection name or error text, and filter by status and
collection; filters and the page are kept in the URL, so a filtered view can be shared.
Each row shows the mode, start time, duration and counts (hashed, skipped, parsed,
embedded, reused, removed). **Cancel** a queued/running job; **Run again** otherwise. One job is active per collection; others queue. Unchanged files (same SHA-256
and settings) are skipped; a failed job keeps the previous index intact.

**Settings** (`/rag/settings`)

| Setting | Default / range |
| --- | --- |
| Maximum file size | 2 MB (100 B – 10 MB) |
| Embedding model | None by default; pick an *Embeddings* model from LLM Settings |
| Area roots | Read-only display of the five data folders (host and container paths) |

Chunking, patterns, search mode and the embeddings switch are per collection. After
selecting a model, use **Test embedding connection**, turn embeddings on for the
collections that should use them, then **Refresh** them.

**Search** (`/rag/search`) — query, a collection picker (empty means all), type
(**Full text**, **Embedding**, **Hybrid**) and 1–30 results. A collection without
embeddings (or with no model configured) falls back to full text, and the result lists
which ones did ("Keyword search used for: ..."). Results show a citation number, collection, full path, chunk text and hash.
This is retrieval only; it does not generate answers.

### 4.4 Code projects

`/code-projects` — the source code the platform can analyze. Files live under
`DATA_ROOT/<CODE_FOLDER>` on the host (default `code`), mounted read-only under
`/work/data` (only the repository worker and admin API have it writable).

**Folder browser** — navigate the code root, preview text files (up to 512 KB).
Symbolic links are listed but never followed.

**Add project**

| Field | Notes |
| --- | --- |
| Project name, description | Name required |
| Source | **Existing folders** or **Git repository** (fixed after creation) |
| Git repository URL | HTTPS only, no embedded credentials; `owner/repo` is expanded to GitHub. Cannot be changed later |
| Clone folder | Relative path under the code root; must not exist yet; cannot be moved after cloning |
| Branch, tag, or commit | Optional; empty = default branch |
| Private repository | Git username + access token (encrypted; turning the switch off deletes them) |
| Automatic pulls + interval | 5 minutes – 7 days (default 60 minutes) |
| Clone repository after saving | Clone immediately, or later with **Clone** |
| Mapped folders | Tree with checkboxes; select existing folders at any depth (max 100). Local projects need at least one |

**Pull / Clone** queues a job for the repository worker (shallow fetch, no hooks,
submodules or LFS; 10-minute limit). A pull refuses to run if the clone has local
modifications, untracked files or a changed origin — clean the folder first. Errors
are shown as a safe summary ("Check the URL, branch, token permissions…").

**Generate graph** builds a code graph with CodeGraph (no LLM, 15-minute limit):
symbols, relationships, routes, languages and parse errors. It skips `.env*`, keys,
dependency/build folders, binaries and files over 1 MiB, and reuses unchanged files.
**View graph** opens an explorer (files → symbols, filter, paging). **Code → Code graphs**
lists every project's graph (status, files, symbols, relationships, languages, last
generated) with **View graph** and **Generate graph**; `/code-graphs?project=<id>` opens one
directly. The graph is a
prerequisite for code documentation phases ([4.12](#412-generate-documents)).

Code projects only clone and pull into `code/…`. To search code, add the project's folders
to a collection in RAG → Collections; removing a project does not change RAG.

**Remove** (in the project's row) asks for confirmation and lists the project's folders. The
project disappears from Code projects, services and agents, its
scheduled pulls stop, and its folders and Git URL can be used by a new project. Files on disk
are kept unless you tick **Also delete these folders and the code graph from disk** (off by
default; it cannot be undone). Deleting is refused, and nothing is removed, when another code
project maps one of the folders (the same folder, a parent or a subfolder) or a RAG collection
indexes it; remove the folder in RAG → Collections first. RAG must be reachable to check this.
If a folder cannot be deleted afterwards (for example, permissions), the project is still
removed and the page names the folder left behind. The API is
`POST /api/code-projects/{id}/archive` with an optional body `{"delete_files": true}`.

Projects cannot be edited or removed while a job runs.

### 4.5 Servers

`/servers` — inventory of hosts. **Register server**: name, IP or hostname (no
URL/port), description, tags (comma-separated, lowercased, up to 32), **SSH allowed**.
Registering does not connect to anything.

**SSH settings** (per server):

- Default SSH user (root is refused), port (default 22).
- **Private key** — OpenSSH/PEM without passphrase; encrypted, shown afterwards only
  as "Private key saved" with **Replace key** / **Remove saved key**.
- **Trusted host keys** (known_hosts lines). *If empty, connections are made without
  host verification* — add them for production.
- **Allow sudo to root** — requires passwordless `sudo -n`.
- **Test connection** — public-key login (10 s timeout), shows the host key
  fingerprint with **Add to trusted hosts**, checks the host is Linux with `setpriv`
  and GNU `timeout`, and verifies sudo if enabled. Save afterwards.

A server cannot be deleted while service deployments reference it.

A server's name opens its page: details, SSH state, the services deployed on it, its
documents and its discovery runs. **Connections and documents** there opens the same
panel as the System map.

#### Server discovery

**Ask agent to collect info** on a server's page starts an agent that logs in over SSH,
looks around and proposes what it finds: services, how they are deployed on this
server, connections between services, and documents. Nothing is written to the
inventory until you accept it. The button needs SSH set up for the server and **SSH
allowed**; when one is missing, the button says which.

The instructions come from **Administration → Prompts** (tag `server-discovery`; the
built-in "Server discovery" prompt). You can edit them for this run only. Several runs
can be active at once.

The run page shows the agent's work (messages, thinking, tool calls) and, in the
sidebar, its proposals. You can write to the agent while it runs. **Review** opens one
proposal:

- A **new service** shows its fields.
- An **update** to an existing service shows the current value against the proposed one,
  with a **Keep** checkbox per changed field. Untick a field to keep the current value.
- A **document** shows its text.

**Accept** writes to the inventory: the deployment is added or updated, connections are
added, and documents are saved as generated documents in `servers/<server>/` and attached.
**Reject** discards the proposal. Rejecting a service also rejects the documents about it.
A document about a proposed service can be accepted only after the service is. If
something was added since the proposal was made, it turns into an update or a new version
and asks you to look at it again.

The agent is told to use read-only commands, but SSH itself is not read-only: anything
the login can do, the agent can do. Use a login with only the permissions you want the
agent to have.

### 4.6 Environments

`/environments` — named groups of services and servers (e.g. `prod`, `staging`).
Each asset can belong to several environments.

- **Environment list** tab: filter, **Graph**, **Documents**, Edit, Delete; click a
  name to see its members.
- **Dependency graph** tab: choose an environment to see the dependencies between its
  services; click a node for service details.
- Removing a service/server that a deployment in this environment uses is refused —
  update the deployment in Services first. Deleting an environment keeps its name as a
  plain label on former deployments.

### 4.7 Services

`/services` — the application inventory that ties code, environments and servers together.

**Add service**:

- **Application details**: name (unique), owner/team, **type** (Custom application,
  Built-in software, External service, Unclassified), **primary role** (Application,
  Proxy, Web server, Database, Cache, Message broker, Worker, Other), purpose.
- **Environments** it belongs to.
- **Code projects** that make up its source.
- **Service relationships**: *Depends on* / *Used by*, each with an optional "why".
  The other service is updated too.
- **Where it runs**: **Add deployment** (server, environment, runtime/manager,
  container/unit name, port, deployed version, log location). **Register server**
  opens the server form inline.
- **Behavior and investigation notes** — free text; keep secrets out of it.

**Details** shows everything above plus code commits and SSH status per deployment,
and links to the service's documents and environment graph. **Agent context (JSON)**
opens `/api/services/{id}/context`, a read-only, credential-free JSON view of the
service for agents. Note: the *downloaded* commit is not the *deployed* version.

Deleting a service removes its deployments and links but not code projects or servers.

### 4.8 Jira

**Settings** (`/jira/settings`) — Jira Data Center URL (include context path such as
`/jira`; use `host.docker.internal` for a Jira on your machine), username, personal
access token (encrypted). Save, then **Test connection** (checks the user identity
and search permission).

**Tickets** (`/jira/tickets`)

1. Enter a **JQL** query (default `ORDER BY updated DESC, key DESC`) and the number of
   tickets (1–200), then **Fetch tickets**. Results are stored locally.
2. Filter by text, project and status; tick tickets.
3. **Export selected** writes each ticket to `jira/<KEY>.md` with metadata,
   description and comments. Keep the page open until it finishes; failed tickets stay
   selected. A RAG error fails the export with its message.
4. Make sure a collection holds `jira` (RAG → Collections → Add folder), then
   Refresh it or let Auto-sync pick it up.

For testing without a real Jira: `make jira-mock-up`, then URL
`http://jira-mock:8080/jira`, user `mock-admin`, token `jira-mock-token`
(75 demo tickets). Stop with `make jira-mock-down`.

**Auto pull.** Jira → Auto pull fetches tickets on a timetable with its own query and ticket limit (separate from the
query on Jira → Tickets). Switch on **Pull tickets automatically**, enter the JQL and number of tickets, pick how often
(a preset or a custom cron expression) and the time zone, check the preview of the next runs, and **Save**. The page
shows the last pull and the next one; **Run now** pulls at once. Jira → Tickets shows a one-line summary. Jira → Tickets always shows the tickets from the
most recent pull, manual or automatic; its query box keeps your manual query. A failed pull is shown and retried at the next scheduled time; a pull that meets a manual fetch is skipped. Each scheduled pull also
records an event for every ticket that is new or changed since its last pull (see Administration → Events); the manual
Fetch tickets does not.

### 4.9 Skills

`/skills` — a library of reusable Markdown instructions that agents can be given.
Fields: name (lowercase-with-dashes, unique), description, instructions (Markdown),
enabled. Search and filter by status; toggle enabled inline. Skills are context only
(injected into the agent's instructions, never executed) and cannot be deleted — disable them.

### 4.10 MCP servers

`/mcp-servers` — external tool servers agents can call. Only the **Streamable HTTP**
transport is supported. Fields: name, description, HTTP(S) endpoint (use
`host.docker.internal` instead of `localhost`), optional bearer token (encrypted),
enabled.

**Test connection** discovers the server's tools (25 s limit). A server can only be
selected for an agent after it is enabled, tested successfully and exposes at least
one tool. Changing the URL clears discovered tools and requires re-entering the token.

### 4.11 Agents

`/agents` lists agents with **Resources**, capabilities, model, active system prompt and status. **Resources** shows `Fixed`, or what a per-run agent accepts, for example `Per run · code (any) · servers (2)`.

**Create an agent** with **New agent** (the only way to create agents). It opens the wizard directly. Its first fields are **Agent model** (the model the agent runs on; it defaults to the first model in the catalog) and **Resources** (Fixed or Per run). Both a generated proposal and **Start blank** use that model. The review shows the two as one line (for example `Claude Opus · Per run`); to change them go back with **Edit role**:

- **Generate proposal** — describe the role, choose a generator model, and the model
  proposes the name, prompt, documents, code projects, SSH, skills, MCP and services.
- **Start blank** — skip generation (no model needed) and fill in the same review tabs
  yourself.

Review and edit the tabs, then **Create agent** (enabled once name and system prompt are
filled in). The agent model is optional — an agent without a model is
saved but runs only after you choose one with **Model** on its General tab.

**Agent detail tabs** (`/agents/:id`):

| Tab | What you do there |
| --- | --- |
| **General** | **Resources** (Fixed: the agent always works with its selections; Per run: each run says what to work on, from those selections), **Model** (required to run; saved with **Save details**), name, purpose, when-to-use, **Enable agent**; **May start subagents** (the agent can hand separate parts of its work to helpers that run in parallel); **Offer as an expert** (other agents can call this agent as a tool; needs When to use; an **Expert** badge shows in All agents); additional instructions; the **System prompt** card (one field, up to 100,000 characters; **Save prompt**); *Clone of <original>* for a clone |
| **Skills** | Enable and pick skills (max 30) |
| **Documents** | Enable and pick RAG collections, or "All collections" (whole collections only; there are no subfolder grants); **Answer only from documents** switch (cite every claim and report questions the documents do not cover). For a per-run agent: what callers may pass (the tab says so) |
| **Code projects** | Enable and pick code projects, or "All code projects". For a per-run agent: what callers may pass (the tab says so) |
| **SSH** | Choose allowed servers; per-server SSH overrides (user, key, host keys, sudo) and per-server instructions/skills/MCP. For a per-run agent: what callers may pass (the tab says so) |
| **MCP** | Pick tested MCP servers (and their tools) |
| **Tools** | Switch tools on or off (Code, Documents, Servers, MCP, Agents groups), **Edit usage** / **Reset** their instructions, see "Needs …" for tools whose resources are missing; **Save tools**. With **May start subagents** on, the Agents group has **Start subagents** (usage, **Reset**) and **A subagent may use**. The **Experts** group lists the agents offered as experts, each with a switch (on by default). |
| **Services** | Pick services whose descriptions are added to the agent's context. For a per-run agent: what callers may pass (the tab says so) |
| **Test** | Chat with the agent (a per-run agent first picks **Resources for this run**) |

An agent has one configuration: what its tabs show is what runs. A conversation keeps the configuration it started with.

**Experiment on a clone.** Open the agent's header menu (*Actions for <name>*) or the **Clone <name>** button in the All agents list and choose **Clone agent**: the clone has the same configuration, references the same saved SSH credentials, starts without history and shows *Clone of <original>*. Change and test the clone, then **Apply to <original>**: the dialog lists the changed sections, and everything except name, description and enabled is replaced. Apply is refused while the original is working or when the original changed since you opened the dialog; if the comparison fails, **Compare again**. Afterwards **Delete this clone** removes the clone (its dialog lists the test chats and other history deleted with it), or keep it as an alternative.

**Test tab.** Type a message and **Run task**: it starts a Test session, which the agent session worker runs (the URL
keeps `session_id`). The workspace shows your message, a collapsed **Worked** block (thinking, tool calls with their
results, comments you sent while it worked) and the answer with its cited sources; the header shows the tokens, model
calls and tools used, and the sidebar **Agents in this run** the agent's status (on a narrow screen open **Run
details**). **Send message** works while the agent works (your comment reaches it at its next step); **Stop** ends the
current turn. Header: **History** (this agent's Test sessions), **Show config** (the configuration pinned for this
session, or the current one before the first message; secrets as references), **New session**, **Close session**.
Under each answer: **Full record** (the turn's record in Agent runs) and **Add feedback** → **Suggest improvements**, which runs a reviewer that proposes
prompt and instruction changes; the sidebar's **Suggestions (n)** lists them, and **Review and apply** opens them for
editing, then **Apply to this agent** or **Apply to a new clone**.

**Apply suggestions.** In a suggestion, **Review and apply** opens the sheet *Apply suggestions*. **Apply to this agent** writes the edited prompt and instructions into the agent (“Applied to this agent. New conversations use it.”); **Apply to a new clone** clones the agent first and writes them there, leaving the original unchanged.

**Choosing tools.** The **Tools** tab (and the wizard's Tools page) decides which tools a native agent gets; new agents start with all tools of the capabilities they use; switching a capability on switches all of its tools on, and a per-run agent gets all tools of the capabilities it uses when it becomes per run or gets one switched on. Agents created earlier keep their tools. Turn a tool off to remove it from the model, or use **Edit usage** to change the instructions the model reads for it (**Reset** restores the built-in text; for an MCP tool the edit is added to the prompt as a "Tool notes" line). A tool marked "Needs …" is saved but not offered until that resource is configured. Grounded agents always keep `report_not_covered`. **All agents** shows a **Tools** column, and **Agent runs**, run records, session pages and Test chats show how often each tool was used.

**Subagents.** With **May start subagents** on (General, or the wizard's Tools page) a native agent can start helpers
with `run_subagent`: each gets a complete task, the tools you allow under **A subagent may use** that the agent passes
it, and some or all of the agent's resources, but not the conversation. Helpers run in parallel (`AGENT_MAX_SUBAGENTS`,
default 4, at once; at most 16 helpers per turn, together with expert calls) and their final answer comes back to the agent. In the Test tab and on the
session page each helper appears in the Worked block (**Open**) and under **Agents in this run** (helpers in the loaded
history; earlier ones appear when you scroll up to older history); selecting one shows its task, its work and its
answer (**Main agent** goes back). The header and the main agent's entry count the whole run including helpers, while
the main turn's Worked line counts only the agent's own work. **Stop** stops the agent and all of its helpers.

**Experts.** Turn on **Offer as an expert** on an agent's General tab (it needs a When to use text, which tells other
agents when to call it; the agent must be enabled and have a model). Every other native agent, including Generate
documents agents, then gets a tool `ask_<name>` for it. In an agent's **Tools** tab the **Experts** group lists the
experts with their When to use and whether they use fixed or per-run resources; switch one off to stop this agent
from calling it. The group says "No experts yet. Turn on Offer as an expert on an agent's General tab." when there is
none, and an expert itself sees "When this agent runs as an expert it cannot call experts." A per-run expert works
only on the resources the caller passes from its own; a fixed expert uses its own. An expert starts without the
conversation, may start its own subagents if it has that switch on, uses its own model and keys, and returns its final
answer. The helpers of one turn (subagents and expert calls together) are limited to 16; sessions started before an
expert was added do not see it. In the Test tab and on the session page an expert call appears in the Worked block as
`Asked <name> · <task>` with **Open**, and under **Agents in this run** as `Expert · <name>` once it has started
(selecting it shows its history; **Main agent** goes back); a call refused before it started shows as a failed tool
line. In **Agent runs**, filtering by the expert's agent lists the calls made to it as "Called as expert by <caller>";
opening one shows that call's record, including the expert's own subagents.
In the wizard, a hand-picked tool
list wins over the "all tools" default.

**Per-run agents (role agents).** A per-run agent defines what it does (prompt, instructions, skills, tools); each run says what it works on. Its tabs' selections are what a run **may** be given: **All code projects** / **All collections** = any. The agent's setting decides the mode. On the **Test** tab, the line **Resources: …** (with **Choose resources**) appears before the first message only and opens **Resources for this run**, one list per kind the agent uses; the choice is kept for the session and shown in the header and in **Show config**. The agent's tools reach exactly what the run was given: a kind with nothing chosen answers "No code project was given for this run" (never everything). A choice outside the selection is refused (`Code project <name> is not allowed for this agent.`), as are removed, archived or disabled resources and kinds the agent does not use. Resources passed to a fixed agent are refused (`This agent uses fixed resources.`). Example: a **Code analyst** with Code projects on and **All code projects**, tested with `payments-api` in one conversation and `helpers` in the next.

Limits: message ≤ 20,000 characters; a Test session's turns have no time
limit (they end with the answer, **Stop**, **Close session** or a lost worker), and a session closes after 24 hours
without activity. A message is blocked if the agent is disabled, has no enabled model, or other tabs have unsaved
changes.

**Upgrading.** Migration 0060 turns **Answer only from documents** on and Code
(repositories) off for the Search agent; its down migration does not turn Code back
on. Saving the agent's settings changes what runs next.


Removing an agent that has history (test chats, documentation sessions or run records of earlier test chats) lists that
history in the **Remove agent?** dialog. **Delete agent and history** deletes the agent together with it; saved
documents are kept. To keep the history, cancel and disable the agent instead. An agent that is working right now
(a Test or documentation session that is starting or working) cannot be removed; stop that work or wait for
it to finish first.
See [Appendix A](#appendix-a-agent-tools-and-safety) for the tools an agent receives.

### 4.12 Generate documents

**Docs → Generate documents** (`/documentation/generations`) lists sessions in which an agent writes one
document: status, model, tokens and the saved document. **New** opens the form. Sessions continue when you leave the page
and close after 24 idle hours (a session idle for 5 minutes is paused and resumes on your next message).

**Prerequisites**

- An enabled model that is a Responses or Claude connection. The session runs an agent made for it; there is no saved agent.

**The form**

- **Agent context** (closed by default; one line shows the model and the system prompt): **Model**, **System prompt**
  and **Context**. The context is generated from what you choose below and can be edited; **Regenerate from selection**
  replaces your edits.
- **What to document**: tick **Code**, **Documents**, **Environment**, **Services** and/or **Servers**; each shows its picker. Pick at
  least one item.
- **Prompt**: **Write my own**, or a saved prompt from Administration → Prompts; **Prompt tag**
  narrows the list. **Prompt text** is the first message; editing a saved prompt changes only this run.
- **Document name** and **folder**: the folder defaults to `<first code project>-docs`, or `docs`. Folder names are
  lowercase (`Payments` and `payments` would be the same folder on macOS): each segment uses lowercase letters, digits,
  '.', '_' and '-', does not start with '.'; `node_modules` and a first segment `services` are not allowed; at most 200
  characters. The name needs ASCII letters or digits (the file name is made from them).

The agent also gets `run_subagent` and every agent offered as an expert (`ask_<name>`). The default system prompt tells it to plan sections first when the document covers several parts, to ask an expert where its description fits and otherwise start a subagent with a complete task and only that part's resources, to write small single-part documents itself, and to combine the answers into one consistent document, resolving or marking conflicts. Subagents are asked to return finished markdown sections with their evidence cited. Sessions created before this version keep their configuration (no experts, no subagents).

The agent gets exactly what you choose: its code projects, collections and servers are the ones ticked (environments and
services also bring their code projects and the servers they are deployed on; servers without SSH are skipped). Its tools
(`list_files`, `read_file`, `search_files`, `shell_run`, `code_search`, `ast_grep`, documents, and `ssh_run` when servers are chosen) work as in an agent's Test tab. The agent can connect over SSH to the servers you tick and to the SSH-allowed servers that environments and services bring; servers without a usable connection are left out, and a ticked server that cannot be used is refused.

**What the agent receives.** The system prompt and context from the form, then one first message listing
what to document, as a user could have typed it:

```
Document the following.

## Code
- Payments API — /work/data/code/payments-api

## Documents
- runbooks: Payments runbooks

## Services
- Payments — deployed on prod-1 (production); code: Payments API (/work/data/code/payments-api)

## Servers
- prod-1 (10.0.0.5): Payments host

## Instructions
Focus on the public API.

When you finish, reply with the complete document in Markdown as your final answer: it becomes the draft. After each
later message, reply again with the complete updated document.
```

Environments and services are described as text (servers, services, deployments, linked code projects).

A turn has no time or model-call limit: it ends when the agent
finishes or you press **Stop** (each model request and MCP connection still times out after 5 minutes). A failed or stopped
turn leaves the conversation and the draft as they were; history is saved only when a turn succeeds. Tokens are recorded,
cost is not.

**The session page.** It uses the same workspace as the Test tab: the main pane shows each turn (your message, a
collapsed **Worked** block with thinking, tool calls and results, and the answer) and scrolls on its own; the message box
is always visible. The main pane follows new events at the bottom; after you scroll up it stops following and shows
**Jump to latest**. Opening a session loads the latest 100 events; earlier pages load as you scroll up until **Beginning
of the session**. The header shows tokens in/out/cached, model calls and the status: Starting, Working, Waiting for you,
Stopped, Failed or Finished; the sidebar shows **Agents in this run** and **Draft · turn n** (on a narrow screen open
**Run details**). Each answer links to its **Full record**.

- **Send message**: the message box stays enabled while the agent works. The agent receives
  the comment at its next step in the same turn ("Queued. Your comment reaches the agent at its next step."). Sent between turns, a message starts a new turn in the same conversation.
- **Stop**: always ends the turn: it is recorded as stopped within about 30 seconds of Stop, and after that it cannot start
  new model requests or tool calls (a request or command already running at Stop cannot be recalled). Queued messages are
  discarded when the turn is stopped (a message sent after Stop starts the next turn); after a failure or a lost worker they go to the next turn.
- **Draft**: each finished turn's final answer, as Markdown; **Open draft** shows it in a wide side panel.
- **Save document**: a versioned document in the folder, linked to the code projects and exported to
  `GENERATED_DOCS_FOLDER/<folder>/<slug>/README.md`. The same name in the same folder asks **Save a new version?**; the
  same name in another folder is a separate document. Nothing is saved automatically.
- **Close session**.

Every turn is an agent run in **Activity → Agent runs** (`cc:<session>:<turn>`).

Errors: "Choose an enabled model."; "Choose at least one code project, collection, environment, service or server to document."; "Select available …" for an item that is gone; `… is not available: it was removed, archived or disabled.`; a provider error, such as
a missing Bedrock inference profile, shows the provider's reason without credentials plus a hint; "Waiting for the agent session worker…" when the worker is not running.

Security: the model key stays in the worker. Every enabled model connection's key is
redacted from stored events, drafts and error messages. `shell_run` runs in the sandboxed `shell-worker`, with `DATA_ROOT`
read-only.

**Upgrading.** Migration 0073 removed the service, whole-environment and code-project generation, its workflows, history
and run records. Saved documents, their versions and exported files stay, and documents written by service phases keep
exporting into `services/<service>/<phase>/`. Old `/documentation/sessions` links redirect. Bookmarks of old generations
(`/documentation/generations/<id>`) open a page that shows "Session not found".

### 4.13 Documents

**Documents** (`/documents`) — all generated and hand-written documents. Search by
title/slug/description and filter by linked resources. **Create document** writes one
manually.

The editor has fields Title, Slug, Short description, Purpose, Audience (People and
agents / People / Agents), Tags, and tabs **Write**, **Preview**, **Linked resources**
(including documented source commits) and **Version history** (**Use version as
draft**). Actions: **Save new version** (every save is a full immutable version),
**Export to folder**, **Download document folder** (ZIP), and **Delete**.

Documents are searchable through a collection that holds `generated-documents` (`documents` on upgraded installs) (exclude old `v<n>/` folders in its patterns): **Refresh** it in
**RAG → Collections**, or turn on Auto-sync, to index what is on disk. **RAG → Search** and the
agents' document search then find them.

**Folders** (`/documentation/folders`) — the documentation folders with their document
count, code projects and last generated time, and, read-only, the RAG collections that
include each one (**Not indexed**, or each collection, "through <folder>" when it holds
a parent folder), each linking to its collection. **Index in RAG → Collections** opens
RAG's New collection with the folder selected. Opening a folder lists its documents
(title, code project, last generated), each opening in Documents. Editing a folder
document keeps it in its folder.

### 4.14 Storage

`/storage` — read-only overview of the data folder (`DATA_ROOT`) and its five folders: source code
(`CODE_FOLDER`), code graphs (`CODE_INDEX_FOLDER`), Jira exports (`JIRA_FOLDER`), document
exports (`GENERATED_DOCS_FOLDER`) and bootstrap docs (`DOCS_FOLDER`). Each shows host and
container paths, availability, and a file browser with text preview.

### 4.15 Agent runs

`/activity/runs` — a complete record of every agent run: agent Test
sessions, earlier test chats and Generate documents sessions. Each record shows, in order:

- the full instructions sent to the model and the input (with conversation history);
- the model's reasoning when the provider returns it (otherwise "Reasoning not returned
  by the provider.");
- every tool call with its full arguments and full output (SSH, shell, code, documents,
  MCP…);
- messages, token usage per model call, errors, and the final status.

Known secrets are replaced with `[redacted]`; very large values are cut at 1,000,000
characters. The run type filter offers **Test session** (Test tab turns), **Agent test** (test chats from before the upgrade) and **Native session** (Generate documents turns); you
can also filter by user. **Full record** links lead here from each reply in an agent's Test tab and from each
Generate documents session turn.
Records are kept until deleted; only administrators can see them.

### 4.16 Schedules

Administration → Schedules lists everything that runs on a timetable (today: the Jira auto pull). **New schedule**
creates one: choose what to run (today: Jira tickets, which uses the query from Jira → Auto pull) and when. **Delete**
removes a schedule and its history. Each row shows what
it runs, when (in plain words and its time zone), the next run and the last result. Switch a schedule off to pause it;
**Run now** runs it once (a paused schedule stays paused); **Edit** changes the timetable: a preset or a custom cron
expression (minute, hour, day of month, month, day of week), the time zone, with a preview of the next three runs.
Schedules run at most every 5 minutes. **History** shows the newest 20 runs. Schedules are run by the
`repository-worker` service; after downtime a missed schedule runs once, not once per missed time.

### 4.17 Events

Administration → Events lists what happened at your sources and what handlers made of it: Time, Source, Type,
Subject, Title and Status. Click a column header to sort by it, and again to flip the order. Today the source is Jira:
every scheduled pull (Jira → Auto pull, or Run now) records **Ticket created** for a ticket it sees for the first time
and **Ticket updated** when a ticket changed in Jira, with what changed (status, labels, assignee, priority, type,
summary, description). The very first pull records every ticket it fetches. Events a handler emitted have the source
**Handler** (see 4.18).

Each event is **Pending** until the event handlers have looked at it (within a few seconds), then **Processed** — or
**Failed** when one of its handlers failed; failed events are not retried. Events recorded before handlers existed are
Processed. Filter by source, type, status and **Emitted by** (the handler that emitted the event), search by ticket
key or title, and open an event to see its
changes, a link to the ticket, the raw data, its **Chain** (the event it came from and everything handlers made of it,
indented by level) and its **Handler runs** (each handler's result, with links to the event it emitted, or, for Send
to agent, the agent task's status and its notification).

**New event** (next to Refresh) makes an event by hand, to try your handlers without waiting for Jira. Fill in
**Type** (e.g. `jira.ticket.created`; known types are suggested), **Subject**, an optional **Title** and the
**Payload**, a JSON object (the form shows what is wrong with it and keeps Create disabled until it is valid), and
choose the **Source**: **Manual**, or **Jira (simulated)** so that handlers with a `source equals jira` condition
match. Create saves the event, opens it, and the handlers run on it like on any other event: it is Pending, then
Processed or Failed (press Refresh to see it change). **Duplicate**, in an open event, starts a new event from a copy
of it (a Jira event is duplicated as Jira, simulated), handy for re-running a handler with a small change to the
payload. Events made by hand have no Jira connection and never affect what the scheduled pull records; their detail
shows **Created by hand**.

### 4.18 Event handlers

Administration → Events → **Handlers** reacts to events. Every few seconds repository-worker takes each new event and
runs every enabled handler whose **Event type** pattern matches (`*` for any part: `jira.ticket.*`, `jira.*.created`,
or `*` for every event) and whose **Conditions** all hold. Conditions compare a field — `source`, `type`, `subject`,
`title` or `payload.<field>` such as `payload.status` — with **equals**, **does not equal**, **contains** (text or
list item), **is one of** (comma-separated values), **matches regex** or **exists**. What changed in a Jira ticket is
under `payload.changes`: a changed status, assignee, priority, type or summary is `{from, to}`, so compare
`payload.changes.status.to` (or `.from`) — `payload.changes.status` with **exists** means "the status changed";
labels are `payload.changes.labels.added` and `.removed` (lists, use **contains**); a changed description is
`payload.changes.description` (use **exists**).

A handler does one thing:

- **Emit event** creates a new event in the same chain. **New event type** (e.g. `triage.requested`), **Subject**,
  **Title** and **Payload** are Jinja templates over the matched event, available as `event` (`event.subject`,
  `event.payload.summary`, …). Empty Subject, Title or Payload copy the matched event's. Write payload values with
  `| tojson`, e.g. `{"summary": {{ event.payload.summary | tojson }}}`; the payload must be a JSON object. The new
  event is processed like any other, so handlers can build chains — at most 5 levels deep.
- **Send to agent** queues one call of the chosen agent for every matching event; the **Prompt** template is the
  message. agent-session-worker runs it once, with no conversation and no streaming, and puts the answer in
  **Notifications** (4.19). A call that fails is tried again 30 seconds and then 2 minutes later (3 attempts in all);
  an agent that was deleted, disabled, switched to per-run resources or left without a usable model fails at once.
  A worker that stops hands a running call back without counting the attempt. Only enabled agents with fixed
  resources can be chosen. A handler whose agent was deleted or no longer qualifies can still be turned off or
  edited; choosing a new agent or turning it back on checks the agent again. Processing the same event again never
  queues a second call. Calls appear in Agent runs as **Event task**.

**Careful with Send to agent on broad patterns.** Every matching event runs the agent once. The first scheduled Jira
pull records every ticket it fetches as **Ticket created**, so a `jira.ticket.*` handler runs the agent once per
ticket; `*` and other broad patterns also match the events other handlers emit, multiplying calls down a chain. Start
narrow (a specific type plus conditions), check with **Preview**, and widen later. The form warns when Send to agent
uses `*` or a pattern ending in `.*`.

Templates render in a separate process with a 2 second time limit (and, on Linux, a 512 MiB memory limit); a template
that overruns fails with `Template took too long to render.` or `Template used too much memory.`

**Preview** renders the handler against a recent event without saving: it says whether the event matches (or which
check failed) and shows the new event or prompt. A handler that fails — a template error, a disabled or deleted agent,
a chain that is too deep — marks the event **Failed** and records why under **Handler runs**; other handlers on that
event still run. Deleting a handler keeps its run history on the events.

### 4.19 Notifications

Administration → **Notifications** lists what agents answered when a handler sent them an event, newest first: Time,
Status (**Succeeded** or **Failed**), Title (`<agent> · <event type> <subject>`) and the first line of the answer
(taken from its first 200 characters, cut off when long). Everyone sees the same list. Filter by status or search the
title; **Load more** and **Refresh** work as on Events. Open a notification to read the whole answer (or why the call
failed, after its last attempt), the agent, the number of attempts and **Open event**, which opens the event that
triggered it. On the event, the handler run shows the call's status (Queued, Running, Succeeded or Failed) and **Open
notification**.

### 4.20 System map

Environments → **System map** draws your inventory — environments, servers, services and code projects — and the
connections between them. Nothing is drawn by hand: add entries on their own pages, and connect them here.

- **Infrastructure.** Each environment holds its servers (the servers you added to it on the Environments page, and
  any server with a deployment there), and each server holds the services deployed on it in that environment. A server
  in two environments appears in both. A server in no environment, or with a deployment that has no environment, sits
  at the top level. A service that belongs to an environment but is not deployed there sits directly in the
  environment. Lines are *connects to* connections.
- **Services.** Every service and code project, with *depends on*, *built from* and *connects to* lines. Archived code
  projects are left out.
- **Browsing.** **Inside** lists what the current box holds (→ opens it; "+N inside" says it holds more), **Outside**
  lists what its connections reach elsewhere. The breadcrumb goes back up. **Depth** shows up to 3 levels; deeper
  entries fold into their box and lines add up (`connects to ×3`). When both ends of a connection are in one environment, the line stays inside it: it never joins one environment's copy of a server to another's. Everything you choose is in the page address.
- **Views.** **Diagram** is drawn by D2 with the layout you pick (tala, elk or dagre); **Mermaid** draws the same view
  with Mermaid; **D2** and **JSON** show the source, with **Copy**.
- **Connections.** Click an entry's name: the panel shows its connections and documents and links to its page.
  **Connect to…** picks a kind — *depends on* (service → service), *built from* (service → code project) or
  *connects to* (anything → anything) — an optional description and attributes (one `name: value` per line, at most
  20), then the target by name. **Edit** changes the description and attributes; to change the ends or the kind,
  remove the connection and add it again. A *built from* connection starts from the service; a code project's panel offers *connects to* only. The Services page edits the same *depends on* and *built from*
  connections; an open Services form that someone changed meanwhile asks you to reload.
- **Documents.** Attach documents from Docs → Documents to any entry; detaching never deletes the document. Deleting an
  entry removes its connections and attachments.
- **Upgrading from the stored graph.** Service dependencies and code project links become connections. Documents
  attached to graph nodes move to the server, environment, service or code project behind the node. Nodes you drew by
  hand (and their attachments) are gone; the documents themselves stay in Docs → Documents.

### 4.21 Prompts

**Administration → Prompts** (`/prompts`) is the library of reusable instructions for agents. Each prompt has tags —
lowercase letters, digits and `-`, at most 10 — that features use to find it: Generate documents can filter by tag,
and the server discovery sheet will offer prompts tagged `server-discovery`. Type a tag and press Enter to add it;
existing tags are suggested. Saving changed text creates a draft version; **Activate vN** makes it the active one;
inactive versions can be deleted. Changing only the name, description, tags or Enabled does not create a version.
Prompts that came with the application are tagged `documentation` plus what they document (`code`, `server`,
`service`, `mixed`). Old `/documentation/prompts` addresses open the same page.

---

## 5. End-to-end workflows

### A. Make internal documentation searchable

1. Write the pages in **Docs → Documents**; they are exported to `generated-documents`.
   (Code is made searchable by adding its `code/<folder>` to a collection.)
2. Admin → **RAG → Collections**: make sure a collection holds `generated-documents`
   (`documents` on upgraded installs), then **Refresh** it; watch **Processing jobs**
   until it succeeds.
3. Check the collection's files under its detail page.
4. Check results in **RAG → Search**.

### B. Document a service from its source code

1. **LLM Settings**: add and test an Anthropic (or Bedrock Claude) model.
2. **Code projects**: add the Git repository; wait for the clone.
3. **Services**: create the service, link the code project.
4. **Administration → Prompts**: save a documentation prompt (or write it in the form).
5. **Docs → Generate documents → New**: choose the model and the prompt, tick **Services** and pick the service → **Generate**. The agent reads the code projects the service links.
6. Follow the session, send messages such as "shorten the API section", then **Save document**.
7. Make the documents searchable through a collection that holds `generated-documents` (`documents` on upgraded installs); **RAG → Collections** → **Refresh** it.

### C. Document deployments and infrastructure

1. **Servers**: register hosts. **Environments**: create `production` with its servers and services.
2. **Services**: add deployments (server + environment + container/unit + port + log path).
3. **Generate documents → New**: tick **Environment** and pick it (or tick **Servers**), add instructions about
   deployments → **Generate**.
4. The agent connects over SSH to the chosen servers (and to those the environment brings).

### D. Build and test an operations agent

1. **Agents → New agent → Generic agent** (generate a proposal or **Start blank**).
2. **General**: assign a model; **SSH**: allow servers; **Code projects** / **Documents**: select resources.
3. **Test**: run a task such as "Check disk usage on the app servers"; inspect the **Worked** block.
4. Use **Add feedback → Suggest improvements**, then **Review and apply → Apply to this agent** (or **Apply to a new clone** to try it first).

### E. Bring Jira knowledge into RAG

**Jira Settings** → save and test → **Tickets** → fetch with JQL → select → **Export
selected** → make sure a collection holds `jira` (RAG → Collections → Add
folder) → **Refresh** it or let Auto-sync pick it up.

### F. Make one code project's documentation searchable

1. **Generate documents** → **New** → **Code**: the project → **Generate** → **Save document**.
2. **Docs → Folders** → **Index in RAG → Collections** → **Create collection** (the folder
   is selected; the sync starts at once).
3. Grant the collection to agents in **Agents**.

### G. Write documentation interactively with an agent

1. **Docs → Generate documents → New** → choose the model, what to document and a prompt → **Generate**.
2. Follow the stream and send a message such as "shorten the API section".
3. **Save document**.
4. **Docs → Folders** → **Index in RAG → Collections**.

---

## 6. Operations

### Configuration (`.env`)

| Variable | Meaning |
| --- | --- |
| `ADMIN_PORT` 8183, `KEYCLOAK_PORT` 8181, `PHOENIX_PORT` 8182, `POSTGRES_PORT` 5438 | Loopback ports |
| `WEB_HOST` | Public host name used for login redirects |
| `DATA_ROOT` | **Required.** Absolute host folder for the application's files (set by `make setup DATA_ROOT=...`) |
| `CODE_FOLDER` | Folder inside `DATA_ROOT` for code projects and Git clones (default `code`) |
| `CODE_INDEX_FOLDER` | Folder for code graph snapshots (default `code-index`) |
| `DOCS_FOLDER` | Folder for bootstrap docs (default `docs`; seeded only when empty) |
| `GENERATED_DOCS_FOLDER` | Folder for document exports (default `generated-documents`) |
| `AGENT_SESSION_MAX_SESSIONS` | Sessions running at once per agent-session-worker (default 4) |
| `AGENT_TASK_MAX` | Event-handler agent tasks running at once per agent-session-worker (whole number of at least 1, default 2; an invalid value is logged every 5 s and the task runner stays down while sessions keep working) |
| `AGENT_MAX_SUBAGENTS` | Subagents running at once in one native session turn (default 4; at most 16 helpers per turn, with expert calls). Expert calls take a slot too, and an expert's own subagents run in slots of its own, so a turn can run up to `AGENT_MAX_SUBAGENTS` x (1 + running experts) helpers at once |
| `JIRA_FOLDER` | Folder for Jira exports, the only folder RAG writes (default `jira`) |
| `CREDENTIAL_ENCRYPTION_KEY` | Encrypts all stored secrets — **back it up separately** |
| `RAG_BACKOFFICE_KEY` | Internal key between APIs and RAG |
| `APP_ADMIN_*`, `KEYCLOAK_ADMIN_PASSWORD`, `PHOENIX_*`, `*_OAUTH2_PROXY_*` | Bootstrap credentials and gateway secrets |
| `DOCKER_DNS_SEARCH` | DNS search domain so short SSH/Jira host names resolve in containers |

Model credentials are **not** in `.env`; they are configured in LLM Settings.

### Backup

Back up the Docker volumes `knowledge-agent-v3_postgres-data`,
`_keycloak-data`, `_phoenix-data`, the `DATA_ROOT` folder (plus any folder
overridden outside it), and `.env`. Store `CREDENTIAL_ENCRYPTION_KEY`
separately from database backups — without it, saved tokens and keys cannot be decrypted.

### Database migrations

`make db-migrate` (idempotent; services wait for it). Legacy databases need a one-time
`make db-adopt` — see [migrations.md](migrations.md).

### Tests

`make smoke` (stack check, no model calls), `make test` (all unit, UI and DB tests),
`make compose-validate`.

### Troubleshooting

| Symptom | Fix |
| --- | --- |
| Compose says "Run make setup" | A required secret is empty: run `make setup` |
| Smoke fails at login | `APP_ADMIN_PASSWORD` no longer matches Keycloak; update `.env` |
| "migrations missing, outdated, or dirty" | `make db-migrate`; for dirty state see migrations.md |
| "Administration unavailable" | User is not in `docs-admins`, or must sign out and in again |
| Short host names don't resolve (SSH/Jira) | Set `DOCKER_DNS_SEARCH`, recreate `admin-api` |
| Git pull fails "local changes" | Remove modified/untracked files in the clone folder |
| Port in use | Change the `*_PORT` variable and run `make up` |

The stack uses HTTP and Keycloak dev mode; configure TLS and production Keycloak
before exposing it beyond localhost.

---

## 7. Current limitations

- **Documents are searchable through a collection that holds `generated-documents` (`documents` on upgraded installs)** only after a **Refresh** or an Auto-sync.
- **Collections are refreshed manually unless Auto-sync is on** (it is off by default), so use
  **Refresh** (or **Refresh all stale**) after files change. Code is indexed as plain
  text (no code-aware chunking or line-number citations), and a collection is limited to
  20,000 files and 200 MB per sync.
- The `api-analysis` skill is in the Skills library but is not used by any documentation flow.
- Code graph tools come with the tool catalog; agents do not have them yet.
- Service inventory is operator-maintained: no live health monitoring.
- Code projects, LLM models and skills cannot be deleted (disable/leave unused).

---

## Appendix A: Agent tools and safety

Which of these a native agent actually gets is chosen on its **Tools** tab; the "Given when" column is the default.

| Tool | Given when | Safety limits |
| --- | --- | --- |
| `list_files`, `read_file`, `search_files` | Code or Documents capability on | Only selected roots plus a scratch workspace; symlinks resolved; binaries refused; reads ≤ 64 KiB; secrets redacted |
| `shell_run` | Code or Documents capability on | Runs in the separate `shell-worker` in a bubblewrap sandbox: no network, no capabilities, empty environment, sources mounted read-only, only the scratch workspace writable; 1–300 s; 32 KiB output |
| `code_graph` | Code capability on and a selected project has a generated graph | Indexed symbol, route and call queries on a disposable snapshot copy |
| `code_search` | Code capability on | Tree-sitter function lookup (Go, Python, JS, TS, TSX) |
| `ast_grep` | Code capability on | Structural code queries; 30 s timeout |
| `ssh_run` | SSH capability on and an allowed server has a key | Runs **any** command (not read-only!) under `timeout` + `setpriv --no-new-privs`; root only via `sudo -n` when allowed; host key checked only if trusted keys are saved; 32 KiB output |
| `get_server_info_basic`, `get_server_services`, `get_server_docs`, `get_service_info`, `get_service_docs`, `get_project_info`, `get_document` | Inventory tools, switched on in the **Tools** tab (off for existing agents until you switch them on) | Read-only: they read the inventory of servers, services, projects and their documents |
| Proposal tools | Discovery runs only | Exist only in a server discovery run; they record proposals that you accept or reject, and write nothing to the inventory themselves |
| MCP tools | MCP capability on | Tools of the selected, tested MCP servers |
| `search_documents`, `read_document` | Documents capability on | Search and read only the granted RAG collections; with **Answer only from documents** on, these are the agent's only tools (plus `report_not_covered`) |
| `report_not_covered` | **Answer only from documents** on | Records what is missing and marks the answer **not covered** |
| `run_subagent` | **May start subagents** on (native agents, sessions only) | Starts a helper with a subset of the agent's tools and resources and no conversation; at most 16 helpers per turn (with expert calls), `AGENT_MAX_SUBAGENTS` at once; only the credentials its configuration needs; result redacted |
| `ask_<name>` | An agent is offered as an expert (**Offer as an expert**) and not switched off for this agent (native agents, sessions only) | Runs that agent without the conversation, with its own prompt, tools, model and keys (or the resources you pass, for a per-run expert); no expert tools inside an expert; counts towards the 16 helpers; result redacted |

For a per-run agent every tool works on the run's resources only; a kind with nothing given is refused with `No … was given for this run`.

Every agent's instructions include "use only configured resources; never display
secrets", and secrets are redacted from activity, answers and traces. Skills and
Services add context only — no tools.

**Treat SSH access with care**: an agent with SSH enabled can change the server.
Use a dedicated low-privilege user, keep **Allow sudo to root** off unless needed,
and always save trusted host keys.

---

## Appendix B: Where older docs disagree with the code

| Document | Claim | Actual behavior |
| --- | --- | --- |
| README.md | Agent Chat/Test returns `TODO` | Agent **Test** really runs models |
| docs/documentation.md | One form with two scopes; generations page is history | Workflows: create per service/environment, then run Phases 1–4 and additional documents |
| docs/documentation.md | 16,384-token output limit for all runs | Not applied to per-endpoint API analysis |
| docs/code-processing.md | Choose a service's "source folders" | You choose code projects; folders are mapped in Code projects |
| docs/architecture.md | Worker serializes jobs per project | The repository worker processes one job at a time overall |
| docs/runtime-verification.md | Admin pages `/platform`, `/github-projects` | `/platform` doesn't exist; `/github-projects` redirects to `/code-projects` |
| Skills (migration 0059) | `api-analysis` skill for API documentation | Not wired into any documentation flow |
