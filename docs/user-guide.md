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
  - [4.3 Code projects](#43-code-projects)
  - [4.4 Servers](#44-servers)
    - [Chats and Discover](#chats-and-discover)
  - [4.5 Environments](#45-environments)
  - [4.6 Services](#46-services)
  - [4.7 Jira](#47-jira)
  - [4.8 Skills](#48-skills)
  - [4.9 MCP servers](#49-mcp-servers)
  - [4.10 Agents](#410-agents)
  - [4.11 Write a new document with an agent](#411-write-a-new-document-with-an-agent)
  - [4.12 Documents](#412-documents)
  - [4.13 Storage](#413-storage)
  - [4.14 Agent runs](#414-agent-runs)
  - [4.15 Schedules](#415-schedules)
  - [4.16 Events](#416-events)
  - [4.17 Event handlers](#417-event-handlers)
  - [4.18 Notifications](#418-notifications)
  - [4.19 Memory](#419-memory)
  - [4.20 System map](#420-system-map)
  - [4.21 Prompts](#421-prompts)
  - [4.22 Output schemas](#422-output-schemas)
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

- **Keep documentation in one place**: write and version documents in **Docs → Documents**,
  by hand or with an agent; agents find them with full-text search.
- **Map source code**: register local folders or clone Git repositories, keep them
  pulled on a schedule, and build a **code graph** (symbols, relationships, routes).
- **Keep an operational inventory**: services, their code, environments, servers,
  deployments and service-to-service dependencies.
- **Write documents with an agent**: on Documents → New, chat with an agent that has the code,
  documents, servers and services you allow; it saves its work as a draft.
- **Configure and test AI agents** with prompts, skills, documents, code, SSH servers
  and MCP tools, and run them interactively (agent **Test** chat).
- **Import Jira tickets** into the database, on demand or on a schedule.

### Architecture at a glance

```
Browser ──► admin-oauth2-proxy :8183 (docs-admins only) ──► admin-web (nginx, Admin UI) ──► admin-api
                                                                                    │
     admin-api ──► PostgreSQL, shell-worker (sandboxed bash), LLM providers, SSH hosts, Jira, MCP servers
     workers:  repository-worker (git clone/pull, code graph)
               agent-session-worker (document, Test, Ask agent and event-handler sessions)
```

`agent-session-worker` runs document, Test and Ask agent sessions, and the agent runs that event handlers start. Like `admin-api` it reaches
PostgreSQL, MCP servers, model providers and SSH hosts, and runs `shell_run` in `shell-worker`; it reads `DATA_ROOT`
read-only.

All ports bind to `127.0.0.1` only. Internal services have no host ports.

---

## 2. Install, start and sign in

Requirements: Docker with Compose, Python 3.10+ (for the setup script). No model
credentials are needed to start.

```sh
make up          # generate .env secrets, build images, migrate DB, start everything (including the Jira mock)
make smoke       # verify admin login, gateway isolation, DB migrations, Phoenix (no model calls)
```

- The first time, choose where the application keeps its files:
  `make setup DATA_ROOT=/opt/knowledge-agent` (an absolute path; create it with `sudo`
  and `chown` it to your user if needed). `make up` refuses to start without it.
- `make up` runs `scripts/setup.py`, which creates `.env` (mode 0600) from
  `.env.example`, fills empty secrets with random values (never overwriting existing
  ones), records `DATA_ROOT`, and creates `code/`, `code-index/` and `docs/` inside it
  (the names come from the `*_FOLDER` variables). It warns about any other entry in
  `DATA_ROOT`, because every service sees all of it; it never moves or deletes them.
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
   embeddings model (AWS Bedrock Titan v2) for memory vector search.
2. **Memory** → select the embeddings model if you added one.
3. **Code projects** → map local folders or clone Git repositories; **Generate graph**.
4. **Servers** → register hosts; configure **SSH settings** if agents or deployment
   documentation should inspect them.
5. **Environments** → group servers and services (e.g. `production`, `staging`).
6. **Services** → describe applications, link code projects, add deployments and dependencies.
7. **Docs → Documents** → **New** → **Write with an agent**: give a title and folder, then tell the agent what to write.
8. **Agents** → create agents, assign a model, enable capabilities, test them.

---

## 4. The Administration application (port 8183)

Requires membership in `docs-admins`; otherwise you see "Administration unavailable"
with a **Sign out** button. `/` redirects to **Docs → Documents**.

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
| Agents | All agents · Output schemas |
| LLM Settings | Models |
| Jira | Tickets · Settings |
| Environments | Environments · Services · Servers · System map |
| Code | Projects · Code graphs |
| Docs | Documents · Folders |

Footer: theme toggle and **Sign out**.

### 4.2 LLM Settings

`/llm-settings` — named model connections used by agents
and memory embeddings.

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
| Supports structured output | Chat models: the model accepts a JSON Schema as its answer format (on by default for OpenAI compatible). Off: a run with an output schema gets the schema in its instructions instead; its answer is still checked (4.22) |
| Enabled | Disabled models cannot be selected or tested |

**Test connection** sends "Reply with OK." (or one embedding) with a 35 s limit.
Changing provider, URL or region requires re-entering or removing the key. There is
no delete — disable a model instead.

> **Compatibility rule.** Agents and documentation generation require a "code-compatible" model: OpenAI
> **Responses** API, Anthropic, or Bedrock `anthropic.claude*`. OpenAI
> **Chat Completions** models can be assigned only as the Search model in LLM Settings.

### 4.3 Code projects

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
prerequisite for code documentation phases ([4.11](#411-write-a-new-document-with-an-agent)).

Code projects only clone and pull into `code/…`. Agents read a project's folders with their
file and code tools.

**Remove** (in the project's row) asks for confirmation and lists the project's folders. The
project disappears from Code projects, services and agents, its
scheduled pulls stop, and its folders and Git URL can be used by a new project. Files on disk
are kept unless you tick **Also delete these folders and the code graph from disk** (off by
default; it cannot be undone). Deleting is refused, and nothing is removed, when another code
project maps one of the folders (the same folder, a parent or a subfolder).
If a folder cannot be deleted afterwards (for example, permissions), the project is still
removed and the page names the folder left behind. The API is
`POST /api/code-projects/{id}/archive` with an optional body `{"delete_files": true}`.

Projects cannot be edited or removed while a job runs.

### 4.4 Servers

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

A server's name opens its page (`/servers/:id`): a header (details, SSH state; **Chat** and **Discover** on
the right) and tabs **Overview** (the services deployed on it) and **Documents**; the open tab
is kept in the URL (`?view=documents`). Its chats are in the chat panel (see below). Services (`/services/:id`: Overview is the service panel) and
environments (`/environments/:id`: the first tab is the Workspace) have the same kind of page. In the environment
workspace, the selected service's column has its own tabs **Details** and **Documents**.

A server's, service's or environment's **inventory document** is the document tagged `inventory` built for it (the
most recently updated one when several; the others are in its Documents list): the Documents tab shows it rendered, with **Open** to
edit it (through drafts, like any document; one that is only a draft is marked "Draft — not published"); without one
there is no box. **New document** in the tab's header opens the New document page built for the resource
(`/documents/new?resource=server&resource_id=…`: "New document for server web-1.", the resource locked in Linked
resources, no tags), and **Save** creates its draft; **Write with an agent** there writes a document built for that
resource alone (untagged; it is listed on the resource's Documents tab as a draft and linked to it when published —
unless its name matches a published document, whose new version it then drafts instead). Below it, the **Documents** list shows every
published document linked to the resource (built for it or linked) and to its direct children — an environment's
services and servers, a server's services, a service's code projects and the servers it is deployed on: each row has
the title (opens the document), its tags (each opens the Documents list filtered by it), the first line of its
description, and "· <name>" when it belongs to a child. The resource's inventory document comes first, then its other
documents, then the children's, grouped by child; a document linked to several of them is listed once. With none:
"No documents yet.". Each row has two icon buttons: **Edit** opens the document's page (a new document's draft opens
at `/documents/<id>` too), and **Delete** asks first — "Delete <title>?", "It is deleted everywhere, with its
history." and, when the document is linked to other resources, "It is also linked to: <names>." — with **Cancel** and
**Delete document**. For a new document's draft it asks "Discard draft <title>?" ("The draft is discarded; nothing
was published.") and **Discard draft** discards the draft. When the document changed meanwhile, the server's message is shown above the list and the list reloads.

#### Chats and Discover

**Chat** opens the chat panel on the latest open chat (or a new one). The panel's header shows the open conversation's
title ("New conversation" for a new one not started yet), then **Conversations**, **New chat** and **Hide chat**.
**Conversations** opens a sheet on the right (like **Run details**) listing every conversation about the server
(service, environment), newest first, each with its status and last activity, the open one marked; picking one opens it
and closes the sheet. With none: "No earlier conversations." **New chat** starts a new conversation (Conversations
still lists the old ones). There is no Chats tab. The chat stays hidden until you ask for it;
on very wide screens (2xl) it opens beside the Info, below that as a second tab (**Info** / **Chat**). The URL keeps
the open chat (`?chat=<id>`), so a reload or a link opens it again; a chat of another page is refused.

A chat answers questions about the resource: its prompt is the resource type's assistant prompt (**Server
assistant**, **Service assistant**, **Environment assistant** in **Administration → Prompts**), which uses the
smallest read-only command that answers. **Settings** locks the resource on; you may add other resources. A server
chat needs SSH to be set up and allowed for the server. A plain chat only drafts: documents it writes
and inventory it saves are drafts for you to confirm.

Every chat (here, Ask agent beside a document, Documents → New and an agent's Test tab) has a **Debug** tab beside the
**Conversation**. It shows what the model receives, built by the same code that runs it: **Instructions** (the whole
system prompt, with **Copy**), **Prompt** (the library prompt and version it came from, linked; the agent's
instructions and skills), **Tools** (each name and description as the model sees it),
**Resources** and **Model**. Before the first message it shows what the next conversation would start with; secrets
are never shown.

**Discover…** starts a run that explores the resource from a library prompt. The sheet offers the prompts (the seeded
**Server discovery** is chosen when the library has it) and shows the prompt's text, which you may edit; the chat
settings of the resource give the model and resources. **Start** starts the run and opens it as the page's chat, so you
can watch it and write to it while it works. The run saves what it finds as drafts: services, deployments,
connections and relations in the inventory (`save_inventory_draft`), documents with `new_document` (linked to the
resource it is for) or `update_document` / `edit_document` (a document that already exists). You confirm or discard the
inventory drafts in the environment workspace, and publish the documents on their pages (**Documents** tab of the
resource, marked "Draft — not published"). Nothing the run writes is live until a person publishes it.

The agent is told to use read-only commands, but SSH itself is not read-only: anything
the login can do, the agent can do. Use a login with only the permissions you want the
agent to have.

### 4.5 Environments

`/environments` — named environments such as `prod` or `staging`. An environment has a name and a description, its
member servers, and the deployments of services on them. A server is in an environment when it is added to it or has
a deployment there; it stays a member (empty) when its last deployment there is removed. A service is in an
environment when one of its deployments is; a service deployed in two environments is in both.

- The list shows each environment with the number of services and servers in it: filter, **Map** (the System map
  rooted at the environment), **Documents**, Edit, Delete; **Details** lists what is in it.
- An environment's page (`/environments/:id`) has a header (name, description, **System map**; **Chat** and
  **Discover** on the right; Chat opens the agent chat beside the page, its Conversations sheet lists the earlier
  chats about the environment) and two tabs, the open one kept in the URL (`?view=documents`; none: Workspace):
  - **Documents**: the environment's own inventory document and documents (a server's or service's documents are in
    its column of the workspace);
  - **Workspace**, of three columns:
  - **Servers**: the member servers, each with its number of services ("no services" for an empty member), **Edit**
    and **Remove from environment** (refused with "Remove the deployments on this server in this environment first."
    while it has deployments there). **Add server** picks a server that is not in the environment yet, or creates a
    new one (name, hostname, description).
  - **Services on** the selected server: its deployments in this environment. **Add service** picks a recorded service
    or creates a new one (name, type, role, owner, description), plus the deployment on this server: runtime, runtime
    name, port, version, log location.
  - The selected **service** (the service column; the service page shows the same panel):
    - its fields (type, role, owner, description, and the address of an external service) with **Edit**, and a link
      to its page. The service page also shows the agent context link, each code project's folder and each
      deployment's SSH status;
    - **Deployment here** (runtime, runtime name, port, version) with Edit and Remove (removing a deployment also
      removes its connections), and **Also runs on**: its other environments and servers, each a link to that
      workspace. The service page lists every deployment under **Runs on** instead;
    - **Connections** out (→) and in (←), each with Edit and Remove. **+** adds a connection from the deployment here
      (on the service page, from the deployment you choose) to a deployment of another service (the picker groups them
      by environment and server), to an **external service** (a recorded one, or **New external service**: name,
      address and description, created with the connection), or to a host, with port, protocol and description. Each
      connection's other service is a link to its page. A connection to an external service belongs to no deployment
      of it, so it is listed under every deployment of that service;
    - **Relations**: depends on, part of, and the reverse (needed by, has part), each with Remove; **+** adds a
      depends on or part of relation to another service;
    - **Code projects** the service is built from, each with Remove; **+** adds one;
    - **Documents**: the service's inventory document and documents, as on any resource page.

    Every Remove (a deployment, connection, relation or code project, and Remove from environment in the servers
    column) asks first in a dialog that names what goes; Cancel removes nothing.

    Every change sends the revision it read. When someone changed the item meanwhile, the column says "This was
    changed meanwhile. Reload to see the latest." and reloads; other refusals, such as a duplicate, show their own
    message.
- The selection is in the URL (`?server=<id>&service=<id>`), so links, reload and Back keep it; a server or service
  that is no longer in the environment selects nothing. Narrow screens show one column at a time, with a back link to
  the previous one. The workspace reloads after every change.
- An environment with deployments cannot be deleted: remove or move its deployments first.

#### Drafts

Everything an agent writes to the inventory — environments, servers, services, deployments, server memberships,
connections and relations — is a **draft**: it is in the inventory with a **Draft** badge, but only a person makes it a
real entry (**Confirm**) or drops it (**Discard**). Agents never change a confirmed entry.

- **Where drafts appear.** In the workspace on the server rows (a draft server, or a server put in the environment by
  a draft membership) and the service rows, and in the service panel on the service itself, its deployments,
  connections, relations and code projects; on the server page's services table and the service page's panel; and on
  the Environments list (a draft environment). A draft environment and draft external services are listed under
  **Other drafts** above the workspace columns. Each draft has **Confirm** (✓) and **Discard** (✕). The server page's
  header shows a draft server's badge only: confirm or discard it in the environment workspace.
- **Confirm** first lists what it also confirms, then confirms them together: a draft is confirmed with every draft it
  needs — a deployment with its server, service and the server's membership of the environment, a connection with its
  deployments (and what they need) and its external service, a relation with both services, a membership with its
  server, and anything in a draft environment with the environment. On a server row, Confirm confirms its membership
  (and the server with it).
- **Discard** first lists what it also deletes, then deletes the draft and the drafts that depend on it: a draft
  deployment takes its draft connections, a draft service its draft deployments, connections and relations, a draft
  server its draft memberships and deployments. It never deletes a confirmed entry. On a server row, Discard deletes a
  draft server (with its membership), or only the draft membership of a confirmed server.
- **Your own changes.** What you add or change is confirmed — except that editing a draft keeps it a draft (confirm it
  when it is right). A confirmed entry never uses a draft, so when what you save uses drafts (a connection to a draft
  deployment, a service added on a draft server, a relation to a draft service — in the workspace, on the System map
  and in the Services form), the form says "Saving confirms the drafts it uses: …", naming the draft deployment, its
  service and server, the server's draft membership ("db-1 in prod") and a draft environment, and saving confirms them
  with it.
- **Filter.** Above the workspace columns, **All**, **Confirmed** or **Drafts** (`?drafts=none` or `?drafts=only` in
  the page address). Drafts keeps every server and service a draft is about: a draft server or membership, a draft
  deployment, and a service with a draft connection (from or to it) or a draft relation, which its service panel
  shows. The filter covers the servers and services columns; the service panel always lists all its connections and
  relations. The environment's header says "N drafts to review" while it has drafts; the link opens the workspace with
  the Drafts filter (an open chat stays open). The count includes document drafts (new documents, and drafts of
  documents) linked to the environment, its servers and its services (also a draft whose only link there is the one it
  adds on publish); the text then says "3 drafts to review (1 document)". When only document drafts wait, the link
  opens the **Documents** tab, whose **Drafts to review** section at the top lists every one of them: its title (a link
  to the draft), the service, server or environment it belongs to, and "New document" or "New version". The
  environment's own documents follow below. On a resource's Documents tab a new document's draft is listed marked
  "Draft — not published", and a document with a draft waiting shows "Draft waiting"; the System map's entry panel
  lists a new document's draft only once it is published.
- **After the agent's turn.** The workspace, the header's count, the Documents tabs (with Drafts to review), the server
  page and the service panel reload after each turn of the page's chat, so what the agent drafted appears without a
  reload.
- **Map.** On the System map, draft boxes and lines are dashed (a line that adds up several connections is dashed only
  when all of them are drafts), and **Show drafts** (on by default; `drafts=0` in the address when off) hides them.
- Someone who changed a draft meanwhile makes Confirm and Discard say "This was changed meanwhile. Reload to see the
  latest." and nothing changes.

### 4.6 Services

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
  also removes its connections: Save first asks, naming each deployment and how many connections it takes with it.

**Save** writes the service, then each changed deployment and relation through the inventory API (one request each).
If one of them is refused (for example a duplicate deployment, or someone changed the service meanwhile), the form
stays open with the message; what was already saved stays saved, and saving again sends only the rest.

**Details** shows everything above plus code commits and SSH status per deployment,
and links to the service's documents and environments. **Agent context (JSON)**
opens `/api/services/{id}/context`, a read-only, credential-free JSON view of the
service for agents. Note: the *downloaded* commit is not the *deployed* version.

**External services**: a service of type External service stands for a system outside your environments (a payment
provider, a SaaS API). Give it an **Address** (a hostname or IP address; the field is shown for this type only; Add
service and New external service in Add connection have it too) and connect deployments to it by name instead of
repeating its host on each connection; it can have documents and relations like any service and needs no deployment. A service that connections point to keeps the type External
service until those connections are removed. A bare host on a connection is still possible for an address nobody has
described yet.

A service with deployments cannot be deleted: remove its deployments first. Deleting a service removes its relations
but not code projects or servers.

### 4.7 Jira

**Settings** (`/jira/settings`) — Jira Data Center URL (include context path such as
`/jira`; use `host.docker.internal` for a Jira on your machine), username, personal
access token (encrypted). Save, then **Test connection** (checks the user identity
and search permission).

**Tickets** (`/jira/tickets`)

1. Enter a **JQL** query (default `ORDER BY updated DESC, key DESC`) and the number of
   tickets (1–200), then **Fetch tickets**. Results are stored locally.
2. Filter by text, project and status. Tickets are read-only here; agents and event handlers use them from the database.

**Users and agents** (on the Settings page, once a connection is saved) maps Jira users to agents: a ticket assigned
to a mapped user is that agent's work. Search for a user as you type (or type a user key), choose an agent, **Add**;
change a row's agent in place or remove it. A row whose agent is turned off says **Agent disabled**. An event handler
with **Agent mapped to the assignee** (4.17) runs the mapped agent. The first mapping creates that handler when there
is none: **Jira assignee agents** (every ticket event with new work for the assignee; the answer is posted on the
ticket and the ticket is given back). The section shows **Handled by: Jira assignee agents (enabled)** (or
**disabled**), which opens the handler, or **No handler runs these agents.** with **Create handler**. The handler is
an ordinary one: edit, turn off or delete it under Events → Handlers; changing mappings never changes it.

For testing without a real Jira: the mock starts with `make up` (`make jira-mock-up` rebuilds only it), then URL
`http://jira-mock:8080/jira`, user `mock-admin`, token `jira-mock-token`
(75 demo tickets). Stop with `make jira-mock-down`.

**Auto pull.** The automatic pull is a schedule, set on Administration → Schedules (4.15): **New schedule** → Jira
tickets, with its own query and number of tickets (separate from the query on Jira → Tickets; left empty, the query and
number of the last manual fetch are used, or 25 tickets), how often and the time zone. **Edit** there changes them;
**Run now** pulls at once. Jira → Tickets shows a one-line summary (off, or how often and the next pull) with a link to
the Schedules page. Jira → Tickets always shows the tickets from the
most recent pull, manual or automatic; its query box keeps your manual query. A failed pull is shown and retried at the next scheduled time; a pull that meets a manual fetch is skipped. Every pull, manual Fetch
tickets or scheduled, records an event for every ticket that is new or changed since the last event for it (see
Administration → Events), in the same transaction as the saved tickets.

### 4.8 Skills

`/skills` — a library of reusable Markdown instructions that agents can be given.
Fields: name (lowercase-with-dashes, unique), description, instructions (Markdown),
enabled. Search and filter by status; toggle enabled inline. Skills are instructions only (never executed) and cannot
be deleted — disable them. A run lists the skills in its access by name and description; the agent loads a skill's
full instructions with the `load_skill` tool when it uses it.

### 4.9 MCP servers

`/mcp-servers` — external tool servers agents can call. Only the **Streamable HTTP**
transport is supported. Fields: name, description, HTTP(S) endpoint (use
`host.docker.internal` instead of `localhost`), optional bearer token (encrypted),
enabled.

**Test connection** discovers the server's tools (25 s limit). A server can only be
selected for an agent after it is enabled, tested successfully and exposes at least
one tool. Changing the URL clears discovered tools and requires re-entering the token.

### 4.10 Agents

`/agents` lists agents with **Resources**, capabilities, model, active system prompt and status. **Resources** counts what the agent may use by type, for example `1 server · 2 documents` (or `No resources`).

**Create an agent** with **New agent** (the only way to create agents). It opens the wizard directly. Its first field is **Agent model** (the model the agent runs on; it defaults to the first model in the catalog). Both a generated proposal and **Start blank** use that model; to change it go back with **Edit role**:

- **Generate proposal** — describe the role, choose a generator model, and the model
  proposes the name, prompt, instructions and resources (servers, services, environments, code projects,
  documents, skills and MCP servers).
- **Start blank** — skip generation (no model needed) and fill in the same review tabs
  yourself.

Review and edit the tabs (General, Prompt, Resources), then **Create agent** (enabled once name and system prompt are
filled in). The agent model is optional — an agent without a model is
saved but runs only after you choose one with **Model** on its General tab.

**Agent detail tabs** (`/agents/:id`):

| Tab | What you do there |
| --- | --- |
| **General** | **Model** (required to run; saved with **Save details**), name, purpose, when-to-use, **Instructions** (added to the system prompt), **Enable agent**; under **Advanced**, **Tools** (the built-in tools grouped as Documents, Inventory, Code, Servers, Memory and Agents; untick the ones this agent must never get, **All** / **None** per group, "N of M tools on"; tools added later are on); **Offer as an expert** (other agents can call this agent as a tool; needs When to use; an **Expert** badge shows in All agents); the **System prompt** card (one field, up to 100,000 characters; **Save prompt**); *Clone of <original>* for a clone |
| **Resources** | What the agent may use, grouped by type: Servers, Services, Environments, Code projects, Documents, Skills and MCP servers. Add from the project's resources (or **Select all** of a type) and **Remove**. A resource that no longer exists shows as **missing** and is dropped when you save. **Save** writes them all |
| **Test** | Chat with the agent; **Settings** may pick some of its resources for the run |

An agent has one configuration: what its tabs show is what runs. A conversation keeps the configuration it started with.

**Run an agent.** **Run** (the play button) on an agent's row, or **Run agent** next to **New agent**, opens the *Run
agent* dialog: the agent (preselected from the row; from the top button choose one of the enabled agents — a disabled
agent's **Run** is off), the **First message** (required) and its resources — every resource of the agent unless you
narrow them with **Choose resources**, the same settings sheet as the Test tab's (without its Output schema) — and an
optional **Output schema** in the dialog itself (4.22). **Start** starts a chat session and
opens its session page (`/sessions/<id>`), where you send further messages, **Stop** a turn and **Close session**. A
chat is not listed on the agent's Test tab; its turns are **Chat** runs in Agent runs (4.14), and it can be reviewed
with **Suggest improvements** through the API like a Test session.

**Experiment on a clone.** Open the agent's header menu (*Actions for <name>*) or the **Clone <name>** button in the All agents list and choose **Clone agent**: the clone has the same configuration, references the same saved SSH credentials, starts without history and shows *Clone of <original>*. Change and test the clone, then **Apply to <original>**: the dialog lists the changed sections, and everything except name, description and enabled is replaced. Apply is refused while the original is working or when the original changed since you opened the dialog; if the comparison fails, **Compare again**. Afterwards **Delete this clone** removes the clone (its dialog lists the test chats and other history deleted with it), or keep it as an alternative.

**Test tab.** The Test tab is the same chat as **Ask agent**. Type a message and send it: it starts a Test run, which the
agent session worker runs (the URL keeps `session_id`). The workspace shows your message, a collapsed **Worked** block
(thinking, tool calls with their results, comments you sent while it worked) and the answer; the
header shows the tokens, model calls and tools used, and the sidebar **Agents in this run** the agent's status (on a
narrow screen open **Run details**). **Send message** works while the agent works (your comment reaches it at its next
step); **Stop** ends the current turn.

**Settings** opens a sheet. A Test run can only narrow the saved agent; nothing can be added. The model and prompt are
shown read-only ("Change them in General."); to try another prompt, clone the agent. In the sheet you can
choose some of the agent's experts and some of
its resources (see *Resources of a run* below). **Preview** shows the
configuration the run would use. **Output schema** (none, or a schema of the library with **Current** or a fixed
version) makes the next run finish with JSON in that schema (4.22); the current settings line under the heading then
ends with *Output: <schema>*. **Start new chat with these settings** keeps **Current** for a run started with Current
(not the version it pinned). Settings are remembered per agent in this browser and reset when the agent is saved.
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

**Tools.** Every run gets every tool it has not switched off: under **Advanced** (collapsed below the General fields) in an agent's settings, **Tools** lists the built-in tools by group; an off tool is never offered to that agent, and tools added later are on. In a chat's **Agent settings** (Advanced → Tools) a conversation can switch more tools off, never back on one its agent has off: those show as *Off for this agent* and are locked. What a tool may do follows the run's resources (there are
no roles): a tool without what it needs refuses (for example `This run has no servers.`). Every agent can read the inventory of its resources
(`get_environment_model`, `list_servers`, `list_deployments`, `list_connections`), and every agent in a conversation
can add to it as **drafts** (`save_environment`, `save_server`, `save_service`, `save_deployment`, `save_connection`,
`save_relation`, `remove_inventory_item`, and `save_inventory_draft` for a whole picture in one call), only for what is
in its resources or what it added in the same session — so it can draft a server, deploy a service on it and connect it
in one conversation. An agent never changes a confirmed entry: it adds and changes drafts, and a person confirms or
discards them in the environment workspace. Its drafts show in the audit log with the person who started the session,
the agent and the session; helpers and experts never change the inventory. Documents work the same way: every agent
in a conversation reads the documents of its resources (`list_documents`, `get_document`) and writes **drafts** of
them — a new document for a server, service or environment it has (`new_document`, built for it, in its folder; a
title already used there is refused with the existing document's id), the whole text of a document it may read
(`update_document`, which can also link the document to another of its resources once published) or one passage
(`edit_document`). Each draft waits on the document's page (**Draft — not published**) until a person publishes or
discards it; nothing needs a role. **Agent runs**, run
records, session pages and Test chats show how often each tool was used.

**Subagents.** Every agent can start helpers with `run_subagent`: each gets a complete task, every tool except
`run_subagent`, and some or all of the run's resources, but not the conversation (helpers only read: no document or inventory
writes). Helpers run in parallel (`AGENT_MAX_SUBAGENTS`,
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
agent has: a pick outside its resources is refused (`server <id> is not in the agent's access`). What a run was given brings its direct children: an environment its services and
servers, a service its code projects and the servers it is deployed on, a server the services deployed on it
(`get_available_resources` marks them `via <type> <id>`). The tools reach exactly the run's resources; a kind the run
has none of is refused (`This run has no code projects.`). Example: a **Code analyst** with several code projects,
tested with `payments-api` in one conversation and `helpers` in the next.

Limits: message ≤ 20,000 characters; a Test session's turns have no time
limit (they end with the answer, **Stop**, **New chat** (it closes the open run) or a lost worker), and a session closes after 24 hours
without activity. A message is blocked if the agent is disabled, has no enabled model, or other tabs have unsaved
changes.

Saving the agent's settings changes what runs next. Migration 0133 removed **Answer only from documents** (agents find
documents with `search_documents` and read them with `get_document`).


Removing an agent that has history (test chats, chats started with **Run agent**, documentation sessions or run records
of earlier test chats) lists that
history in the **Remove agent?** dialog. **Delete agent and history** deletes the agent together with it; saved
documents are kept. To keep the history, cancel and disable the agent instead. An agent that is working right now
(a Test or document session that is starting or working) cannot be removed; stop that work or wait for
it to finish first.
See [Appendix A](#appendix-a-agent-tools-and-safety) for the tools an agent receives.

### 4.11 Write a new document with an agent

**Documents → New** has a **Write with an agent** button (`/documents/new`). It opens a chat with an agent that writes a document that does not
exist yet. It is the same chat as **Ask agent** on a document ([4.12](#412-documents)): the page takes the full width, the
document takes two thirds and the chat one third.

1. Give a **Title** and a **Folder**. The Folder field appears with the chat. Without both, nothing starts. Folder names are
   lowercase (`Payments` and `payments` would be the same folder on macOS): each segment uses lowercase letters, digits,
   '.', '_' and '-', does not start with '.'; `node_modules` and a first segment `services` are not allowed; at most 200
   characters. The title needs ASCII letters or digits (the file name is made from them).
2. Message the agent: what to write and what to read. It writes the document with `update_document` and changes it with
   `edit_document`. Each save updates the run's draft; you can ask for more changes.
3. A bar above the editor shows the draft. **Compare** opens the draft page. **Promote** creates the document and opens it.

If the title and folder match an existing document, the agent's saves become a draft of that document. Nothing goes live until
you promote the draft. There is one conversation per page visit; **New chat** closes it.

**Settings** in the chat opens what the agent may use: the model, the resources (code projects,
environments, services and servers), the experts, and under **Advanced** the prompt. A server whose SSH
is not set up or not allowed cannot be chosen. The prompts offered are those tagged `documentation`; the default is **Write a
document** (Administration → Prompts). It tells the agent to read what it was given, write with `update_document`, change a passage
with `edit_document`, and never say the document is published. If you write your own prompt, tell the agent to write with
`update_document`; a final answer is not turned into a draft. **Preview** shows exactly what the next message will start with, as
JSON. Nothing is chosen until you tick it; experts (`ask_<name>`) are offered only when you tick them. The run gets
every tool, including `run_subagent`; what each may reach follows the resources you chose (for example `ssh_run` only
on the chosen servers), as in an agent's Test tab.

**The run page.** Every run, including this one, has a page at `/sessions/<id>`. It uses the same workspace as the Test tab: the
main pane shows each turn (your message, a collapsed **Worked** block with thinking, tool calls and results, and the answer)
and scrolls on its own; the message box is always visible. After you scroll up it stops following new events and shows
**Jump to latest**. Opening a session loads the latest 100 events; earlier pages load as you scroll up. The header shows tokens
in/out/cached, model calls and the status: Starting, Working, Waiting for you, Stopped, Failed or Finished; the sidebar shows
**Agents in this run**, the draft and, for a run started with an output schema, **Output** (on a narrow screen open
**Run details**). Each answer links to its **Full record**. A run
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
agent to write with `update_document`; copy the rules from **Write a document**. Migration 0073 removed the service,
whole-environment and code-project generation; documents written by service phases keep their phase layout.

### 4.12 Documents

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
- A live document shows "Version n" with **Save**, **Download document folder** (ZIP),
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

Anything a person or an agent saves is a **draft** (a document a discovery run drafts too); a version goes live
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
- A new document's draft opens at `/documents/{id}`; **Publish** creates the document there. If a document with that name was created in the folder meanwhile, the draft becomes a draft of it and opens
  on its draft page (`/documents/drafts/{id}`, where an agent's draft of a live document opens too) asking
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
Resources; the agent may change it as a draft) and the experts (none unless you tick them), and under **Advanced** the
prompt (**Revise a document** by default, any other saved prompt, or **Own prompt**) and the **Tools** (every tool is on; untick the ones this conversation must not get. A saved agent's own off tools show as *Off for this agent* and cannot be switched on). **Preview** shows exactly what the
next message will start with, as JSON. Your choices are remembered for the document in this browser. While a
conversation is open its settings are fixed: Settings shows them, and **Start new chat with these settings** closes it
and lets you change them for the next one. **New chat** closes the conversation; **Earlier runs** reopens another. The
**Agent runs** tab lists the document's runs, open ones first. Deleting the document deletes its runs; their drafts are
deleted with it.

Agents find published documents with `search_documents` (full-text search over the documents their run may read)
and read one with `get_document`.

**Folders** (`/documentation/folders`) — the documentation folders with their document
count, code projects and last generated time. Opening a folder lists its documents
(title, code project, last generated), each opening in Documents. Editing a folder
document keeps it in its folder.

### 4.13 Storage

`/storage` — read-only overview of the data folder (`DATA_ROOT`) and its three folders: source code
(`CODE_FOLDER`), code graphs (`CODE_INDEX_FOLDER`) and bootstrap docs (`DOCS_FOLDER`). Each shows host and
container paths, availability, and a file browser with text preview.

### 4.14 Agent runs

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
can also filter by agent, by status (Working, Completed, Failed, Stopped), by **Output** (**Has output**: runs that
finished with an output schema's JSON; **No output**) and by user (part of the email, press Enter).
Click **Started** or **Tokens** to sort (again to reverse). The list comes in pages of 25, 50 or 100 runs (**Previous**
/ **Next**); filters, sorting and the page are in the link, so it can be shared or reloaded. **Full record** links lead here from each reply in an agent's Test tab and from each
document run turn.
Records are kept until deleted; only administrators can see them.

### 4.15 Schedules

Administration → Schedules lists everything that runs on a timetable and is the only place to set it up. **New
schedule** creates one: choose what to run and when:

- **Jira tickets** (one per project), with its **Jira query (JQL)** and **Number of tickets**, 1–200; empty fields use
  the query and number of the last manual fetch on Jira → Tickets, or 25 tickets.
- **Emit event**, with a **Name** (e.g. `Morning summary`; names are unique in the project, a name in use is refused
  with `A schedule with this name already exists.`, and `Jira auto pull` — in any case — is kept for the Jira tickets
  schedule); make as many as you like. Each run records one
  `schedule.fired` event (source **Schedule**, subject and title the schedule's name; 4.16) and the run's result says
  `Recorded event <id>.` The schedule itself runs no agent: an event handler (4.17) sends the event to an agent with
  its task, and the answer goes to Notifications (4.18). The row says which handler runs it — **Handled by:
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

### 4.16 Events

Administration → Events lists what happened at your sources and what handlers made of it: Time, Source, Type,
Subject, Title and Status. Click a column header to sort by it, and again to flip the order. Sources are Jira, Memory (4.19), Schedule (an Emit event schedule, 4.15) and Manual. For Jira:
every scheduled pull (the Jira tickets schedule on Administration → Schedules, or Run now) records **Ticket created** for a ticket it sees for the first time
and **Ticket updated** when a ticket changed in Jira, with what changed (status, labels, assignee, priority, type,
summary, description). The very first pull records every ticket it fetches. Events a handler emitted have the source
**Handler** (see 4.17).

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

### 4.17 Event handlers

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

**Running a schedule.** An Emit event schedule (4.15) records `schedule.fired` with the schedule's name as subject
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
  notification is written with the answer, or why it failed (4.18). With **Also post the answer as a comment on the Jira
  ticket**, the answer to a Jira event is also posted on its ticket as the Jira connection's user (see the Jira docs for
  the permission it needs); a failed run is not posted. A failed run is not retried: continue it, or send
  the event again. Any enabled agent can be chosen; its run gets all of the agent's resources. A handler whose agent was
  deleted or disabled can still be turned off or edited; choosing a new
  agent or turning it back on checks the agent again. Processing the same event again never starts a second run for
  the same handler. **Output schema** (none, or a schema with **Current** or a fixed version) makes the run finish with
  JSON in that schema (4.22). Its `run.output` event joins the chain of the event that started the run, one level
  deeper, so a loop through runs stops at 5 levels like any chain: on an event 5 levels deep a handler with a schema is
  **Skipped** ("Stopped: the event chain is 5 levels deep; its run.output would be deeper.").

  Instead of one agent, **Agent** can be **Agent mapped to the assignee** (Jira event types only): each matching
  event runs the agent mapped to the ticket's assignee under Jira → Settings → **Users and agents** (4.7). An
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

### 4.18 Notifications

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

### 4.19 Memory

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

**Embedding.** Entries are embedded with the embedding model chosen at the top of the Memory page (connections are managed in LLM Settings) so search
finds related wording. When no embedding model is configured, or embedding fails, the entry is still saved and shows **Not embedded**: it is found
by words only. After fixing the model, **Re-embed** on the page embeds those entries.

**Events.** Every change records an event with the source `memory`: `memory.saved`, `memory.updated`,
`memory.restored` and `memory.deleted`. The payload holds `memory_id`, `name`, `metadata`, `revision`, `agent_id`,
`session_id` and `actor`, but not the content. Event handlers (4.17) can react, for example a Send to agent handler
with the condition `source equals memory` and `type equals memory.saved`.

### 4.20 System map

Environments → **System map** draws your inventory — environments, servers, services and code projects — and the
connections between them. Nothing is drawn by hand, and nothing is stored for the map: every view is drawn from the
environment models (the same data as each environment's inventory), so it always matches the inventory. Add entries on
their own pages, and connect them here.

- **Infrastructure.** Each environment holds the servers with a deployment in it, and each server holds the services
  deployed on it in that environment. A server in two environments appears in both; a server without deployments sits
  at the top level. Lines are network **connections** between deployments (with their port and protocol); a connection
  to an external host ends on a box for that host.
- **Services.** Every service and code project, with relation lines (*depends on*, *part of*, *built from*) and the
  connections rolled up to the services (*connects to*). Archived code projects are left out.
- **Browsing.** **Inside** lists what the current box holds (→ opens it; "+N inside" says it holds more), **Outside**
  lists what its connections reach elsewhere. The breadcrumb goes back up. **Depth** shows up to 3 levels; deeper
  entries fold into their box and lines add up (`connects to ×3`). A connection line carries its label, `protocol:port`
  (`connects to postgres:5432`; just the protocol or just `:port` when the other is missing); a line that adds up
  several connections lists their distinct labels, sorted, at most 3 and then `+N` (`connects to tcp:25, tcp:587`). When both ends of a connection are in one environment, the line stays inside it: it never joins one environment's copy of a server to another's. Everything you choose is in the page address.
- **Drafts.** Entries an agent drafted and nobody confirmed yet are drawn dashed (a line that adds up several
  connections is dashed when all of them are drafts); the Inside list says "draft". **Show drafts** (on by default)
  hides them: the map is then drawn from the confirmed inventory only (`drafts=0` in the page address and in the view's
  API address). See [Drafts](#drafts).
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

### 4.21 Prompts

**Administration → Prompts** (`/prompts`) is the library of reusable instructions for agents. Each prompt has tags —
lowercase letters, digits and `-`, at most 10 — that features use to find it: the agent chat on Documents → New filters by tag,
and a chat on a server, service or environment offers the enabled prompts tagged `assistant` and with its type
(`server`, `service` or `environment`), the seeded **Server assistant**, **Service assistant** or **Environment
assistant** first. Type a tag and press Enter to add it;
existing tags are suggested. Saving changed text creates a draft version; **Activate vN** makes it the active one;
inactive versions can be deleted. Changing only the name, description, tags or Enabled does not create a version.
Prompts that came with the application are tagged `documentation` plus what they document (`code`, `server`,
`service`, `mixed`).

### 4.22 Output schemas

**Agents → Output schemas** (`/output-schemas`) is the library of JSON Schemas a run can be asked to finish with. The
list shows each schema's name, description, current version and the handlers that use it; **New schema** opens the
editor.

- **Editor.** Name, description and **Schema (JSON)**. The schema must stay in the subset strict structured output
  accepts: the root is an object; every object lists all its properties in `required` and sets
  `additionalProperties: false`; types `object`, `array`, `string`, `number`, `integer`, `boolean`, `null` (nullable
  via a type list), `enum` and `description`. **Check** shows each problem with its path (for example
  `$.properties.tags: 'minItems' is not supported`), or "The schema is accepted."; **Save** refuses a schema with
  problems. Up to 64 KB (of UTF-8 JSON).
- **Validate sample.** Paste an answer a run could give into **Sample (JSON)**: it is checked against the schema in the
  editor and either matches or lists why not.
- **Versions.** Every save of a changed schema adds a version; old versions are kept. **Versions (n)** opens a side
  sheet with each version (when, by whom, **Current**); **View** shows its JSON and **Copy to editor** puts it back in
  the editor (save to make it the next version).
- **Delete** asks first. A schema a handler uses is refused ("Used by: <handler names>."). Runs that used it keep their
  output.

**Choosing a schema for a run.** The schema belongs to the run, not to the agent: the same agent answers normally or
with JSON depending on how it is started. Choose **Output schema** in the Test tab's **Settings**, in **Run agent**
(4.10), in a **Send to agent** handler (4.17), or send `output_schema_id` (and optionally `output_schema_version`)
when starting a run through the API. **Current** uses the schema's current version when the run starts; the run keeps
that exact version, so later edits do not change it. Chats on a document, a resource or Documents → New never take a
schema ("Chats do not take an output schema.").

**What happens.** The schema goes to the model as the answer format (a model without **Supports structured output**
gets it in its instructions). The final answer is checked; if it does not match, the model is asked once more with the
errors. Still invalid, the run fails with "Output did not match the schema", keeping the errors and the raw answer. A
valid answer is stored on the run (up to 256 KB) and recorded as a `run.output` event that handlers can react to (a
run a handler started records it in that event's chain, so handler loops stop at 5 levels; 4.17).

**Reading the output.** The run page (and the Test tab) shows **Output** beside the transcript: the formatted JSON with
**Copy**, or the errors and raw answer of a failed run. Over the API, `GET /api/sessions/<id>/output` returns
`{schema, output, valid, error?, errors?, raw?}`, `GET /api/sessions/<id>/status` a light status for polling, and
`POST /api/sessions?wait=<1–120>` starts a run and waits for its result.

---

## 5. End-to-end workflows

### A. Document a service from its source code

1. **LLM Settings**: add and test an Anthropic (or Bedrock Claude) model.
2. **Code projects**: add the Git repository; wait for the clone.
3. **Services**: create the service, link the code project.
4. **Administration → Prompts**: save a documentation prompt (or write it in the form).
5. **Docs → Documents → New → Write with an agent**: give a title and folder, open **Settings**, pick the service under Resources and the prompt, then tell the agent what to write. The agent reads the code projects the service links.
6. Follow the chat, send messages such as "shorten the API section", then **Promote** the draft.

### B. Document deployments and infrastructure

1. **Servers**: register hosts. **Environments**: create `production`.
2. **Services**: add deployments (server + environment + container/unit + port + log path); the services and servers
   are then in `production`. On the **System map**, add the connections between the deployments.
3. **Documents → New → Write with an agent**: in **Settings** pick the environment (or the servers) and ask for the
   deployments to be documented.
4. The agent connects over SSH to the chosen servers (and to those the environment brings).

### C. Build and test an operations agent

1. **Agents → New agent → Generic agent** (generate a proposal or **Start blank**).
2. **General**: assign a model; **SSH**: allow servers; **Code projects** / **Documents**: select resources.
3. **Test**: run a task such as "Check disk usage on the app servers"; inspect the **Worked** block.
4. Use **Add feedback → Suggest improvements**, then **Review and apply → Apply to this agent** (or **Apply to a new clone** to try it first).

### D. Write documentation interactively with an agent

1. **Docs → Documents → New → Write with an agent** → give a title and folder, choose the resources and the prompt in **Settings**, and tell the agent what to write.
2. Follow the stream and send a message such as "shorten the API section".
3. **Promote** the draft.

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
| `AGENT_SESSION_MAX_SESSIONS` | Sessions running at once per agent-session-worker (default 4) |
| `AGENT_MAX_SUBAGENTS` | Subagents running at once in one native session turn (default 4; at most 16 helpers per turn, with expert calls). Expert calls take a slot too, and an expert's own subagents run in slots of its own, so a turn can run up to `AGENT_MAX_SUBAGENTS` x (1 + running experts) helpers at once |
| `CREDENTIAL_ENCRYPTION_KEY` | Encrypts all stored secrets — **back it up separately** |
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

- **Document search is full text only** (title, description and content; no embeddings).
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
| `list_files`, `read_file`, `search_files` | The run has code projects | Only selected roots plus a scratch workspace; symlinks resolved; binaries refused; reads ≤ 64 KiB; secrets redacted |
| `shell_run` | The run has code projects | Runs in the separate `shell-worker` in a bubblewrap sandbox: no network, no capabilities, empty environment, sources mounted read-only, only the scratch workspace writable; 1–300 s; 32 KiB output |
| `code_graph` | The run has the code project and it has a generated graph | Indexed symbol, route and call queries on a disposable snapshot copy |
| `code_search` | The run has code projects | Tree-sitter function lookup (Go, Python, JS, TS, TSX) |
| `ast_grep` | The run has code projects | Structural code queries; 30 s timeout |
| `ssh_run` | The run has the server and it is allowed SSH with a key | Runs **any** command (not read-only!) under `timeout` + `setpriv --no-new-privs`; root only via `sudo -n` when allowed; host key checked only if trusted keys are saved; 32 KiB output |
| `get_resource`, `get_document` | Inventory tools: the run was given the resource or document | Read-only: `get_resource` answers with what is recorded about one server, service, environment or code project the run was given (its connection, instructions, services, documents and links to related resources), and `get_document` reads documents the run was given or linked to those resources. Anything else gets "The server … is not available in this run." (or service, environment, code project; "This document …" for documents), the same answer as for something that does not exist |
| `list_documents`, `new_document`, `update_document`, `edit_document` | Document tools: the run has the resource (or the document) | Draft documents of a server, service or environment in the run's access; every write is a draft a person publishes (see Drafts and connections) |
| MCP tools | The run has the MCP server | Every tool of the tested MCP servers in the run's access |
| `search_documents` | Every run | Full-text search over the published documents the run may read (given to it, or linked to a server, service, environment or code project in it); drafts are never searched. Returns each match's id, title, short description, tags, linked resources and a short snippet; read one with `get_document` |
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
