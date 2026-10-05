# Knowledge Agent v3 — User Guide

This guide explains how the application is used, from first start-up to day-to-day
work. It describes the behavior of the **current code** (including uncommitted work
on documentation workflows, code graphs and the sandboxed shell worker), not the
intended design. Where existing documentation in `docs/` disagrees with the code,
the differences are listed in [Appendix B](#appendix-b-where-older-docs-disagree-with-the-code).

- [1. What the application is](#1-what-the-application-is)
- [2. Install, start and sign in](#2-install-start-and-sign-in)
- [3. Recommended first-time setup order](#3-recommended-first-time-setup-order)
- [4. The Reader application (port 8180)](#4-the-reader-application-port-8180)
- [5. The Administration application (port 8183)](#5-the-administration-application-port-8183)
  - [5.1 Navigation](#51-navigation)
  - [5.2 LLM Settings](#52-llm-settings)
  - [5.3 RAG: collections, processing, settings, search](#53-rag-collections-processing-settings-search)
  - [5.4 Code projects](#54-code-projects)
  - [5.5 Servers](#55-servers)
  - [5.6 Environments](#56-environments)
  - [5.7 Services](#57-services)
  - [5.8 Jira](#58-jira)
  - [5.9 Skills](#59-skills)
  - [5.10 MCP servers](#510-mcp-servers)
  - [5.11 Agents](#511-agents)
  - [5.12 Multi-agent board](#512-multi-agent-board)
  - [5.13 Documentation generation](#513-documentation-generation)
  - [5.14 Documents and documentation prompts](#514-documents-and-documentation-prompts)
  - [5.15 Storage](#515-storage)
  - [5.16 Activity](#516-activity)
- [6. End-to-end workflows](#6-end-to-end-workflows)
- [7. Operations](#7-operations)
- [8. Current limitations](#8-current-limitations)
- [Appendix A: Agent tools and safety](#appendix-a-agent-tools-and-safety)
- [Appendix B: Where older docs disagree with the code](#appendix-b-where-older-docs-disagree-with-the-code)

---

## 1. What the application is

Knowledge Agent v3 is a self-hosted platform for building a knowledge base about
your software systems and running AI agents over it. It consists of two separate web
applications sharing one Keycloak realm and one PostgreSQL database:

| Application | URL | Who | Purpose |
| --- | --- | --- | --- |
| **Reader** | http://localhost:8180 | Any user in the Keycloak `docs` realm | Browse and search the documentation knowledge base |
| **Administration** | http://localhost:8183 | Members of the `docs-admins` group only | Configure models, content, code, inventory, agents; generate documentation |
| Keycloak console | http://localhost:8181 | Keycloak master `admin` | Manage users and groups |
| Phoenix | http://localhost:8182 | `admin@localhost` | Inspect agent traces |

What you can actually do today:

- **Curate a searchable knowledge base** (RAG): review **collections**, each holding
  paths under `DATA_ROOT` that sources add (documents, Jira exports or source code),
  index them into a full-text (and optionally vector) index with **Refresh**, and
  search them — from the admin RAG pages, agents and the Reader.
- **Map source code**: register local folders or clone Git repositories, keep them
  pulled on a schedule, and build a **code graph** (symbols, relationships, routes).
- **Keep an operational inventory**: services, their code, environments, servers,
  deployments and service-to-service dependencies.
- **Generate documentation with an LLM** for a service or an environment: application
  overview, per-endpoint API documentation, messaging documentation, code analysis,
  D2 diagrams and deployment/infrastructure documents (via SSH inspection).
- **Configure and test AI agents** with prompts, skills, documents, code, SSH servers
  and MCP tools, and run them interactively (agent **Test** chat) or as a team on the
  **multi-agent board**.
- **Import Jira tickets** into the knowledge base as Markdown.

Generated documents are searchable by the Reader and agents through the `documents`
collection (the `generated-documents` folder) after a **Refresh** in RAG → Collections
(see [5.3](#53-rag-collections-processing-settings-search)).

### Architecture at a glance

```
Browser ──► oauth2-proxy :8180 ──► web (nginx, Reader UI) ──► docs-wiki API ──► rag API ──► PostgreSQL (+pgvector)
Browser ──► admin-oauth2-proxy :8183 (docs-admins only) ──► admin-web (nginx, Admin UI) ──► admin-api
                                                                                    │
     admin-api ──► rag, PostgreSQL, shell-worker (sandboxed bash), LLM providers, SSH hosts, Jira, MCP servers
     workers:  rag-worker (indexing) · repository-worker (git clone/pull, code graph) · documentation-worker (doc generation)
```

All ports bind to `127.0.0.1` only. Internal services have no host ports.

---

## 2. Install, start and sign in

Requirements: Docker with Compose, Python 3.10+ (for the setup script). No model
credentials are needed to start.

```sh
make up          # generate .env secrets, build images, migrate DB, start everything
make smoke       # verify logins, isolation, search, DB migrations, Phoenix (no model calls)
make admin-user  # create an extra admin "local-admin" (password in .local/users/local-admin.json)
```

- The first time, choose where the application keeps its files:
  `make setup DATA_ROOT=/opt/knowledge-agent` (an absolute path; create it with `sudo`
  and `chown` it to your user if needed). `make up` refuses to start without it.
- `make up` runs `scripts/setup.py`, which creates `.env` (mode 0600) from
  `.env.example`, fills empty secrets with random values (never overwriting existing
  ones), records `DATA_ROOT`, and creates `code/`, `code-index/`, `rag-data/`,
  `generated-documents/` and `docs/` inside it.
- The bootstrap admin is `v3-admin` with the password `APP_ADMIN_PASSWORD` from `.env`.
  Change it in Keycloak afterwards, not in `.env`.
- Additional admins: `make admin-user ADMIN_USERNAME=alice` (optional `ADMIN_EMAIL`).
  Re-running keeps existing passwords; reset with
  `python3 scripts/admin_user.py --username alice --reset-password`.
- **Reader-only users** and group changes are managed in the Keycloak console
  (http://localhost:8181, user `admin`, password `KEYCLOAK_ADMIN_PASSWORD`), realm `docs`.
  Any user in the realm can open the Reader; only `docs-admins` members can open
  Administration. After adding someone to a group they must sign out and back in.

Stop with `make down` (data volumes are preserved). `make status` and `make logs`
show state and logs.

---

## 3. Recommended first-time setup order

Most features depend on earlier ones. A sensible order in the Administration app:

1. **LLM Settings** → add a chat model and **Test connection**. Optionally add an
   embeddings model (AWS Bedrock Titan v2) for vector search.
2. **RAG → Settings** → select the embeddings model if you added one.
3. Let a source add its path (Docs → Documents → `documents`; Jira export → its chosen collection; code project **Searchable** → its chosen collection), then **RAG → Collections** → **Refresh**.
4. **Code projects** → map local folders or clone Git repositories; **Generate graph**.
5. **Servers** → register hosts; configure **SSH settings** if agents or deployment
   documentation should inspect them.
6. **Environments** → group servers and services (e.g. `production`, `staging`).
7. **Services** → describe applications, link code projects, add deployments and dependencies.
8. **Documentation → Generate documents** → create a workflow per service and run its phases.
9. **Agents** → create agents, assign a model, enable capabilities, test them.
10. **Multi-agent board** → give the Coordinator a model and run team tasks.

---

## 4. The Reader application (port 8180)

Sign in through Keycloak. The Reader is read-only: there is no editing and no admin
navigation (admin paths return 404).

**Where its content comes from.** The Reader shows the **RAG workspace** (the
collections managed in Admin → RAG → Collections), not the bootstrap `DATA_ROOT/docs`. With a single
configured app (the default, named `docs`), `/` redirects straight to `/docs`.

### Layout

- **Sidebar**: app name (a switcher if several apps exist), links **Search**, **Chat**,
  **Gaps**, then a table of contents grouped by **collection**, with file names as
  titles. Collapse with the header button or **Ctrl/Cmd+B**; the state is remembered.
- **Footer**: light/dark theme toggle.
- **Breadcrumbs**: app › collection › document.

### Reading a document — `/docs/doc/<path>`

Markdown is rendered with GitHub-flavored syntax and code highlighting. Code blocks
have a **Copy** button; Mermaid diagrams open full-screen on click with zoom controls.
The app landing page `/docs` renders `index.md` from the workspace root, so if your RAG root
has no `index.md` it shows an error. Add one to give readers a home page.

### Search — `/docs/search`

Type in the box; results update as you type (150 ms debounce). The Reader always
uses **hybrid** search across **all collections** (a collection uses keyword search
when its embeddings are off; the admin Search page shows "Keyword search used for: ..."). Each result shows the file (link), its collection and the
matching text chunk. There are no filters here — use Admin → RAG → Search for
collection scope and search-mode control. Errors show as "no results".

### Chat — `/docs/chat`

Ask a question and the assistant answers only from the knowledge base (the RAG
collections). **Advanced** lets you narrow the chat to chosen collections (all by
default); it can only narrow what the agent is allowed to search. While it works you see activity lines such as "searching: …" and
"reading: …"; the answer appears when it is complete. Claims carry `[n]` citations
that link to the cited documents. Enter sends; Shift+Enter adds a new line. Which
agent answers is chosen by an administrator (see [5.11](#511-agents)).

If the documents do not cover a question, the answer says what is missing, shows a
**not covered** badge, and the question is recorded on the **Gaps** page.

Conversations expire after 7 days idle (a message in an expired one shows "This
conversation has ended. Start a new one."). **Delete** removes a conversation from
your list only; administrators can still see it in Activity. Only one answer runs at a
time ("Wait for the current answer."). If an administrator has not configured the
assistant you see "The assistant isn't configured yet. Ask an administrator.";
"The assistant's configuration isn't allowed in the Reader." and "Chat is temporarily
unavailable." mean the chosen agent is unsuitable or the service is down.

### Gaps — `/docs/gaps`

Lists questions the chat agent could not answer from the docs. A new gap is recorded
whenever a Reader answer is marked **not covered** (or "No gaps recorded").

---

## 5. The Administration application (port 8183)

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

### 5.1 Navigation

| Group | Items |
| --- | --- |
| Administration | Storage · Code projects · Environments · Services · Skills · MCP servers · Servers · Activity |
| Agents | All agents · Multi-agent board |
| LLM Settings | Models |
| Jira | Tickets · Settings |
| Docs | Documents · Prompts · Generate documents |
| RAG | Collections · Processing jobs · Settings · Search |

Footer: theme toggle and **Sign out**. Legacy URLs `/docs/*` and `/github-projects`
redirect to their current pages.

### 5.2 LLM Settings

`/llm-settings` — named model connections used by agents, documentation generation
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

### 5.3 RAG: collections, processing, settings, search

A **collection** is a named search index with its own settings and one or more paths
(folders under `DATA_ROOT`). RAG indexes the files where they already are — nothing is
copied. Everything is by whole collection: agents are granted collections, not subfolders.
Sources add and remove their own paths; collections are not created or filled by hand here.

**Workflow**

1. A source adds its path to a collection: **Docs → Documents** (`documents`), a **Jira
   export** ([5.8](#58-jira), default `jira`) or a **Searchable** code project
   ([5.4](#54-code-projects), default the project's slug).
2. Admin → **RAG → Collections** → **Refresh** the collection.
3. When embeddings are on, watch **Embedded** go from `0 / N` to `N / N`.

**Collections** (`/rag/collections`)

- The table shows name and description, paths with their sources (Documents, Jira, Code
  project linked to its page, or Unowned), files, embedded chunks, last synced, status and
  a **Refresh** button.
- **Status**: *Up to date*; *Needs refresh* (never synced, a folder changed after the last
  sync, a path was added or removed, or the settings changed since); *Syncing*; *Failed*
  (the previous index stays); *Folder missing* (any path is missing; Refresh is refused and
  the index is kept).
- **Refresh all stale** queues every collection that needs it. Nothing refreshes
  automatically.
- **Edit collection** shows search settings only: description, search mode (Full text /
  Embedding / Hybrid), embeddings on/off, include and exclude patterns (default include
  `**/*.md`, `**/*.markdown`, `**/*.txt`; matched relative to each path), chunk size
  (default 2000) and overlap (default 200). The name cannot be changed. Changing settings
  makes the collection need refresh. There is no New collection.
- **Collection detail**: indexed files with full paths, chunk count and embedding state,
  and a file's chunks. There is no upload or edit. **Detach** removes an unowned path and
  its indexed files (the files stay on disk).
- **Remove** appears only when no source owns a path; it removes the index rows and agent
  grants and keeps the files on disk. Otherwise remove the sources first (`Remove this
  collection's sources first: …`).
- Skipped and counted (never fatal): symbolic links, special and unreadable files, invalid
  names, binary files and files over the maximum size; `.git`, `node_modules` and `.env*`
  are always ignored. A sync fails if a collection has more than 20,000 files or 200 MB —
  narrow the include patterns. Code is indexed as plain text.

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

### 5.4 Code projects

`/code-projects` — the source code the platform can analyze. Files live under
`DATA_ROOT/code` on the host (or `CODE_PATH`), mounted read-only as `/work/code`
(only the repository worker writes there).

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
**View graph** opens an explorer (files → symbols, filter, paging). The graph is a
prerequisite for code documentation phases ([5.13](#513-documentation-generation)).

**Searchable** — a switch plus a collection name. Turning it on adds all mapped folders
(`code/<path>`) to the collection, creating it if missing. The name defaults to the
project's slug and an existing collection can be picked; changing it moves the project's
paths (**Move to another collection**). Turning it off, or archiving the project, removes
its paths from every collection. Editing the mapped folders adds and removes paths to
match. If RAG is unavailable an error shows on the page with **Retry**; the project itself
is never rolled back. If RAG is unavailable, archiving a searchable project fails with an error and can simply be retried (the project stays listed). **Refresh** the collection in RAG → Collections afterwards.

Projects cannot be edited while a job runs, and there is no delete action.

### 5.5 Servers

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

### 5.6 Environments

`/environments` — named groups of services and servers (e.g. `prod`, `staging`).
Each asset can belong to several environments.

- **Environment list** tab: filter, **Graph**, **Documents**, Edit, Delete; click a
  name to see its members.
- **Dependency graph** tab: choose an environment to see the dependencies between its
  services; click a node for service details.
- Removing a service/server that a deployment in this environment uses is refused —
  update the deployment in Services first. Deleting an environment keeps its name as a
  plain label on former deployments.

### 5.7 Services

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

### 5.8 Jira

**Settings** (`/jira/settings`) — Jira Data Center URL (include context path such as
`/jira`; use `host.docker.internal` for a Jira on your machine), username, personal
access token (encrypted). Save, then **Test connection** (checks the user identity
and search permission).

**Tickets** (`/jira/tickets`)

1. Enter a **JQL** query (default `ORDER BY updated DESC, key DESC`) and the number of
   tickets (1–200), then **Fetch tickets**. Results are stored locally.
2. Filter by text, project and status; tick tickets.
3. **Export selected** into a **Target collection**: any valid name or an existing one,
   default `jira`. The export adds `rag-data/jira` to that collection (moving it from a
   previous one) and writes each ticket to `rag-data/jira/<KEY>.md` with metadata,
   description and comments. Keep the page open until it finishes; failed tickets stay
   selected. A RAG error fails the export with its message.
4. Follow the link in the notice to the collection and **Refresh** it to make the tickets
   searchable.

For testing without a real Jira: `make jira-mock-up`, then URL
`http://jira-mock:8080/jira`, user `mock-admin`, token `jira-mock-token`
(75 demo tickets). Stop with `make jira-mock-down`.

### 5.9 Skills

`/skills` — a library of reusable Markdown instructions that agents can be given.
Fields: name (lowercase-with-dashes, unique), description, instructions (Markdown),
enabled. Search and filter by status; toggle enabled inline. Skills are context only
(injected into the agent's instructions, never executed) and cannot be deleted — disable them.

### 5.10 MCP servers

`/mcp-servers` — external tool servers agents can call. Only the **Streamable HTTP**
transport is supported. Fields: name, description, HTTP(S) endpoint (use
`host.docker.internal` instead of `localhost`), optional bearer token (encrypted),
enabled.

**Test connection** discovers the server's tools (25 s limit). A server can only be
selected for an agent after it is enabled, tested successfully and exposes at least
one tool. Changing the URL clears discovered tools and requires re-entering the token.

### 5.11 Agents

`/agents` lists agents with capabilities, model, active system prompt and status.

**Create an agent** with the **New Agent Wizard** (the only way to create agents):

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
| **General** | Name, purpose, when-to-use, **Enable agent**; **Model connection** (required to run); additional instructions |
| **Skills** | Enable and pick skills (max 30) |
| **Documents** | Enable and pick RAG collections, or "All collections" (whole collections only; there are no subfolder grants); **Answer only from documents** switch (cite every claim and report questions the documents do not cover) |
| **Code projects** | Enable and pick code projects, or "All code projects" |
| **SSH** | Choose allowed servers; per-server SSH overrides (user, key, host keys, sudo) and per-server instructions/skills/MCP |
| **MCP** | Pick tested MCP servers (and their tools) |
| **Services** | Pick services whose descriptions are added to the agent's context |
| **Prompts** | System prompt versions: new draft, edit draft, **Set active**, delete inactive versions |
| **Versions** | Snapshot the whole configuration as a version; **Test vN**, **Copy as draft**, **Activate vN** |
| **Test** | Chat with the agent |

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

**Reader assistant.** A card at the top of `/agents` chooses which agent answers
Reader chat (default: the Search agent). Agents that are disabled, have no enabled
model, use SSH, MCP tools or code projects, or have Documents off, or have **Answer
only from documents** off, are disabled in the list with the reason, and the card
warns when the current Reader agent can't answer. The Reader agent cannot be deleted.

**Upgrading.** Migration 0060 turns **Answer only from documents** on and Code
(repositories) off for the Search agent; its down migration does not turn Code back
on. Settings changes touch only the agent's working copy: if the Search agent (or
whichever agent answers the Reader) has an active configuration version, the Reader
keeps using that version's snapshot. Save and activate a new version so the Reader
uses the new settings — until then the card lists "active version vN predates these
settings" and the Reader replies "The assistant's configuration isn't allowed in the
Reader."

Agents with sessions or board history cannot be removed — disable them instead.
See [Appendix A](#appendix-a-agent-tools-and-safety) for the tools an agent receives.

### 5.12 Multi-agent board

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

### 5.13 Documentation generation

**Docs → Generate documents** (`/documentation/generations`) produces documentation
from your code, inventory and servers using an LLM. Work is organized in
**workflows**: one per service (or per environment).

**Prerequisites**

- A code-compatible, enabled chat model in LLM Settings.
- A service (with linked code projects), or an environment.
- For code phases: a **code graph** for every selected code project (Phase 1).
- For deployment/infrastructure documents: servers with SSH configured.
- Active documentation prompts (seeded automatically; edit under **Docs → Prompts**).

No agent from the Agents page is needed — generation uses fixed built-in specialists.

**Create a workflow:** **New documentation** → choose scope (**Service** or **Whole
environment**), the service/environment, and the model → **Create workflow**. The
workflow page shows a phase summary table, the phases, and the generation history.
Edits to prompts and context **autosave** ("Saving…" / "Saved"); closing the page does
not cancel running jobs.

**Service workflow phases** — each is started manually; none triggers the next:

| Phase | Button | What it produces |
| --- | --- | --- |
| 1. Code graph | **Generate graph** | Code graph per project (no LLM, 0 tokens). Inspect it in the graph explorer. Later phases stay disabled until every selected project has a graph |
| 2. Application overview | **Generate / Regenerate application overview** | Overview document, optional D2 component diagram |
| 3. API documentation | **Generate / Regenerate API documentation** | One analyzed section per HTTP route found in the graph, assembled with a coverage table. Progress shows "N route registrations · x/y sections saved" |
| 4. Messaging documentation | **Generate / Regenerate messaging documentation** | Message flow documentation, optional D2 diagram |

Each phase section has an editable **prompt** (changes apply to that run only;
**Reset** / **Reload active** / **Manage … prompt versions**), a **context and
requests** field, and an **Inspect** area: run selector, status and progress, **token
usage**, the exact prompt and source snapshot used, a preview, and a link to the
document. Each successful run adds a **new version** of the same document.

**Additional documentation** (same page): tick **Code analysis**, **Code diagram
(D2/TALA)**, **Deployment on an environment** and/or **Deployment diagram** (deployment
documents also need an environment and servers to inspect), add context, tick **Create
new versions of existing documents** if they exist already, then **Generate documents**.

**Environment workflows** offer **Full environment architecture** (built from the
services' saved documents, with three D2 views) and **Server infrastructure
architecture** (inspects one SSH-enabled server).

**Important behaviors**

- Only **one documentation job per service** can be queued or running at a time.
- If source code or the graph changed since Phase 1, the run fails with "Source or
  graph selection changed. Generate and inspect the graph again…" — rerun Phase 1.
- To retry a failure, click the same Generate button. A failed API run reuses
  sections that already completed when inputs are unchanged.
- Token usage (input, output, model calls; cached/reasoning listed separately) is
  recorded even for failed runs.
- Results are exported as files to `GENERATED_DOCS_PATH` (Markdown, `metadata.json`,
  and `.d2` files per diagram).

### 5.14 Documents and documentation prompts

**Documents** (`/documents`) — all generated and hand-written documents. Search by
title/slug/description and filter by linked resources. **Create document** writes one
manually.

The editor has fields Title, Slug, Short description, Purpose, Audience (People and
agents / People / Agents), Tags, and tabs **Write**, **Preview**, **Linked resources**
(including documented source commits) and **Version history** (**Use version as
draft**). Actions: **Save new version** (every save is a full immutable version),
**Export to folder**, **Download document folder** (ZIP), **Delete**, and links back
to the workflow that produced it.

Documents are searchable through the automatically created `documents` collection
(folder `generated-documents`, old `v<n>/` folders excluded): **Refresh** it in
**RAG → Collections** to index what is on disk. The Reader's search and chat and the
agents' document search then find them.

**Prompts** (`/documentation/prompts`) — versioned prompts used by documentation
generation. Saving changed text creates a draft version; **Activate vN** makes it the
active one; inactive versions can be deleted. If an active prompt changes while a
workflow page is open, generating fails with "An active prompt changed. Reload it…".

### 5.15 Storage

`/storage` — read-only overview of the five storage areas: source code
(`CODE_PATH`), code graphs (`CODE_INDEX_PATH`), RAG source files (`RG_DATA`, where Jira exports are written), document
exports (`GENERATED_DOCS_PATH`) and bootstrap docs (`DOCS_PATH`). Each shows host and
container paths, availability, and a file browser with text preview.

### 5.16 Activity

`/activity` — Reader chat usage: users, their sessions, and each session's questions
and answers with the cited sources and the **not covered** badge. It reads Reader
conversations from the database. **Open in agent session** opens the conversation in
the Reader agent's **Test** tab, where **Add feedback** → **Suggest improvements**
works on real conversations. It does not show admin changes, agent test sessions or
board tasks.

**Agent runs** (`/activity/runs`) — a complete record of every agent run: agent Test
conversations, Reader chat, multi-agent board tasks (coordinator, every specialist and
the summary in one record) and documentation steps. Each record shows, in order:

- the full instructions sent to the model and the input (with conversation history);
- the model's reasoning when the provider returns it (otherwise "Reasoning not returned
  by the provider.");
- every tool call with its full arguments and full output (SSH, shell, code, documents,
  MCP…);
- messages, token usage per model call, errors, and the final status.

Known secrets are replaced with `[redacted]`; very large values are cut at 1,000,000
characters. Filter by run type or user. **Full record** links lead here from each reply in
an agent's Test tab, from a board task, and **Run records** from each documentation job.
Records are kept until deleted; only administrators can see them.

---

## 6. End-to-end workflows

### A. Make internal documentation searchable for everyone

1. Write the pages in **Docs → Documents**; they land in the `documents` collection.
   (Code is made searchable from the code project page with **Searchable**.)
2. Admin → **RAG → Collections** → **Refresh** `documents`; watch **Processing jobs**
   until it succeeds.
3. Check the collection's files under its detail page.
4. Check results in **RAG → Search**.
5. Readers open http://localhost:8180 → the collection appears in the sidebar; use **Search**.

### B. Document a service from its source code

1. **LLM Settings**: add and test an Anthropic (or OpenAI Responses / Bedrock Claude) model.
2. **Code projects**: add the Git repository; wait for the clone.
3. **Services**: create the service, link the code project.
4. **Docs → Generate documents → New documentation**: scope *Service*, choose the service
   and model → **Create workflow**.
5. Phase 1 **Generate graph** → inspect the graph.
6. Run Phase 2 (overview), then Phase 3 (API), then Phase 4 (messaging) — one at a time.
7. Review each document in **Docs → Documents**, edit, **Save new version**.
8. **RAG → Collections** → **Refresh** the `documents` collection to make them searchable.

### C. Document deployments and infrastructure

1. **Servers**: register hosts, configure **SSH settings**, **Test connection**, add trusted host keys.
2. **Environments**: create `production` with its servers and services.
3. **Services**: add deployments (server + environment + container/unit + port + log path).
4. In the service's documentation workflow, tick **Deployment on an environment**
   (+ diagram), choose environment and servers → **Generate documents**.
5. For the whole environment, create an *Environment* workflow and generate
   **Full environment architecture** / **Server infrastructure architecture**.

### D. Build and test an operations agent

1. **Agents → New Agent Wizard** (generate a proposal or **Start blank**).
2. **General**: assign a model; **SSH**: allow servers; **Code projects** / **Documents**: select resources.
3. **Test**: run a task such as "Check disk usage on the app servers"; inspect **View activity**.
4. Use **Add feedback → Suggest improvements**, then **Versions → Save draft → Activate**.

### E. Run a team task

1. Open the **Coordinator** agent → assign a model; adjust coordinator settings.
2. **Multi-agent board → New task**, pick specialists, **Start task**, follow the board.

### F. Bring Jira knowledge into RAG

**Jira Settings** → save and test → **Tickets** → fetch with JQL → select → **Export
selected** into the target collection (default `jira`) → follow the notice's link →
**Refresh** it.

---

## 7. Operations

### Configuration (`.env`)

| Variable | Meaning |
| --- | --- |
| `WEB_PORT` 8180, `ADMIN_PORT` 8183, `KEYCLOAK_PORT` 8181, `PHOENIX_PORT` 8182, `POSTGRES_PORT` 5438 | Loopback ports |
| `WEB_HOST` | Public host name used for login redirects |
| `DATA_ROOT` | **Required.** Absolute host folder for the application's files (set by `make setup DATA_ROOT=...`) |
| `RG_DATA` | Optional override for the `rag-data` area, the writable folder for Jira exports (default `DATA_ROOT/rag-data`) |
| `CODE_PATH` | Optional override for code projects and Git clones (default `DATA_ROOT/code`) |
| `CODE_INDEX_PATH` | Optional override for code graph snapshots (default `DATA_ROOT/code-index`) |
| `GENERATED_DOCS_PATH` | Optional override for document exports (default `DATA_ROOT/generated-documents`) |
| `DOCS_PATH` | Optional override for bootstrap docs (default `DATA_ROOT/docs`; not what the Reader shows) |
| `CREDENTIAL_ENCRYPTION_KEY` | Encrypts all stored secrets — **back it up separately** |
| `RAG_BACKOFFICE_KEY` | Internal key between APIs and RAG |
| `APP_ADMIN_*`, `KEYCLOAK_ADMIN_PASSWORD`, `PHOENIX_*`, `*_OAUTH2_PROXY_*` | Bootstrap credentials and gateway secrets |
| `PHOENIX_PUBLIC_URL` | Enables "View trace" links on the agent board |
| `DOCKER_DNS_SEARCH` | DNS search domain so short SSH/Jira host names resolve in containers |

Model credentials are **not** in `.env`; they are configured in LLM Settings.

### Backup

Back up the Docker volumes `knowledge-agent-v3_postgres-data`, `_wiki-data`,
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
| Code docs phases disabled | Run Phase 1 **Generate graph** for every selected project |
| "Documentation … already queued or running" | Wait for the current job of that service |
| Port in use | Change the `*_PORT` variable and run `make up` |

The stack uses HTTP and Keycloak dev mode; configure TLS and production Keycloak
before exposing it beyond localhost.

---

## 8. Current limitations

- **Reader chat answers appear when complete** (no word-by-word streaming).
- **Documents are searchable through the `documents` collection** (the
  `generated-documents` folder) only after a **Refresh**.
- **Collections are refreshed manually**: nothing syncs automatically, so use
  **Refresh** (or **Refresh all stale**) after files change. Code is indexed as plain
  text (no code-aware chunking or line-number citations), and a collection is limited to
  20,000 files and 200 MB per sync.
- The `api-analysis` skill is in the Skills library but is not used by API documentation generation.
- The `code_graph` tool is only available to documentation generation, not to agent
  Test chats or the board.
- Service inventory is operator-maintained: no live health monitoring.
- Code projects, LLM models and skills cannot be deleted (disable/leave unused).
- Reader document pages don't show front matter, "updated", or freshness badges in
  workspace mode; search results have no highlighting.

---

## Appendix A: Agent tools and safety

| Tool | Given when | Safety limits |
| --- | --- | --- |
| `list_files`, `read_file`, `search_files` | Code or Documents capability on | Only selected roots plus a scratch workspace; symlinks resolved; binaries refused; reads ≤ 64 KiB; secrets redacted |
| `shell_run` | Code or Documents capability on | Runs in the separate `shell-worker` in a bubblewrap sandbox: no network, no capabilities, empty environment, sources mounted read-only, only the scratch workspace writable; 1–300 s; 32 KiB output |
| `code_search` | Code capability on | Tree-sitter function lookup (Go, Python, JS, TS, TSX) |
| `ast_grep` | Code capability on | Structural code queries; 30 s timeout |
| `code_graph` | Documentation generation only | Queries the Phase 1 code graph |
| `ssh_run` | SSH capability on and an allowed server has a key | Runs **any** command (not read-only!) under `timeout` + `setpriv --no-new-privs`; root only via `sudo -n` when allowed; host key checked only if trusted keys are saved; 32 KiB output |
| MCP tools | MCP capability on | Tools of the selected, tested MCP servers |
| `search_documents`, `read_document` | Documents capability on | Search and read only the granted RAG collections; with **Answer only from documents** on, these are the agent's only tools (plus `report_not_covered`) |
| `report_not_covered` | **Answer only from documents** on | Records the uncovered question as a gap and marks the answer **not covered** |

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
| README.md | Agent Chat/Test returns `TODO` | Agent **Test** and the **multi-agent board** really run models |
| services/docs-wiki-ui/README.md | Freshness badges, streaming chat, highlighted snippets | Not present in workspace mode |
| docs/documentation.md | One form with two scopes; generations page is history | Workflows: create per service/environment, then run Phases 1–4 and additional documents |
| docs/documentation.md | 16,384-token output limit for all runs | Not applied to per-endpoint API analysis |
| docs/code-processing.md | Choose a service's "source folders" | You choose code projects; folders are mapped in Code projects |
| docs/architecture.md | Worker serializes jobs per project | The repository worker processes one job at a time overall |
| docs/runtime-verification.md | Admin pages `/platform`, `/github-projects` | `/platform` doesn't exist; `/github-projects` redirects to `/code-projects` |
| Skills (migration 0059) | `api-analysis` skill for API documentation | Not wired into documentation generation |
