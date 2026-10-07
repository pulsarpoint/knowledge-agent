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
  - [4.6 Environments](#46-environments)
  - [4.7 Services](#47-services)
  - [4.8 Jira](#48-jira)
  - [4.9 Skills](#49-skills)
  - [4.10 MCP servers](#410-mcp-servers)
  - [4.11 Agents](#411-agents)
  - [4.12 Multi-agent board](#412-multi-agent-board)
  - [4.13 Generate documents](#413-generate-documents)
  - [4.14 Documents and documentation prompts](#414-documents-and-documentation-prompts)
  - [4.15 Storage](#415-storage)
  - [4.16 Agent runs](#416-agent-runs)
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
- **Generate documents with a Claude Code agent**: pick an agent and what to document
  (code projects, environments, services, servers), follow the session and save the draft.
- **Configure and test AI agents** with prompts, skills, documents, code, SSH servers
  and MCP tools, and run them interactively (agent **Test** chat) or as a team on the
  **multi-agent board**.
- **Import Jira tickets** into the knowledge base as Markdown.

Generated documents are searchable through a collection that holds `generated-documents` (`documents` on upgraded installs), after a **Refresh** or with Auto-sync in RAG → Collections
(see [4.3](#43-rag-collections-processing-settings-search)).

### Architecture at a glance

```
Browser ──► admin-oauth2-proxy :8183 (docs-admins only) ──► admin-web (nginx, Admin UI) ──► admin-api
                                                                                    │
     admin-api ──► rag, PostgreSQL, shell-worker (sandboxed bash), LLM providers, SSH hosts, Jira, MCP servers
     workers:  rag-worker (indexing) · repository-worker (git clone/pull, code graph)
               claude-code-worker (Generate documents sessions)
```

`claude-code-worker` runs the Claude Agent SDK with its bundled Claude Code CLI. It reads
`DATA_ROOT` read-only, needs network access to Bedrock or the Anthropic API, and its home
volume `claude-code-home` is its only writable mount. It runs as user `claude` (uid 1001),
so the files under `DATA_ROOT` that sessions should read must be readable by uid 1001.

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
8. **Docs → Generate documents** → **New**: choose a Claude Code agent and what to document → **Generate**.
9. **Agents** → create agents, assign a model, enable capabilities, test them.
10. **Multi-agent board** → give the Coordinator a model and run team tasks.

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
| Administration | Storage · Skills · MCP servers · Agent runs |
| Agents | All agents · Multi-agent board |
| LLM Settings | Models |
| Jira | Tickets · Settings |
| Environments | Environments · Services · Servers |
| Code | Projects · Code graphs |
| Docs | Documents · Prompts · Generate documents · Folders |
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

> **Compatibility rule.** Agents (other than the Coordinator) and documentation
> generation require a "code-compatible" model: OpenAI **Responses** API, Anthropic,
> or Bedrock `anthropic.claude*`. OpenAI **Chat Completions** models can only be used
> by the Coordinator.

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
prerequisite for code documentation phases ([4.13](#413-documentation-generation)).

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

`/agents` lists agents with **Agent type**, capabilities, model, active system prompt and status.

**Create an agent** with **New agent** (the only way to create agents). It opens the wizard directly. The review **General** tab starts with **Agent type** (Native or Claude Code):

- **Generate proposal** — describe the role, choose a generator model, and the model
  proposes the name, prompt, documents, code projects, SSH, skills, MCP and services.
- **Start blank** — skip generation (no model needed) and fill in the same review tabs
  yourself.

Review and edit the tabs, then **Create agent** (enabled once name and system prompt are
filled in). The agent model is optional at this point — an agent without a model is
saved but runs only after you choose one on its General tab.

**Agent detail tabs** (`/agents/:id`):

| Tab | What you do there |
| --- | --- |
| **General** | **Agent type** (Native or Claude Code; switching keeps every setting), name, purpose, when-to-use, **Enable agent**; the model filtered by type and **Model connection** (required to run); additional instructions; and the **Claude Code tools** card (Read, Grep, Glob, Bash switches) for Claude Code agents |
| **Skills** | Enable and pick skills (max 30) |
| **Documents** | Enable and pick RAG collections, or "All collections" (whole collections only; there are no subfolder grants); **Answer only from documents** switch (cite every claim and report questions the documents do not cover) |
| **Code projects** | Enable and pick code projects, or "All code projects" |
| **SSH** | Choose allowed servers; per-server SSH overrides (user, key, host keys, sudo) and per-server instructions/skills/MCP |
| **MCP** | Pick tested MCP servers (and their tools) |
| **Services** | Pick services whose descriptions are added to the agent's context |
| **Prompts** | System prompt versions: new draft, edit draft, **Set active**, delete inactive versions |
| **Versions** | Snapshot the whole configuration as a version; **Test vN**, **Copy as draft**, **Activate vN** |
| **Test** | Native: chat with the agent. Claude Code: **Start a session** opens **Docs → Generate documents → New** with this agent |

> **Working configuration vs. active version.** Once an agent has an active
> configuration version, edits in the tabs change the *working configuration* only.
> Create a draft in **Versions** and **Activate** it before new runs use the changes.
> Existing test sessions stay pinned to the version they started with.

**Test tab.** Type a task and **Run task** (then **Send message** to continue the
session). Replies stream in; each has **View activity** (tool calls with arguments and
output). Other controls: session **History**, **Show config** (the exact pinned
configuration, secrets as references), **Close session**, **New session**, **Cancel
run**, and token usage. **Add feedback** → **Suggest improvements** runs a reviewer
that proposes prompt/instruction changes you can copy or **Save as new version**.

Limits: task ≤ 20,000 characters, 300 s and 20 turns per run, sessions expire after
1 hour idle, one running message per session. A run is blocked if the agent is
disabled, has no enabled model, or other tabs have unsaved changes.

**Upgrading.** Migration 0060 turns **Answer only from documents** on and Code
(repositories) off for the Search agent; its down migration does not turn Code back
on. Settings changes touch only the agent's working copy: if the Search agent has an
active configuration version, runs keep using that version's snapshot until you save
and activate a new version.

**Claude Code agents.** Claude Code agents are configured exactly like native agents (same tabs, prompt versions, versions). They need a Claude model (`Choose a Claude model for Claude Code agents`). Their sessions use the active prompt, instructions and enabled skills as the system prompt, the code projects as extra read-only working folders and the Claude Code tools switches. Documents, code search and code graph, SSH, MCP and services are saved but not given to Claude Code yet (the tabs say so). Claude Code agents never run in native test chats or on the multi-agent board. Agents created before this release got the built-in documentation prompt as prompt version 1.

Agents with sessions or board history cannot be removed — disable them instead.
See [Appendix A](#appendix-a-agent-tools-and-safety) for the tools an agent receives.

### 4.12 Multi-agent board

`/agent-board` — give one task to a **Coordinator** agent, which delegates subtasks
to selected specialist agents and then writes a final summary.

Prerequisites: the built-in Coordinator agent must have an enabled model (any
tool-capable chat model). Specialists need to be enabled, with a model and an active prompt.

1. **New task** → enter the main task, tick 1–10 subagents → **Start task**.
2. Watch the phase: *Planning subtasks → Subagents working → Reviewing results → Finished*.
3. Follow live **Coordinator activity**, and the kanban columns *Queued / Running /
   Results*; **View work** opens a subtask's activity and result. **View trace** opens
   Phoenix if `PHOENIX_PUBLIC_URL` is set.
4. Read the final answer; **Stop task** or **Use task again** as needed.

Coordinator settings (Coordinator's General tab): max model turns (1–30), max subagent
calls (1–50), task timeout (30–3600 s), notes, and the final-summary prompt. Up to 3
main tasks run concurrently, with at most 30 unfinished tasks.

### 4.13 Generate documents

**Docs → Generate documents** (`/documentation/generations`) lists Claude Code sessions in which an agent writes one
document: status, agent, tokens and the saved document. **New** opens the form. Sessions continue when you leave the page
and close after 24 idle hours (a session idle for 5 minutes is paused and resumes on your next message).

**Prerequisites**

- An enabled Claude Code agent with a Claude model (Agents → Agent type: Claude Code).
- Claude Code needs at least one working folder: code projects with folders, or environments or services linked to code
  projects, or an agent with code projects of its own.

**The form**

- **Agent**: an enabled Claude Code agent (from an agent's Test tab it is preselected).
- **What to document**: tick **Code**, **Environment**, **Services** and/or **Servers**; each shows its picker. Pick at
  least one item.
- **Instructions** (optional): extra instructions for this run.
- **Document name** and **folder**: the folder defaults to `<first code project>-docs`, or `docs`. Folder names are
  lowercase (`Payments` and `payments` would be the same folder on macOS): each segment uses lowercase letters, digits,
  '.', '_' and '-', does not start with '.'; `node_modules` and a first segment `services` are not allowed; at most 200
  characters. The name needs ASCII letters or digits (the file name is made from them).

There is no model or prompt field: the agent runs with its own model, prompt, instructions, skills and Claude Code tools.
Put a documentation prompt in the agent (Docs → Prompts holds the earlier prompts to copy from).

**What the agent receives.** Its system prompt (its own prompt, instructions and skills), then one first message listing
what to document, as a user could have typed it:

```
Document the following.

## Code
- Payments API — /work/data/code/payments-api

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

**Working folders** (read-only): the chosen code projects, then the code projects linked to the chosen environments and
services, then the agent's own code projects. The first is the working directory.

**Servers are text only.** The form lists name, address and description; there is no SSH from this form. SSH through the
agent comes with the tool gateway.

**The session page.** Activity and Draft sit side by side, each with its own scroll, and the message box is always
visible. Activity follows new events at the bottom; after you scroll up it stops following and shows **Jump to latest**.
Opening a session loads the latest 100 events; earlier pages load as you scroll up until **Beginning of the session**.
It also shows tokens in/out/cached and a status: Starting, Working, Waiting for you, Stopped, Failed or Finished.

- **Send message**: while the agent is working, the message is queued and delivered when the current response finishes.
  After a turn it starts a new turn in the same conversation.
- **Stop**: interrupts the turn and drops queued messages.
- **Draft**: each finished turn's final answer, as Markdown.
- **Save document**: a versioned document in the folder, linked to the code projects and exported to
  `GENERATED_DOCS_FOLDER/<folder>/<slug>/README.md`. The same name in the same folder asks **Save a new version?**; the
  same name in another folder is a separate document. Nothing is saved automatically.
- **Close session**.

Every turn is an agent run in **Activity → Agent runs** (`cc:<session>:<turn>`).

Errors: "The agent has no enabled Claude model. Choose one on its General tab."; "Choose at least one code project,
environment, service or server to document."; "Claude Code needs at least one working folder…"; a provider error, such as
a missing Bedrock inference profile, shows the provider's reason without credentials plus a hint; "Waiting for the Claude
Code worker…" when the worker is not running; a folder that no longer exists is refused when starting.

Security: the model key is passed only to the session's Claude Code process. Every enabled Claude connection's key is
redacted from stored events, drafts and error messages. Each running session's Claude Code process runs as its own user,
so one session cannot read another session's process or files, and the agent can reach only the internet and the database
host, not the other services. Residual risk: Bash in the session can read the session's own key from its Claude Code
process, so use a key with limited scope and spend.

**Upgrading.** Migration 0073 removed the service, whole-environment and code-project generation, its workflows, history
and run records. Saved documents, their versions and exported files stay, and documents written by service phases keep
exporting into `services/<service>/<phase>/`. Old `/documentation/sessions` links redirect. Bookmarks of old generations
(`/documentation/generations/<id>`) open a page that shows "Session not found".

### 4.14 Documents and documentation prompts

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

**Prompts** (`/documentation/prompts`) — a library of versioned documentation prompts. Nothing generates with them
directly: copy one into a documentation agent's prompt. Saving changed text creates a draft version; **Activate vN** makes
it the active one; inactive versions can be deleted.

### 4.15 Storage

`/storage` — read-only overview of the data folder (`DATA_ROOT`) and its five folders: source code
(`CODE_FOLDER`), code graphs (`CODE_INDEX_FOLDER`), Jira exports (`JIRA_FOLDER`), document
exports (`GENERATED_DOCS_FOLDER`) and bootstrap docs (`DOCS_FOLDER`). Each shows host and
container paths, availability, and a file browser with text preview.

### 4.16 Agent runs

`/activity/runs` — a complete record of every agent run: agent Test
conversations, multi-agent board tasks (coordinator, every specialist and
the summary in one record) and Generate documents sessions. Each record shows, in order:

- the full instructions sent to the model and the input (with conversation history);
- the model's reasoning when the provider returns it (otherwise "Reasoning not returned
  by the provider.");
- every tool call with its full arguments and full output (SSH, shell, code, documents,
  MCP…);
- messages, token usage per model call, errors, and the final status.

Known secrets are replaced with `[redacted]`; very large values are cut at 1,000,000
characters. The run type filter offers **Agent test**, **Board task** and **Claude Code** (Generate documents turns); you can also filter by user. **Full record** links lead here from each reply in
an agent's Test tab, from a board task, and from each Generate documents session turn.
Records are kept until deleted; only administrators can see them.

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
4. **Agents**: create a Claude Code agent with a Claude model and a documentation prompt.
5. **Docs → Generate documents → New**: choose the agent, tick **Services** and pick the service → **Generate**.
6. Follow the session, send messages such as "shorten the API section", then **Save document**.
7. Make the documents searchable through a collection that holds `generated-documents` (`documents` on upgraded installs); **RAG → Collections** → **Refresh** it.

### C. Document deployments and infrastructure

1. **Servers**: register hosts. **Environments**: create `production` with its servers and services.
2. **Services**: add deployments (server + environment + container/unit + port + log path).
3. **Generate documents → New**: tick **Environment** and pick it (or tick **Servers**), add instructions about
   deployments → **Generate**.
4. Servers are described as text until agents get SSH tools; the agent does not connect to them.

### D. Build and test an operations agent

1. **Agents → New agent → Generic agent** (generate a proposal or **Start blank**).
2. **General**: assign a model; **SSH**: allow servers; **Code projects** / **Documents**: select resources.
3. **Test**: run a task such as "Check disk usage on the app servers"; inspect **View activity**.
4. Use **Add feedback → Suggest improvements**, then **Versions → Save draft → Activate**.

### E. Run a team task

1. Open the **Coordinator** agent → assign a model; adjust coordinator settings.
2. **Multi-agent board → New task**, pick specialists, **Start task**, follow the board.

### F. Bring Jira knowledge into RAG

**Jira Settings** → save and test → **Tickets** → fetch with JQL → select → **Export
selected** → make sure a collection holds `jira` (RAG → Collections → Add
folder) → **Refresh** it or let Auto-sync pick it up.

### G. Make one code project's documentation searchable

1. **Generate documents** → **New** → **Code**: the project → **Generate** → **Save document**.
2. **Docs → Folders** → **Index in RAG → Collections** → **Create collection** (the folder
   is selected; the sync starts at once).
3. Grant the collection to agents in **Agents**.

### H. Write documentation interactively with Claude Code

1. **Agents → New agent**, **Agent type: Claude Code**, choose a Claude model and write the
   system prompt → **Create agent**.
2. **Docs → Generate documents → New** (or the agent's **Test** tab → **Start a session**) → choose what to document → **Generate**.
3. Follow the stream and send a message such as "shorten the API section".
4. **Save document**.
5. **Docs → Folders** → **Index in RAG → Collections**.

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
| `CLAUDE_CODE_MAX_SESSIONS` | Running Claude Code processes per `claude-code-worker` (default 4) |
| `JIRA_FOLDER` | Folder for Jira exports, the only folder RAG writes (default `jira`) |
| `CREDENTIAL_ENCRYPTION_KEY` | Encrypts all stored secrets — **back it up separately** |
| `RAG_BACKOFFICE_KEY` | Internal key between APIs and RAG |
| `APP_ADMIN_*`, `KEYCLOAK_ADMIN_PASSWORD`, `PHOENIX_*`, `*_OAUTH2_PROXY_*` | Bootstrap credentials and gateway secrets |
| `PHOENIX_PUBLIC_URL` | Enables "View trace" links on the agent board |
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
- Generate documents runs Claude Code agents only (native agents come with the session API) and describes servers as text (no SSH from the form).

---

## Appendix A: Agent tools and safety

| Tool | Given when | Safety limits |
| --- | --- | --- |
| `list_files`, `read_file`, `search_files` | Code or Documents capability on | Only selected roots plus a scratch workspace; symlinks resolved; binaries refused; reads ≤ 64 KiB; secrets redacted |
| `shell_run` | Code or Documents capability on | Runs in the separate `shell-worker` in a bubblewrap sandbox: no network, no capabilities, empty environment, sources mounted read-only, only the scratch workspace writable; 1–300 s; 32 KiB output |
| `code_search` | Code capability on | Tree-sitter function lookup (Go, Python, JS, TS, TSX) |
| `ast_grep` | Code capability on | Structural code queries; 30 s timeout |
| `ssh_run` | SSH capability on and an allowed server has a key | Runs **any** command (not read-only!) under `timeout` + `setpriv --no-new-privs`; root only via `sudo -n` when allowed; host key checked only if trusted keys are saved; 32 KiB output |
| MCP tools | MCP capability on | Tools of the selected, tested MCP servers |
| `search_documents`, `read_document` | Documents capability on | Search and read only the granted RAG collections; with **Answer only from documents** on, these are the agent's only tools (plus `report_not_covered`) |
| `report_not_covered` | **Answer only from documents** on | Records what is missing and marks the answer **not covered** |

Every agent's instructions include "use only configured resources; never display
secrets", and secrets are redacted from activity, answers and traces. Skills and
Services add context only — no tools.

**Claude Code agents** use the Read, Grep, Glob and Bash tools switched on in their **Claude Code tools** card (all on by default; off tools are also disallowed; catalog tools come in a later release), as user `claude` (uid 1001) in
`claude-code-worker`, with `DATA_ROOT` read-only and `setting_sources=[]` (no `.claude/`
settings, hooks or CLAUDE.md from the folders). The model key exists only in the CLI
environment.

**Treat SSH access with care**: an agent with SSH enabled can change the server.
Use a dedicated low-privilege user, keep **Allow sudo to root** off unless needed,
and always save trusted host keys.

---

## Appendix B: Where older docs disagree with the code

| Document | Claim | Actual behavior |
| --- | --- | --- |
| README.md | Agent Chat/Test returns `TODO` | Agent **Test** and the **multi-agent board** really run models |
| docs/documentation.md | One form with two scopes; generations page is history | Workflows: create per service/environment, then run Phases 1–4 and additional documents |
| docs/documentation.md | 16,384-token output limit for all runs | Not applied to per-endpoint API analysis |
| docs/code-processing.md | Choose a service's "source folders" | You choose code projects; folders are mapped in Code projects |
| docs/architecture.md | Worker serializes jobs per project | The repository worker processes one job at a time overall |
| docs/runtime-verification.md | Admin pages `/platform`, `/github-projects` | `/platform` doesn't exist; `/github-projects` redirects to `/code-projects` |
| Skills (migration 0059) | `api-analysis` skill for API documentation | Not wired into any documentation flow |
