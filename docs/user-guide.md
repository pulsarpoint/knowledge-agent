# Knowledge Agent v3 — User Guide

This guide explains how the application is used, from first start-up to day-to-day
work. It describes the behavior of the **current code** (including uncommitted work
on document agents, code graphs and the sandboxed shell worker), not the
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
    - [Chats and proposals](#chats-and-proposals)
  - [4.6 Environments](#46-environments)
  - [4.7 Services](#47-services)
  - [4.8 Jira](#48-jira)
  - [4.9 Skills](#49-skills)
  - [4.10 MCP servers](#410-mcp-servers)
  - [4.11 Agents](#411-agents)
  - [4.12 Write a new document with an agent](#412-write-a-new-document-with-an-agent)
  - [4.13 Documents](#413-documents)
  - [4.14 Storage](#414-storage)
  - [4.15 Agent runs](#415-agent-runs)
  - [4.16 Schedules](#416-schedules)
  - [4.17 Events](#417-events)
  - [4.18 Event handlers](#418-event-handlers)
  - [4.19 Notifications](#419-notifications)
  - [4.20 Memory](#420-memory)
  - [4.21 System map](#421-system-map)
  - [4.22 Prompts](#422-prompts)
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
- **Write documents with an agent**: on Documents → New, chat with an agent that has the code,
  documents, servers and services you allow; it saves its work as a draft.
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
               agent-session-worker (document, Test, Ask agent and event-handler sessions)
```

`agent-session-worker` runs document, Test and Ask agent sessions, and the agent runs that event handlers start. Like `admin-api` it reaches
RAG, PostgreSQL, MCP servers, model providers and SSH hosts, and runs `shell_run` in `shell-worker`; it reads `DATA_ROOT`
read-only.

All ports bind to `127.0.0.1` only. Internal services have no host ports.

---

## 2. Install, start and sign in

Requirements: Docker with Compose, Python 3.10+ (for the setup script). No model
credentials are needed to start.

```sh
make up          # generate .env secrets, build images, migrate DB, start everything (including the Jira mock)
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
8. **Docs → Documents** → **New** → **Write with an agent**: give a title and folder, then tell the agent what to write.
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
| Administration | Storage · Prompts · Skills · MCP servers · Agent runs · Schedules · Events · Notifications · Memory |
| Agents | All agents |
| LLM Settings | Models |
| Jira | Tickets · Settings |
| Environments | Environments · Services · Servers · System map |
| Code | Projects · Code graphs |
| Docs | Documents · Folders |
| RAG | Collections · Processing jobs · Settings · Search |

Footer: theme toggle and **Sign out**.

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
prerequisite for code documentation phases ([4.12](#412-write-a-new-document-with-an-agent)).

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

A server's name opens its page (`/servers/:id`): its **Info** (details, SSH state, the services deployed on it, its
documents) and its **Chats**. Services (`/services/:id`) and environments (`/environments/:id`) have the same kind of
page. **Connections and documents** there opens the same panel as the System map.

A server's, service's or environment's **inventory document** is the document tagged `inventory` built for it (the
most recently updated one when several; the others are in its Documents list): Info shows it rendered, with **Open** to
edit it (through drafts, like any document; one that is only a draft is marked "Draft — not published"). Without one,
Info says "No inventory document yet." with **New inventory document**: the New document page opens built for the
resource with its Tags starting as `inventory`, and **Save** creates the draft of its inventory document. Below it, the **Documents** list shows every
published document linked to the resource (built for it or linked) and to its direct children — an environment's
services and servers, a server's services, a service's code projects and the servers it is deployed on: each row has
the title (opens the document), its tags (each opens the Documents list filtered by it), the first line of its
description, and "· <name>" when it belongs to a child. The resource's inventory document comes first, then its other
documents, then the children's, grouped by child; a document linked to several of them is listed once. With none:
"No documents yet.".

#### Chats and proposals

**Chats** lists the conversations on the server (service, environment): open ones first, then by last activity; runs
started from **Propose…** carry a `proposals` badge. **Chat** opens the latest open chat that is not a Propose… run
(or a new one), **New chat** a new one, and a chat in the list that chat. The chat stays hidden until you ask for it;
on very wide screens (2xl) it opens beside the Info, below that as a second tab (**Info** / **Chat**). The URL keeps
the open chat (`?chat=<id>`), so a reload or a link opens it again; a chat of another page is refused.

A chat answers questions about the resource: its prompt is the resource type's assistant prompt (**Server
assistant**, **Service assistant**, **Environment assistant** in **Administration → Prompts**), which uses the
smallest read-only command that answers. **Settings** locks the resource on; you may add other resources. A server
chat needs SSH to be set up and allowed for the server. A plain chat may not change content: its proposal and
edit tools refuse.

Every chat (here, Ask agent beside a document, Documents → New and an agent's Test tab) has a **Debug** tab beside the
**Conversation**. It shows what the model receives, built by the same code that runs it: **Instructions** (the whole
system prompt, with **Copy**), **Prompt** (the library prompt and version it came from, linked, or "Written in the
Propose wizard"; the agent's instructions and skills), **Tools** (each name and description as the model sees it),
**Resources** and **Model**. Before the first message it shows what the next conversation would start with; secrets
are never shown.

**Propose…** starts a run that proposes what the inventory should record. It is a small wizard:

1. **What** — on a server, **Services on this server** and/or **Documents**; on a service or environment,
   **Documents** only.
2. **Options** — for services: update the ones already recorded, look for new ones; for documents: an overview
   document, one document per service (servers and environments), the aspects to cover (configuration, logs, health
   checks, ports and paths); and extra instructions.
3. **Review** — the prompt built from those choices (the steps of the **Server discovery** prompt, one block per
   option; edit it as you like). The run gets every tool, like every run, and may propose (it starts with the Content editor
   role); the prompt asks only for what you chose. **Start** starts the run and opens it as the page's chat, so you can
   watch it and write to it while it works.

**Proposals** (`/servers/:id/proposals`, and the same for services and environments) lists every proposal made in
the resource's runs, grouped by run (newest first, with who started it and **Open chat**), with a status filter
(**All**, **Pending**, **Accepted**, **Rejected**). **Review** opens one proposal:

- A **new service** shows its fields.
- An **update** to an existing service shows the current value against the proposed one,
  with a **Keep** checkbox per changed field. Untick a field to keep the current value.
- A **document** shows its text.

**Accept** writes services to the inventory: the deployment is added or updated and connections are added. A document
is never written live by Accept: a new document becomes a draft (a generated document named in `servers/<server>/`, in
`servers/<server>-2`, `-3`, ... when the folder would overlap another document's export, or in
`server-documents/<server>` when `servers` itself is a document's export), and a new version of an existing document
becomes a draft of that document. The accepted proposal says "Waiting to be published" (a new document) or "Waiting
to be promoted" (a new version) with **Open draft**; **Publish** on the draft makes the new document live (it is
built for the server or service), **Promote** on the document's Drafts tab makes the new version live, and the proposal
then shows **Open document**. A new document is tagged `inventory` (the server's or service's inventory document) when no document tagged
`inventory` is built for it yet; otherwise it is an ordinary document beside it. Discovery never tags an existing
document and never changes the inventory document: a proposal with its title is a new document, not a new version, and
you decide whether to replace the inventory document with it (move the tag: edit the tags of both and promote) or merge
them by hand. If the document changed since the proposal was made, Accept refuses once and shows the proposal again;
review it and accept.
**Reject** discards the proposal. Rejecting a proposed new service also rejects the document proposals about it;
documents about an already recorded service stay and can be accepted on their own.
A document about a proposed service can be accepted only after the service is. If
something was added since the proposal was made, it turns into an update or a new version
and asks you to look at it again.

The agent is told to use read-only commands, but SSH itself is not read-only: anything
the login can do, the agent can do. Use a login with only the permissions you want the
agent to have.

### 4.6 Environments

`/environments` — named environments such as `prod` or `staging`. An environment has a name and a description; what
is in it comes from deployments: a service or server is in an environment when one of its deployments is (add
deployments on the Services page). A service deployed in two environments is in both.

- The list shows each environment with the number of services and servers deployed in it: filter, **Map** (the System
  map rooted at the environment), **Documents**, Edit, Delete; **Details** lists what is deployed in it.
- An environment's page (`/environments/:id`) lists its services and servers and links to its **System map**.
- An environment with deployments cannot be deleted: remove or move its deployments first.

### 4.7 Services

`/services` — the application inventory that ties code, environments and servers together.

**Add service**:

- **Application details**: name (unique), owner/team, **type** (Custom application,
  Built-in software, External service, Unclassified), **primary role** (Application,
  Proxy, Web server, Database, Cache, Message broker, Worker, Other), purpose.
- **Code projects** that make up its source (each a *built from* relation).
- **Service relationships** — logical relations, in either direction, each with an optional "why": *Depends on* /
  *Used by* (depends on), *Part of* / *Parts* (a component or library of another service). Network traffic between
  deployments is recorded as connections on the System map.
- **Where it runs**: **Add deployment** — server, **environment** (required), runtime/manager, container/unit name,
  port, deployed version, log location. **Register server** opens the server form inline. Removing a saved deployment
  also removes its connections.

**Save** writes the service, then each changed deployment and relation through the inventory API (one request each).
If one of them is refused (for example a duplicate deployment, or someone changed the service meanwhile), the form
stays open with the message; what was already saved stays saved, and saving again sends only the rest.

**Details** shows everything above plus code commits and SSH status per deployment,
and links to the service's documents and environments. **Agent context (JSON)**
opens `/api/services/{id}/context`, a read-only, credential-free JSON view of the
service for agents. Note: the *downloaded* commit is not the *deployed* version.

A service with deployments cannot be deleted: remove its deployments first. Deleting a service removes its relations
but not code projects or servers.

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

**Users and agents** (on the Settings page, once a connection is saved) maps Jira users to agents: a ticket assigned
to a mapped user is that agent's work. Search for a user as you type (or type a user key), choose an agent, **Add**;
change a row's agent in place or remove it. A row whose agent is turned off says **Agent disabled**. An event handler
with **Agent mapped to the assignee** (4.18) runs the mapped agent. The first mapping creates that handler when there
is none: **Jira assignee agents** (every ticket event with new work for the assignee; the answer is posted on the
ticket and the ticket is given back). The section shows **Handled by: Jira assignee agents (enabled)** (or
**disabled**), which opens the handler, or **No handler runs these agents.** with **Create handler**. The handler is
an ordinary one: edit, turn off or delete it under Events → Handlers; changing mappings never changes it.

For testing without a real Jira: the mock starts with `make up` (`make jira-mock-up` rebuilds only it), then URL
`http://jira-mock:8080/jira`, user `mock-admin`, token `jira-mock-token`
(75 demo tickets). Stop with `make jira-mock-down`.

**Auto pull.** The automatic pull is a schedule, set on Administration → Schedules (4.16): **New schedule** → Jira
tickets, with its own query and number of tickets (separate from the query on Jira → Tickets; left empty, the query and
number of the last manual fetch are used, or 25 tickets), how often and the time zone. **Edit** there changes them;
**Run now** pulls at once. Jira → Tickets shows a one-line summary (off, or how often and the next pull) with a link to
the Schedules page. Jira → Tickets always shows the tickets from the
most recent pull, manual or automatic; its query box keeps your manual query. A failed pull is shown and retried at the next scheduled time; a pull that meets a manual fetch is skipped. Every pull, manual Fetch
tickets or scheduled, records an event for every ticket that is new or changed since the last event for it (see
Administration → Events), in the same transaction as the saved tickets.

### 4.9 Skills

`/skills` — a library of reusable Markdown instructions that agents can be given.
Fields: name (lowercase-with-dashes, unique), description, instructions (Markdown),
enabled. Search and filter by status; toggle enabled inline. Skills are instructions only (never executed) and cannot
be deleted — disable them. A run lists the skills in its access by name and description; the agent loads a skill's
full instructions with the `load_skill` tool when it uses it.

### 4.10 MCP servers

`/mcp-servers` — external tool servers agents can call. Only the **Streamable HTTP**
transport is supported. Fields: name, description, HTTP(S) endpoint (use
`host.docker.internal` instead of `localhost`), optional bearer token (encrypted),
enabled.

**Test connection** discovers the server's tools (25 s limit). A server can only be
selected for an agent after it is enabled, tested successfully and exposes at least
one tool. Changing the URL clears discovered tools and requires re-entering the token.

### 4.11 Agents

`/agents` lists agents with **Resources**, capabilities, model, active system prompt and status. **Resources** counts what the agent may use by type, for example `1 server · 2 documents` (or `No resources`).

**Create an agent** with **New agent** (the only way to create agents). It opens the wizard directly. Its first field is **Agent model** (the model the agent runs on; it defaults to the first model in the catalog). Both a generated proposal and **Start blank** use that model; to change it go back with **Edit role**:

- **Generate proposal** — describe the role, choose a generator model, and the model
  proposes the name, prompt, instructions and resources (servers, services, environments, code projects,
  collections, documents, skills and MCP servers).
- **Start blank** — skip generation (no model needed) and fill in the same review tabs
  yourself.

Review and edit the tabs (General, Prompt, Resources; **Content editor** is on General), then **Create agent** (enabled once name and system prompt are
filled in). The agent model is optional — an agent without a model is
saved but runs only after you choose one with **Model** on its General tab.

**Agent detail tabs** (`/agents/:id`):

| Tab | What you do there |
| --- | --- |
| **General** | **Model** (required to run; saved with **Save details**), name, purpose, when-to-use, **Instructions** (added to the system prompt), **Content editor** (lets the agent propose services and documents and edit its editable documents), **Answer only from documents** (cite every claim and report questions the documents do not cover), **Enable agent**; **Offer as an expert** (other agents can call this agent as a tool; needs When to use; an **Expert** badge shows in All agents); the **System prompt** card (one field, up to 100,000 characters; **Save prompt**); *Clone of <original>* for a clone |
| **Resources** | What the agent may use, grouped by type: Servers, Services, Environments, Code projects, Collections, Documents, Skills and MCP servers. Add from the project's resources (or **Select all** of a type) and **Remove**; a document may be made **Editable** (only a Content editor changes it). A resource that no longer exists shows as **missing** and is dropped when you save. **Save** writes them all |
| **Test** | Chat with the agent; **Settings** may pick some of its resources for the run |

An agent has one configuration: what its tabs show is what runs. A conversation keeps the configuration it started with.

**Run an agent.** **Run** (the play button) on an agent's row, or **Run agent** next to **New agent**, opens the *Run
agent* dialog: the agent (preselected from the row; from the top button choose one of the enabled agents — a disabled
agent's **Run** is off), the **First message** (required) and its resources — every resource of the agent unless you
narrow them with **Choose resources**, the same settings sheet as the Test tab's. **Start** starts a chat session and
opens its session page (`/sessions/<id>`), where you send further messages, **Stop** a turn and **Close session**. A
chat is not listed on the agent's Test tab; its turns are **Chat** runs in Agent runs (4.15), and it can be reviewed
with **Suggest improvements** through the API like a Test session.

**Experiment on a clone.** Open the agent's header menu (*Actions for <name>*) or the **Clone <name>** button in the All agents list and choose **Clone agent**: the clone has the same configuration, references the same saved SSH credentials, starts without history and shows *Clone of <original>*. Change and test the clone, then **Apply to <original>**: the dialog lists the changed sections, and everything except name, description and enabled is replaced. Apply is refused while the original is working or when the original changed since you opened the dialog; if the comparison fails, **Compare again**. Afterwards **Delete this clone** removes the clone (its dialog lists the test chats and other history deleted with it), or keep it as an alternative.

**Test tab.** The Test tab is the same chat as **Ask agent**. Type a message and send it: it starts a Test run, which the
agent session worker runs (the URL keeps `session_id`). The workspace shows your message, a collapsed **Worked** block
(thinking, tool calls with their results, comments you sent while it worked) and the answer with its cited sources; the
header shows the tokens, model calls and tools used, and the sidebar **Agents in this run** the agent's status (on a
narrow screen open **Run details**). **Send message** works while the agent works (your comment reaches it at its next
step); **Stop** ends the current turn.

**Settings** opens a sheet. A Test run can only narrow the saved agent; nothing can be added. The model and prompt are
shown read-only ("Change them in General."); to try another prompt, clone the agent. In the sheet you can
choose some of the agent's experts and some of
its resources (see *Resources of a run* below). **Preview** shows the
configuration the run would use. Settings are remembered per agent in this browser and reset when the agent is saved.
**New chat** closes the open run and starts fresh. **Earlier runs** lists this agent's Test runs and event runs (**Event**
badge) to reopen them. A Jira event run's page also lists **Earlier runs on <KEY>**: the other runs on the same
ticket. Under each answer: **Full record** (the turn's record in Agent runs) and **Add feedback** →
**Suggest improvements**, which runs a reviewer that proposes instruction changes and memory notes (never system
prompt changes); **Suggestions (n)** lists them (with the names of the proposed notes), and **Review and apply** opens
them for editing.

**Apply suggestions.** In a suggestion, **Review and apply** opens the sheet *Apply suggestions*: the reviewer's advice,
**Instructions** (the current text under *Current instructions*, the proposed text to edit, and **Apply instructions**,
ticked when the reviewer proposed a change) and **Memory notes** — facts for the shared memory every agent can search.
Each note has a checkbox (ticked by default) and an editable name and content; a note whose name is already taken shows
**Already exists — open in Memory** and starts unticked (edit that entry on the Memory page, or rename the note). With
instructions ticked, **Apply to this agent** writes them into the agent (“Applied to this agent. New conversations use
it.”) and **Apply to a new clone** clones the agent first and writes them there, leaving the original unchanged; with
only notes ticked, **Save memory notes**. When notes were sent, the sheet lists the saved notes (each opens its Memory
entry) and any refused names with the reason; **Done** closes it.

**Tools.** Every run gets every tool; there is nothing to choose. What a tool may do follows the run's resources and
the **Content editor** role: a tool without what it needs refuses (for example `This run has no servers.`), and
`report_not_covered` works only with **Answer only from documents**. **Agent runs**, run records, session pages and
Test chats show how often each tool was used.

**Subagents.** Every agent can start helpers with `run_subagent`: each gets a complete task, every tool except
`run_subagent`, and some or all of the run's resources (content editor off), but not the conversation. Helpers run in parallel (`AGENT_MAX_SUBAGENTS`,
default 4, at once; at most 16 helpers per turn, together with expert calls) and their final answer comes back to the agent. In the Test tab and on the
session page each helper appears in the Worked block (**Open**) and under **Agents in this run** (helpers in the loaded
history; earlier ones appear when you scroll up to older history); selecting one shows its task, its work and its
answer (**Main agent** goes back). The header and the main agent's entry count the whole run including helpers, while
the main turn's Worked line counts only the agent's own work. **Stop** stops the agent and all of its helpers.

**Experts.** Turn on **Offer as an expert** on an agent's General tab (it needs a When to use text, which tells other
agents when to call it; the agent must be enabled and have a model). Every other native agent, including the agent of a
new-document run, then gets a tool `ask_<name>` for it; an expert itself calls no experts. A run may narrow the
experts in its chat **Settings**. An expert works on
its own resources, or on those the caller passes, which must be the caller's own and in the expert's resources. An expert starts without the
conversation, may start its own subagents, uses its own model and keys, and returns its final
answer. The helpers of one turn (subagents and expert calls together) are limited to 16; sessions started before an
expert was added do not see it. In the Test tab and on the session page an expert call appears in the Worked block as
`Asked <name> · <task>` with **Open**, and under **Agents in this run** as `Expert · <name>` once it has started
(selecting it shows its history; **Main agent** goes back); a call refused before it started shows as a failed tool
line. In **Agent runs**, filtering by the expert's agent lists the calls made to it as "Called as expert by <caller>";
opening one shows that call's record, including the expert's own subagents.

**Resources of a run.** A run gets the agent's resources, or a subset picked for it: on the **Test** tab **Settings**
lists the agent's resources by type and you pick from them (the choice is kept for the session and shown in the header
and in **Preview**); chat settings, event handlers and expert calls narrow the same way. A run never gets more than the
agent has: a pick outside its resources is refused (`server <id> is not in the agent's access`), and an editable
document may be picked read-only. What a run was given brings its direct children: an environment its services and
servers, a service its code projects and the servers it is deployed on, a server the services deployed on it
(`get_available_resources` marks them `via <type> <id>`). The tools reach exactly the run's resources; a kind the run
has none of is refused (`This run has no code projects.`). Example: a **Code analyst** with several code projects,
tested with `payments-api` in one conversation and `helpers` in the next.

Limits: message ≤ 20,000 characters; a Test session's turns have no time
limit (they end with the answer, **Stop**, **New chat** (it closes the open run) or a lost worker), and a session closes after 24 hours
without activity. A message is blocked if the agent is disabled, has no enabled model, or other tabs have unsaved
changes.

**Upgrading.** Migration 0060 turns **Answer only from documents** on and Code
(repositories) off for the Search agent; its down migration does not turn Code back
on. Saving the agent's settings changes what runs next.


Removing an agent that has history (test chats, chats started with **Run agent**, documentation sessions or run records
of earlier test chats) lists that
history in the **Remove agent?** dialog. **Delete agent and history** deletes the agent together with it; saved
documents are kept. To keep the history, cancel and disable the agent instead. An agent that is working right now
(a Test or document session that is starting or working) cannot be removed; stop that work or wait for
it to finish first.
See [Appendix A](#appendix-a-agent-tools-and-safety) for the tools an agent receives.

### 4.12 Write a new document with an agent

**Documents → New** has a **Write with an agent** button (`/documents/new`). It opens a chat with an agent that writes a document that does not
exist yet. It is the same chat as **Ask agent** on a document ([4.13](#413-documents)): the page takes the full width, the
document takes two thirds and the chat one third.

1. Give a **Title** and a **Folder**. The Folder field appears with the chat. Without both, nothing starts. Folder names are
   lowercase (`Payments` and `payments` would be the same folder on macOS): each segment uses lowercase letters, digits,
   '.', '_' and '-', does not start with '.'; `node_modules` and a first segment `services` are not allowed; at most 200
   characters. The title needs ASCII letters or digits (the file name is made from them).
2. Message the agent: what to write and what to read. It writes the document with `save_document` and changes it with
   `edit_document`. Each save updates the run's draft; you can ask for more changes.
3. A bar above the editor shows the draft. **Compare** opens the draft page. **Promote** creates the document and opens it.

If the title and folder match an existing document, the agent's saves become a draft of that document. Nothing goes live until
you promote the draft. There is one conversation per page visit; **New chat** closes it.

**Settings** in the chat opens what the agent may use: the model, the resources (code projects, document collections,
environments, services and servers), the experts, and under **Advanced** the prompt. A server whose SSH
is not set up or not allowed cannot be chosen. The prompts offered are those tagged `documentation`; the default is **Write a
document** (Administration → Prompts). It tells the agent to read what it was given, write with `save_document`, change a passage
with `edit_document`, and never say the document is published. If you write your own prompt, tell the agent to write with
`save_document`; a final answer is not turned into a draft. **Preview** shows exactly what the next message will start with, as
JSON. Nothing is chosen until you tick it; experts (`ask_<name>`) are offered only when you tick them. The run gets
every tool, including `run_subagent`; what each may reach follows the resources you chose (for example `ssh_run` only
on the chosen servers), as in an agent's Test tab.

**The run page.** Every run, including this one, has a page at `/sessions/<id>`. It uses the same workspace as the Test tab: the
main pane shows each turn (your message, a collapsed **Worked** block with thinking, tool calls and results, and the answer)
and scrolls on its own; the message box is always visible. After you scroll up it stops following new events and shows
**Jump to latest**. Opening a session loads the latest 100 events; earlier pages load as you scroll up. The header shows tokens
in/out/cached, model calls and the status: Starting, Working, Waiting for you, Stopped, Failed or Finished; the sidebar shows
**Agents in this run** and the draft (on a narrow screen open **Run details**). Each answer links to its **Full record**. A run
that wrote a draft shows **Open draft** (or **Open document** once it is promoted; a discarded draft shows "The document draft
was discarded.").

- **Send message**: the message box stays enabled while the agent works. The agent receives the comment at its next step
  ("Queued. Your comment reaches the agent at its next step."). Sent between turns, a message starts a new turn.
- **Stop**: ends the turn within about 30 seconds; a request or command already running cannot be recalled. Queued messages are
  discarded when the turn is stopped.
- **Close session**. Sessions continue when you leave the page and close after 24 idle hours (a session idle for 5 minutes is
  paused and resumes on your next message).
- **Wake-ups**: an agent can schedule a wake-up to continue the session later (from 1 minute to 30 days ahead, at most 3
  pending per session), for example to check a deployment again. Each pending one is listed above the message box as
  `Wakes up at <time>: <message>` (your local time) with **Cancel**. When it is due the session gets a new turn that starts
  with `Scheduled wake-up`; if the agent is working then, it waits for that turn to end. A session with a pending wake-up
  is not closed after 24 idle hours. **Stop** keeps the wake-ups; **Close session** cancels them.

A turn has no time or model-call limit: it ends when the agent finishes or you press **Stop** (each model request and MCP
connection still times out after 5 minutes). A failed or stopped turn leaves the conversation and the draft as they were.
Tokens are recorded, cost is not. Every turn is an agent run in **Activity → Agent runs** (`cc:<session>:<turn>`).

Errors: "Choose an enabled model."; "Select available …" for an item that is gone; `… is not available: it was removed,
archived or disabled.`; a provider error, such as a missing Bedrock inference profile, shows the provider's reason without
credentials plus a hint; "Waiting for the agent session worker…" when the worker is not running.

Security: the model key stays in the worker. Every enabled model connection's key is redacted from stored events, drafts and
error messages. `shell_run` runs in the sandboxed `shell-worker`, with `DATA_ROOT` read-only.

**Upgrading.** The Docs → Generate documents pages (`/documentation/generations`) and their API are gone; write documents here
instead. Migration 0101 closed the Generate documents chats that were open ("Closed by an update; start a new chat."). Earlier
Generate documents runs stay readable at `/sessions/<id>` and in Agent runs. Custom prompts tagged `documentation` should tell the
agent to write with `save_document`; copy the rules from **Write a document**. Migration 0073 removed the service,
whole-environment and code-project generation; documents written by service phases keep exporting into
`services/<service>/<phase>/`.

### 4.13 Documents

**Documents** (`/documents`) — all generated and hand-written documents. Search by
title/slug/description and filter by linked resources and by one **Tag** (`?tag=`, exact, any case; the Drafts view
filters by it too). Every tag badge — in the list, on a resource page and under a document's Tags — opens the list
filtered by that tag. **New document** writes one manually.

Every save is a draft: content goes live only by **Publish** (a document that is only a draft) or **Promote** (a draft
of a live document). The editor has fields Title, Slug, Short description, Purpose, Audience (People and agents /
People / Agents), Tags, and tabs **Write**, **Preview**, **Linked resources** (including documented source commits)
and, on a live document, **Version history** (**Use version as draft**), **Drafts n** (a badge counts the drafts) and
**Connections**.

- **New document**: **Save** creates a draft of the new document and opens it at `/documents/{id}`.
- A document that is only a draft shows "Draft — not published yet" with **Save** (its name — slug and folder — can
  still change), **Publish** (it becomes version 1 of the document, same address) and **Discard**. It has no version
  history or Drafts tab until it is published.
- A live document shows "Version n" with **Save**, **Export to folder**, **Download document folder** (ZIP),
  **Delete** and **Ask agent**. The first **Save** creates your draft of the document and the editor goes on editing
  it — "Editing your draft (created {time}, from version {n}). Save updates it; Promote makes it live." — later Saves
  update the same draft. Promote it on the **Drafts** tab.
- **Linked resources**: **Save links** writes the links at once (links never go through drafts); a link the document
  was built for stays locked. If someone changed the document's links meanwhile, Save links says so and writes
  nothing.
- **Inventory document**: a document tagged `inventory` that is built for a server, service or environment. Make one
  an inventory document, or stop it being one, by editing its Tags like any other content (Save, then Promote or
  Publish). The tag filter `inventory` lists them.

#### Drafts and connections

Anything a person or an agent saves is a **draft** (an accepted discovery document proposal too); a version goes live
only when a person publishes or promotes a draft.

- The list's **Drafts** column counts each document's open drafts and opens its Drafts tab; **Has drafts** shows only
  documents with drafts. **New drafts** above the table lists drafts of documents that do not exist yet (title and tags,
  folder, written by, updated) with **Open** and **Discard**; it ignores the filters. The **Drafts** view lists every draft with its tags: a draft of a new document
  is marked **Not published** (**Open**, **Publish**, **Discard**); a draft of a live document names its document.
- **Drafts** tab: the open drafts (written by a person or an **Agent run**, updated, based on version n, **stale** when
  the live version moved on). A draft shows its changes against the live version, a preview and its **Sources** (the
  document it revises and the documents its author read, "(deleted)" when one is gone). **Edit** loads it into the
  editor; **Promote** makes it the live version — a stale draft first says "This draft was based on version n; the
  live version is now m. Promoting replaces version m's text." and promotes only with **Promote anyway**; **Discard**
  removes it from the lists (it stays in the audit history). With unsaved changes in the editor, **Edit** and **Promote**
  first ask "Discard unsaved changes?" (**Keep editing** does nothing).
- A new document's draft opens at `/documents/{id}`; **Publish** creates the document there (a failed export is
  shown). If a document with that name was created in the folder meanwhile, the draft becomes a draft of it and opens
  on its draft page (`/documents/drafts/{id}`, where a discovery proposal's draft of a live document opens too) asking
  you to review it again.
- **Connections** tab: **Depends on** (add with **Find a document**, remove), **Used by**, and **Built from** (what the
  live version was built from).
- **Version history** marks versions promoted from a draft ("from draft").
- Drafts are never exported, searched or shown to agents; deleting a document deletes its drafts and links.

#### Ask agent

**Ask agent** on a document's page widens the page to the full screen and opens a chat beside the document (the document
takes two thirds, the chat one third; on a small screen the chat sits below). Write what you want ("Add a section on log
rotation; remove the old backup steps") and send it: the agent starts with the current settings, reads the document
part by part and saves its changes as a draft — never the live version. Its replies are a normal conversation: it may
ask you something first, and you can ask for more changes; each save updates the same draft. A bar above the editor
shows the agent's draft (Compare opens it with the changes; Promote makes it the live version). A turn that ended
without a save says so. If you edit, promote or discard the draft meanwhile, the agent's next save starts a new draft.
How the agent works on the document is the **Revise a document** prompt (Administration → Prompts); change it there, or
pick another prompt under Advanced.

**Settings** in the chat opens what the agent may use: the model, the servers and other resources (the ones linked to
the document are ticked; a server whose SSH is not set up or not allowed cannot be chosen), the document (listed under
Resources as editable) and the experts (none unless you tick them; the run gets every tool), and under **Advanced** the
prompt (**Revise a document** by default, any other saved prompt, or **Own prompt**). **Preview** shows exactly what the
next message will start with, as JSON. Your choices are remembered for the document in this browser. While a
conversation is open its settings are fixed: Settings shows them, and **Start new chat with these settings** closes it
and lets you change them for the next one. **New chat** closes the conversation; **Earlier runs** reopens another. The
**Agent runs** tab lists the document's runs, open ones first. Deleting the document deletes its runs; their drafts are
deleted with it.

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
sessions, earlier test chats, document runs and the earlier Generate documents sessions. Each record shows, in order:

- the full instructions sent to the model and the input (with conversation history);
- the model's reasoning when the provider returns it (otherwise "Reasoning not returned
  by the provider.");
- every tool call with its full arguments and full output (SSH, shell, code, documents,
  MCP…);
- messages, token usage per model call, errors, and the final status.

Known secrets are replaced with `[redacted]`; very large values are cut at 1,000,000
characters. The run type filter offers **Test session** (Test tab turns), **Chat** (turns of chats started with **Run agent**
on the Agents page), **Agent test** (test chats from before the upgrade) and **Native session** (document run turns); you
can also filter by agent, by status (Working, Completed, Failed, Stopped) and by user (part of the email, press Enter).
Click **Started** or **Tokens** to sort (again to reverse). The list comes in pages of 25, 50 or 100 runs (**Previous**
/ **Next**); filters, sorting and the page are in the link, so it can be shared or reloaded. **Full record** links lead here from each reply in an agent's Test tab and from each
document run turn.
Records are kept until deleted; only administrators can see them.

### 4.16 Schedules

Administration → Schedules lists everything that runs on a timetable and is the only place to set it up. **New
schedule** creates one: choose what to run and when:

- **Jira tickets** (one per project), with its **Jira query (JQL)** and **Number of tickets**, 1–200; empty fields use
  the query and number of the last manual fetch on Jira → Tickets, or 25 tickets.
- **Emit event**, with a **Name** (e.g. `Morning summary`; names are unique in the project, a name in use is refused
  with `A schedule with this name already exists.`, and `Jira auto pull` — in any case — is kept for the Jira tickets
  schedule); make as many as you like. Each run records one
  `schedule.fired` event (source **Schedule**, subject and title the schedule's name; 4.17) and the run's result says
  `Recorded event <id>.` The schedule itself runs no agent: an event handler (4.18) sends the event to an agent with
  its task, and the answer goes to Notifications (4.19). The row says which handler runs it — **Handled by:
  <name>** (opens the handler; `(off)` after the name when it is switched off) or **Handled by: <n> handlers** — or `No handler runs this schedule.` with **Add
  handler**, which opens a new handler already set up for this schedule (its name, event type `schedule.fired`, the
  condition `subject` equals the schedule's name, action Send to agent): choose the agent, write the prompt and
  save.

An Emit event schedule is **Repeating** (a timetable, below) or **Once**: a **Date and time** in the schedule's time
zone, which must be in the future (`Choose a time in the future.`). The Jira tickets schedule always repeats and shows
no such switch. A one-time schedule shows `Once at <time>` as its next run; when the time comes it runs once and
switches itself off, keeping its row and history, and then shows `Ran once at <time>`. **Run now** runs it without
using up its time. **Edit** it to a new future time to run it again (it is switched back on, as is a paused schedule
moved to a new time or switched from Repeating to Once); an edit that keeps its time, such as a rename, leaves it as
it is, even after the time has passed. Times are kept to the minute; a time that falls in a daylight-saving change is
read as the first matching clock time.

**Delete** removes a schedule and its history; the handlers of an Emit event schedule stay (they no longer match any
event). Each row shows what
it runs, when (in plain words and its time zone), the next run and the last result. Switch a schedule off to pause it;
**Run now** runs it once (a paused schedule stays paused); **Edit** changes the Jira query and number of tickets (or an
Emit event schedule's name — renaming it also rewrites the `subject` condition of the handlers that run it, after
asking `This also updates <n> handler(s).`; earlier events keep the old name as their subject) and the
timetable: a preset or a custom cron
expression (minute, hour, day of month, month, day of week), the time zone, with a preview of the next three runs.
Schedules run at most every 5 minutes. **History** shows the newest 20 runs. Schedules are run by the
`repository-worker` service; after downtime a missed schedule runs once, not once per missed time.

### 4.17 Events

Administration → Events lists what happened at your sources and what handlers made of it: Time, Source, Type,
Subject, Title and Status. Click a column header to sort by it, and again to flip the order. Sources are Jira, Memory (4.20), Schedule (an Emit event schedule, 4.16) and Manual. For Jira:
every scheduled pull (the Jira tickets schedule on Administration → Schedules, or Run now) records **Ticket created** for a ticket it sees for the first time
and **Ticket updated** when a ticket changed in Jira, with what changed (status, labels, assignee, priority, type,
summary, description). The very first pull records every ticket it fetches. Events a handler emitted have the source
**Handler** (see 4.18).

Each event is **Pending** until the event handlers have looked at it (within a few seconds), then **Processed** — or
**Failed** when one of its handlers failed; failed events are not retried. Events recorded before handlers existed are
Processed. **Processed** means the handlers have looked at it and none of those that matched failed — also when no
handler matched at all. The **Handlers** column says how many handlers ran on the event (`—` when none did), and the
**Handled** filter shows only events some handler ran on, or only those none did. Filter by source, type, status,
Handled, **Emitted by** (the handler that emitted the event) and **Schedule** (an Emit event schedule: its events,
source Schedule with the schedule's name as subject), search by ticket
key or title, and open an event to see its
changes, a link to the ticket, the raw data, its **Chain** (the event it came from and everything handlers made of it,
indented by level) and its **Handler runs** (each handler's result, with links to the event it emitted, or, for Send
to agent, the agent run's status, **Open run** and **Open notification**; events handled before agent runs existed show their old task status).

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
or `*` for every event) and whose **Conditions** all hold. Conditions compare a field — `source` (`jira`, `memory`, `schedule`, `handler` or `manual`), `type`, `subject`,
`title` or `payload.<field>` such as `payload.status` — with **equals**, **does not equal**, **contains** (text or
list item), **is one of** (comma-separated values), **matches regex** or **exists**. What changed in a Jira ticket is
under `payload.changes`: a changed status, assignee, priority, type or summary is `{from, to}`, so compare
`payload.changes.status.to` (or `.from`) — `payload.changes.status` with **exists** means "the status changed";
labels are `payload.changes.labels.added` and `.removed` (lists, use **contains**); a changed description is
`payload.changes.description` (use **exists**); new comments are `payload.changes.comments.added` (their ids). A Jira
event also carries every comment of the ticket, whole and oldest first, in `payload.comments` (each `id`, `author`,
`author_id`, `created`, `body`) and their number in `payload.comment_count`: a Send to agent message can answer the
latest one with `{{ event.payload.comments[-1].body }}`.

**Running a schedule.** An Emit event schedule (4.16) records `schedule.fired` with the schedule's name as subject
and title and the payload `schedule_id` (for reference),
`name`, `fired_at` (this run), `last_run_at` (the previous successful run; empty — `null` — on the first run) and `trigger`
(`scheduled` or `manual` for Run now). Under **Event type** the form offers **Schedule: <name>** for each Emit event
schedule: choosing one sets the type to `schedule.fired` and adds the condition `subject` **equals** the
schedule's name (replacing an earlier `subject` equals condition; it stays editable like any condition). Only handlers
with that condition run the schedule, and the Schedules page lists them; renaming the schedule updates the condition. A handler left over from a deleted schedule runs again when a
schedule takes that name. Use Send to agent with a fixed agent (the form offers Agent
mapped to the assignee and the Jira options only when the event type can match Jira events) and a prompt such as `Summarise what changed since {{ event.payload.last_run_at or "the last week" }}.` (the `or`
gives the first run a fallback; without it the prompt would say `None`). **Add handler** on the Schedules page opens this form already filled in.

A handler does one thing:

- **Emit event** creates a new event in the same chain. **New event type** (e.g. `triage.requested`), **Subject**,
  **Title** and **Payload** are Jinja templates over the matched event, available as `event` (`event.subject`,
  `event.payload.summary`, …). Empty Subject, Title or Payload copy the matched event's. Write payload values with
  `| tojson`, e.g. `{"summary": {{ event.payload.summary | tojson }}}`; the payload must be a JSON object. The new
  event is processed like any other, so handlers can build chains — at most 5 levels deep.
- **Send to agent** starts an ordinary agent run of the chosen agent for every matching event. The **Prompt** template
  is the run's first message, the run uses all of the agent's resources and every tool, and the handler's
  creator is the actor. The run is titled `<agent> · <event type> <event subject>`, appears in the agent's **Test** tab
  under **Earlier runs**, and can be opened and continued like any other run. When its first turn ends, one
  notification is written with the answer, or why it failed (4.19). With **Also post the answer as a comment on the Jira
  ticket**, the answer to a Jira event is also posted on its ticket as the Jira connection's user (see the Jira docs for
  the permission it needs); a failed run is not posted. A failed run is not retried: continue it, or send
  the event again. Any enabled agent can be chosen; its run gets all of the agent's resources. A handler whose agent was
  deleted or disabled can still be turned off or edited; choosing a new
  agent or turning it back on checks the agent again. Processing the same event again never starts a second run for
  the same handler.

  Instead of one agent, **Agent** can be **Agent mapped to the assignee** (Jira event types only): each matching
  event runs the agent mapped to the ticket's assignee under Jira → Settings → **Users and agents** (4.8). An
  unassigned ticket or an unmapped assignee starts no run. Add the condition `payload.for_assignee` **equals** `true`
  (the form offers it) so the agent starts only when the ticket is new and assigned, was reassigned, or got a comment
  from someone other than the connection's own user — not on every edit; `payload.assignee_id` is the assignee's
  Jira key. **Preview** says which agent would run, or why none would. With the answer posted on the ticket, **Give
  the ticket back after replying** (on by default for a new handler) assigns the ticket, once the comment is posted,
  to whoever had it before the agent's user, else to its reporter — never to a user mapped to an agent; the comment
  ends with `Assigned back to <name>.`. It is skipped when someone else took the ticket meanwhile.

**Careful with Send to agent on broad patterns.** Every matching event starts one agent run. The first scheduled Jira
pull records every ticket it fetches as **Ticket created**, so a `jira.ticket.*` handler runs the agent once per
ticket; `*` and other broad patterns also match the events other handlers emit, multiplying calls down a chain. Start
narrow (a specific type plus conditions), check with **Preview**, and widen later. The form warns when Send to agent
uses `*` or a pattern ending in `.*`. With **Also post the answer as a comment on the Jira ticket** on, the agent also
comments on every matching ticket — after the first pull, on every ticket it fetched.

Templates render in a separate process with a 2 second time limit (and, on Linux, a 512 MiB memory limit); a template
that overruns fails with `Template took too long to render.` or `Template used too much memory.`

**Preview** renders the handler against a recent event without saving: it says whether the event matches (or which
check failed) and shows the new event or prompt. A handler that fails — a template error, a disabled or deleted agent,
a chain that is too deep — marks the event **Failed** and records why under **Handler runs**; other handlers on that
event still run. Deleting a handler keeps its run history on the events; a schedule it ran keeps recording events
(the confirmation says so).

### 4.19 Notifications

Administration → **Notifications** lists what agents answered when a handler sent them an event, newest first: Time,
Status (**Succeeded** or **Failed**), Title (`<agent> · <event type> <subject>`) and the first line of the answer
(taken from its first 200 characters, cut off when long). Everyone sees the same list. Filter by status or search the
title; **Load more** and **Refresh** work as on Events. Open a notification to read the whole answer (or why the run
failed), the agent, **Open run**, which opens the run in the agent's Test tab, and **Open event**, which opens the event that
triggered it. On the event, the handler run shows the run's status, **Open run** and **Open notification**.
A notification whose answer goes to Jira shows **Posted to KEY** (opens the comment), **Posting to KEY…** while it is
being posted or retried, or **Not posted to KEY** with the reason. A handler that gives the ticket back adds
**Hand-back**: **Assigned back to <name>**, **Assigning back to <name>…** (retried on failure), **Not assigned back
to <name>** with the reason, or why it was skipped.
Notifications from before event runs show the old task's attempts and have no **Open run**.

### 4.20 Memory

Memory is a shared notebook for the whole project: agents save what they learn (a decision, a convention, a fact about a
system) and every agent can find it again in a later run. Each entry has a unique **name**, **content** (text, up to
20,000 characters), optional **metadata** (up to 20 labels such as `topic` or `system`) and the agent that saved it.
Names use lowercase letters, digits and `. _ / -` (up to 200 characters). Metadata keys are lowercase snake_case (up to
64 characters) and values are text of 1 to 500 characters.

**Tools.** Every run has the memory tools (group **Memory**):

- `search_memory` finds entries by meaning and by words. It can narrow by metadata and by the agent that saved them, and
  returns up to `limit` entries with the content cut at 2,000 characters.
- `get_memory` reads one entry whole by name.
- `save_memory` creates an entry. If the name already exists nothing is overwritten: the agent gets the existing entry
  back, merges its new knowledge into it and calls `update_memory`.
- `update_memory` replaces an entry's content and metadata. It must pass the **revision** it read; if someone changed
  the entry meanwhile, the update is refused and the current entry is returned, so the agent reads it and tries again.

In the tools, metadata is a list of `{key, value}` items; on the page and in the API it is a plain object. Agents cannot
delete or rename entries; only people can, on the page.

**The page.** Administration → **Memory** lists entries with name, metadata, who saved them (agent or person) and when.
Search by words; click a metadata badge to add it as a filter chip (click the chip to remove it); **Saved by agent**
shows only the entries of one agent; **Load more** shows older ones. Open an entry (or **New entry**) to edit its
**Name**, **Metadata** and **Content**, then **Save**. A link `/memory?entry=<id>` opens that entry directly. If an agent or another person changed the entry after you opened
it, Save says so and **Load current** brings in the latest version (your edits are replaced). **History** lists every
earlier version and shows what restoring it would change in the current text; **Restore** makes an old version the newest as a new revision,
so nothing is lost. **Delete** (after confirmation) removes the entry and its whole history.

**Embedding.** Entries are embedded with the RAG embedding model (4.3, RAG Settings) so search finds related wording. When
no embedding model is configured, or embedding fails, the entry is still saved and shows **Not embedded**: it is found
by words only. After fixing the model, **Re-embed** on the page embeds those entries.

**Events.** Every change records an event with the source `memory`: `memory.saved`, `memory.updated`,
`memory.restored` and `memory.deleted`. The payload holds `memory_id`, `name`, `metadata`, `revision`, `agent_id`,
`session_id` and `actor`, but not the content. Event handlers (4.18) can react, for example a Send to agent handler
with the condition `source equals memory` and `type equals memory.saved`.

### 4.21 System map

Environments → **System map** draws your inventory — environments, servers, services and code projects — and the
connections between them. Nothing is drawn by hand: add entries on their own pages, and connect them here.

- **Infrastructure.** Each environment holds the servers with a deployment in it, and each server holds the services
  deployed on it in that environment. A server in two environments appears in both; a server without deployments sits
  at the top level. Lines are network **connections** between deployments (with their port and protocol); a connection
  to an external host ends on a box for that host.
- **Services.** Every service and code project, with relation lines (*depends on*, *part of*, *built from*) and the
  connections rolled up to the services (*connects to*). Archived code projects are left out.
- **Browsing.** **Inside** lists what the current box holds (→ opens it; "+N inside" says it holds more), **Outside**
  lists what its connections reach elsewhere. The breadcrumb goes back up. **Depth** shows up to 3 levels; deeper
  entries fold into their box and lines add up (`connects to ×3`). When both ends of a connection are in one environment, the line stays inside it: it never joins one environment's copy of a server to another's. Everything you choose is in the page address.
- **Views.** **Diagram** is drawn by D2 with the layout you pick (tala, elk or dagre); **Mermaid** draws the same view
  with Mermaid; **D2** and **JSON** show the source, with **Copy**.
- **Connections and relations.** Click an entry's name: the panel links to its page and shows its documents; for a
  service it also shows its connections and relations. **Add a connection**: **From** one of the service's
  deployments, **To** another deployment (its port is filled in) or **An external host** (a hostname or IP, which needs
  a port), the **Port**, the **Protocol** (`tcp` unless you change it: 1–20 lowercase letters, digits or `. + -`) and a
  description. **Edit** changes an outgoing connection; **Remove** removes it. **Add a relation**: *depends on* or *is
  part of* another service, or *is built from* a code project, with a description; to change one, remove it and add it
  again. The Services page edits the same relations. A change someone made meanwhile is refused with a message and the
  panel reads the current state again.
- **Documents.** The panel lists the entry's documents from their links: its inventory document (**Inventory**) and
  every other document built for it or linked to it. **Link a document** finds a published document and links it to
  the entry; **Remove** removes that link (the document stays). A link a document was built for can't be removed.
  Deleting an entry removes its documents' links to it.
- **Upgrading.** From the stored graph: service dependencies and code project links became links (0095); nodes you
  drew by hand are gone and document attachments are not carried over (the documents keep the links they were built
  for). From links (0125): *depends on* and *built from* became relations; a *connects to* between two services became
  one connection per pair of their deployments in the same environment (a *depends on* when there is none); links on
  servers or environments were dropped. Environment membership without a deployment is gone (0126).

### 4.22 Prompts

**Administration → Prompts** (`/prompts`) is the library of reusable instructions for agents. Each prompt has tags —
lowercase letters, digits and `-`, at most 10 — that features use to find it: the agent chat on Documents → New filters by tag,
and a chat on a server, service or environment offers the enabled prompts tagged `assistant` and with its type
(`server`, `service` or `environment`), the seeded **Server assistant**, **Service assistant** or **Environment
assistant** first. Type a tag and press Enter to add it;
existing tags are suggested. Saving changed text creates a draft version; **Activate vN** makes it the active one;
inactive versions can be deleted. Changing only the name, description, tags or Enabled does not create a version.
Prompts that came with the application are tagged `documentation` plus what they document (`code`, `server`,
`service`, `mixed`).

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
5. **Docs → Documents → New → Write with an agent**: give a title and folder, open **Settings**, pick the service under Resources and the prompt, then tell the agent what to write. The agent reads the code projects the service links.
6. Follow the chat, send messages such as "shorten the API section", then **Promote** the draft.
7. Make the documents searchable through a collection that holds `generated-documents` (`documents` on upgraded installs); **RAG → Collections** → **Refresh** it.

### C. Document deployments and infrastructure

1. **Servers**: register hosts. **Environments**: create `production`.
2. **Services**: add deployments (server + environment + container/unit + port + log path); the services and servers
   are then in `production`. On the **System map**, add the connections between the deployments.
3. **Documents → New → Write with an agent**: in **Settings** pick the environment (or the servers) and ask for the
   deployments to be documented.
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

1. **Documents → New → Write with an agent**: pick the code project in **Settings**, ask for its documentation, then **Promote** the draft.
2. **Docs → Folders** → **Index in RAG → Collections** → **Create collection** (the folder
   is selected; the sync starts at once).
3. Grant the collection to agents in **Agents**.

### G. Write documentation interactively with an agent

1. **Docs → Documents → New → Write with an agent** → give a title and folder, choose the resources and the prompt in **Settings**, and tell the agent what to write.
2. Follow the stream and send a message such as "shorten the API section".
3. **Promote** the draft.
4. **Docs → Folders** → **Index in RAG → Collections**.

---

## 6. Operations

### Configuration (`.env`)

| Variable | Meaning |
| --- | --- |
| `ADMIN_PORT` 8183, `KEYCLOAK_PORT` 8181, `PHOENIX_PORT` 8182, `POSTGRES_PORT` 5438, `JIRA_MOCK_PORT` 8184 (Jira mock page) | Loopback ports |
| `WEB_HOST` | Public host name used for login redirects |
| `DATA_ROOT` | **Required.** Absolute host folder for the application's files (set by `make setup DATA_ROOT=...`) |
| `CODE_FOLDER` | Folder inside `DATA_ROOT` for code projects and Git clones (default `code`) |
| `CODE_INDEX_FOLDER` | Folder for code graph snapshots (default `code-index`) |
| `DOCS_FOLDER` | Folder for bootstrap docs (default `docs`; seeded only when empty) |
| `GENERATED_DOCS_FOLDER` | Folder for document exports (default `generated-documents`) |
| `AGENT_SESSION_MAX_SESSIONS` | Sessions running at once per agent-session-worker (default 4) |
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

Every run gets every tool below; the "Works when" column says when a tool acts rather than refusing (the run's
resources and roles decide).

| Tool | Works when | Safety limits |
| --- | --- | --- |
| `list_files`, `read_file`, `search_files` | The run has code projects or collections | Only selected roots plus a scratch workspace; symlinks resolved; binaries refused; reads ≤ 64 KiB; secrets redacted |
| `shell_run` | The run has code projects or collections | Runs in the separate `shell-worker` in a bubblewrap sandbox: no network, no capabilities, empty environment, sources mounted read-only, only the scratch workspace writable; 1–300 s; 32 KiB output |
| `code_graph` | The run has the code project and it has a generated graph | Indexed symbol, route and call queries on a disposable snapshot copy |
| `code_search` | The run has code projects | Tree-sitter function lookup (Go, Python, JS, TS, TSX) |
| `ast_grep` | The run has code projects | Structural code queries; 30 s timeout |
| `ssh_run` | The run has the server and it is allowed SSH with a key | Runs **any** command (not read-only!) under `timeout` + `setpriv --no-new-privs`; root only via `sudo -n` when allowed; host key checked only if trusted keys are saved; 32 KiB output |
| `get_resource`, `get_document` | Inventory tools: the run was given the resource or document | Read-only: `get_resource` answers with what is recorded about one server, service, environment or code project the run was given (its connection, instructions, services, documents and links to related resources), and `get_document` reads documents the run was given or linked to those resources. Anything else gets "The server … is not available in this run." (or service, environment, code project; "This document …" for documents), the same answer as for something that does not exist |
| Proposal tools | **Propose…** wizard runs | Act only in a content-editor run started from a server, service or environment (the **Propose…** wizard asks for the role); `propose_service` only in a run started from a server. A plain chat refuses them. They record proposals that you accept or reject, and write nothing to the inventory themselves |
| MCP tools | The run has the MCP server | Every tool of the tested MCP servers in the run's access |
| `search_documents`, `read_document` | The run has collections | Search and read only the granted RAG collections; **Answer only from documents** asks the agent to answer from them and cite every claim |
| `report_not_covered` | **Answer only from documents** on (else "This run does not answer only from documents.") | Records what is missing and marks the answer **not covered** |
| `run_subagent` | Agent sessions (never inside a subagent) | Starts a helper with every tool but `run_subagent`, a subset of the run's resources and no conversation; at most 16 helpers per turn (with expert calls), `AGENT_MAX_SUBAGENTS` at once; only the credentials its configuration needs; result redacted |
| `ask_<name>` | An agent is offered as an expert (**Offer as an expert**) and the run did not leave it out (sessions only) | Runs that agent without the conversation, with its own prompt, tools, model and keys (on its own resources, or a subset of them you pass); no expert tools inside an expert; counts towards the 16 helpers; result redacted |
| `load_skill` | The skill is in the run's access and enabled | Returns the skill's instructions by name; any other name gets "This skill is not available in this run." |

Every tool works on the run's resources only; a kind the run has none of is refused with `This run has no ….`

Every agent's instructions include "use only configured resources; never display
secrets", and secrets are redacted from activity, answers and traces. Skills are
instructions loaded with `load_skill` and services add context; neither adds tools.

**Treat SSH access with care**: an agent with SSH enabled can change the server.
Use a dedicated low-privilege user, keep **Allow sudo to root** off unless needed,
and always save trusted host keys.

---

## Appendix B: Where older docs disagree with the code

| Document | Claim | Actual behavior |
| --- | --- | --- |
| README.md | Agent Chat/Test returns `TODO` | Agent **Test** really runs models |
| docs/documentation.md | One form with two scopes | Workflows: create per service/environment, then run Phases 1–4 and additional documents |
| docs/documentation.md | 16,384-token output limit for all runs | Not applied to per-endpoint API analysis |
| docs/code-processing.md | Choose a service's "source folders" | You choose code projects; folders are mapped in Code projects |
| docs/architecture.md | Worker serializes jobs per project | The repository worker processes one job at a time overall |
| Skills (migration 0059) | `api-analysis` skill for API documentation | Not wired into any documentation flow |
