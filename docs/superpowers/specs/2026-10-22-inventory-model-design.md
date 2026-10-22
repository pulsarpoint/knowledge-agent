# Inventory model: environments, servers, services, deployments, connections

Date: 2026-10-22. Status: agreed in conversation (2026-10-22), for review.

## Why

What an environment looks like is stored and read in too many ways:

- A service's or server's environment is stored twice (`environment_services` / `environment_servers` membership and
  `service_deployments.environment_id`), plus a legacy free-text `service_deployments.environment`. The map unions them;
  the agent's `get_resource` reads membership only — they can disagree.
- Service dependencies have two write paths (the service form's aggregate `save_service` and `/inventory/links`) and
  three readers that compute different pictures (the server-built map, a client-side dependency graph, `get_resource`).
- A network connection is a generic `inventory_links` row of kind `connects_to` between two services with free-form
  `attributes` nobody checks — it cannot say "the app on host A connects to the database on host B:5432". Agents never
  see those attributes.
- Agents can only *propose* services for the server their run started from; the UI and agents write through different
  code.

## Decisions (user, 2026-10-22)

- **Network connections link deployments** (a service running on a server), or a deployment and an external host:port.
- **Service relations stay**, separate from connections: logical, service-level relations that are not network traffic —
  `depends_on`, `part_of` (a component or library of another service), `built_from` (service → code project).
- **Environment membership comes from deployments only** — no separate membership tables.
- **Agents change the inventory directly**, through the same API as the UI, gated by a role; every change is audited.
- **One API** used by the UI and the agent tools; **one environment model (JSON)** that the system map draws from.

## Model

- **Environment** — `ka.environments` (unchanged columns).
- **Server** — `ka.servers` (unchanged; SSH settings stay).
- **Service** — `ka.services`: the logical service (name, type, role, owner, description, tags).
- **Deployment** — `ka.service_deployments`: a service running on a server *in an environment*: `environment_id NOT NULL`,
  runtime, runtime_name, `port` (the main listening port), log location, version. The free-text `environment` column is
  removed. A server or service is "in" an environment when one of its deployments is.
- **Connection** — new `ka.connections`:
  `from_deployment_id` (NOT NULL) → either `to_deployment_id` or an external endpoint (`to_host` text); `port` (defaults
  to the target deployment's port), `protocol` (short text, e.g. `tcp`, `http`, `postgres`), `description`, audit
  columns (`created_by` person / `created_by_agent` + `session_id`, timestamps), `revision`. No self-connection; unique
  on (from, to deployment or host, port, protocol). Connections may cross environments.
- **Service relation** — new `ka.service_relations`: `from_service_id` → `to_service_id` (kinds `depends_on`, `part_of`)
  or `to_code_project_id` (kind `built_from`); `description`; audit columns; unique on (from, to, kind).
- **Documents** keep attaching to environments, servers, services, deployments and code projects as today.
- Removed: `environment_services`, `environment_servers`, `inventory_links` (and its `inventory_entries` view where
  nothing else needs it), the aggregate `save_service` write path, and the readers listed under "One reader".

### Migration (test data — converted where simple)

- Deployments without `environment_id` get the environment named by their free-text `environment` (created if missing).
- `inventory_links`: `depends_on` → relation `depends_on`; `built_from` → relation `built_from`; `connects_to` between
  two services → one connection per pair of their deployments in the same environment, or a `depends_on` relation when
  either is not deployed; `connects_to` ends on servers or environments are dropped (noted in the migration notes).
- Membership-only servers/services (in an environment without a deployment there) lose that membership; they show as
  "not deployed" in their lists.

## One API (admin-api, one `inventory` module)

Reads:
- `GET /environments/{id}/model` — the environment model (below).
- `GET /environments/{id}/servers`, `GET /servers/{id}/deployments`, `GET /services/{id}/deployments`,
  `GET /services/{id}/connections` (outgoing and incoming, by deployment), `GET /deployments/{id}/connections`,
  `GET /services/{id}/relations`.

Writes (each with revision checks, validation and audit):
- servers, services, environments: create / update / delete (delete refused while deployments or connections use it).
- deployments: create / update / delete (delete removes its connections — confirmed in the UI).
- connections: create / update / delete.
- relations: create / update / delete.

The UI's forms (servers, services, environments, deployments, links on the system map) all call these; nothing writes
the inventory any other way. Discovery's "accept proposal" calls the same functions.

## Environment model (JSON)

`GET /environments/{id}/model` → `{environment, servers: [{…server, deployments: [{id, service_id, runtime, port,
version, …}]}], services: [{…service}], connections: [{id, from_deployment_id, to_deployment_id | to_host, port,
protocol, description}], relations: [{…}], external: [{host, ports}]}` — every field a reader needs, nothing secret (no
SSH credentials). One pure function builds it from rows; it is the only reader the map and the agents use.

## Agent tools

Thin wrappers over the same functions, each with a pure `check_<tool>` against the run's access object (like every tool):
- Reads: `get_environment_model(environment_id)`, `list_servers(environment_id)`, `list_deployments(server_id |
  service_id)`, `list_connections(service_id | deployment_id)`.
- Writes: `save_server`, `save_service`, `save_deployment`, `save_connection`, `save_relation` (create without `id`,
  update with `id` + `revision`) and `remove_inventory_item(kind, id)`.
- **Role:** writes need the run's role `inventory_editor` (new, default off; an agent setting "Inventory editor" next to
  Content editor; narrowing rules as for content_editor). Reads and writes stay inside the run's access (the
  environment, server or service must be in the access object; a new server or service is allowed when the environment
  it is deployed in is).
- Audit: every change records the agent and session (and the person who started the session).

## System map

- The infrastructure view is drawn from the environment model: environment › server › deployment (service), with
  **connections** as edges between deployments (exact host and port), external endpoints as boundary nodes.
- The services view draws **relations** and connections rolled up to service level (drawn differently).
- The client-side dependency graph (`serviceGraph.ts`, `ServiceDependencyGraph.tsx`, `ServiceTalaGraph.tsx`) and the
  link sheet's free-form attributes are replaced by the model.

## Phases (each a plan and branch)

1. **Model and migration** — the tables, the conversion, the internal functions (create/update/delete/list) with tests.
2. **API and UI** — the endpoints; the forms move onto them; the aggregate `save_service` and `/inventory/links` go.
3. **Agent tools and role** — the tools, `inventory_editor`, audit; `get_resource` reads the model.
4. **Model JSON and map** — `GET /environments/{id}/model`; the map and services view draw from it; the old readers go.

## Out of scope

- Discovering connections automatically (an agent can add them with the tools, e.g. from `ss`/`netstat` output).
- Changing the proposals flow beyond calling the new functions.

## Testing

- Pure: the model builder (servers, deployments, connections, external endpoints, relations); each tool's check (access,
  role).
- API: every endpoint's validation, revision checks, delete rules, audit; the UI and agent tools produce the same rows.
- Migration: each conversion rule, up and down.
- Map: built from the model — exact connection edges by deployment, rolled-up service edges, relations in the services
  view.
