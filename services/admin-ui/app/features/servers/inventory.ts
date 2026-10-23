import { ApiError, request } from "~/lib/api";
import type { ServiceAsset } from "./services";

/** The inventory's one REST API (admin-api inventory/api.py): create, update and delete each item; the lists the pages
 * read. Updates and deletes send the revision that was read; a stale one is a 409 with the code STALE_REVISION (other
 * 409s, such as a duplicate, have no code and their own message). */
export type ItemKind = "servers" | "services" | "environments" | "deployments" | "connections" | "relations";
export type RelationKind = "depends_on" | "part_of" | "built_from";
/** Every inventory entry is confirmed or a draft (written by an agent; a person confirms or discards it). */
export type Status = "draft" | "confirmed";
/** Whether a write was refused because the item changed since it was read. */
export const isStale = (e: unknown) => e instanceof ApiError && e.status === 409 && e.code === "STALE_REVISION";

export interface Deployment {
  id: string;
  service_id: string;
  server_id: string;
  environment_id: string;
  runtime: string;
  runtime_name: string;
  port: number | null;
  log_location: string;
  deployed_version: string;
  status: Status;
  revision: number;
}
/** A network connection from a deployment to another deployment, an external service or an external host. */
export interface Connection {
  id: string;
  from_deployment_id: string;
  to_deployment_id: string | null;
  to_service_id: string | null;
  to_host: string | null;
  port: number | null;
  protocol: string;
  description: string;
  status: Status;
  revision: number;
}
/** A service relation: depends_on and part_of point at a service, built_from at a code project. */
export interface Relation {
  id: string;
  from_service_id: string;
  kind: RelationKind;
  to_service_id: string | null;
  to_code_project_id: string | null;
  description: string;
  status: Status;
  revision: number;
}
export type DeploymentValue = Omit<Deployment, "id" | "revision" | "status">;
export type ConnectionValue = Omit<Connection, "id" | "revision" | "status">;
export type RelationValue = Omit<Relation, "id" | "revision" | "status">;

const send = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
const at = (kind: ItemKind, id: string) => `/api/${kind}/${encodeURIComponent(id)}`;

export const createItem = <T>(kind: ItemKind, value: object) => request<T>(`/api/${kind}`, send("POST", value));
/** Changes the given fields; the others keep their saved values. */
export const updateItem = <T>(kind: ItemKind, id: string, revision: number, changes: object) =>
  request<T>(at(kind, id), send("PUT", { ...changes, revision }));
export const deleteItem = (kind: ItemKind, id: string, revision: number) =>
  request<{ id: string }>(`${at(kind, id)}?revision=${revision}`, { method: "DELETE" });

export const getServiceDeployments = (id: string, signal?: AbortSignal) =>
  request<{ deployments: Deployment[] }>(`${at("services", id)}/deployments`, { signal });
export const getServiceRelations = (id: string, signal?: AbortSignal) =>
  request<{ relations: Relation[] }>(`${at("services", id)}/relations`, { signal });
/** Outgoing and incoming connections of the service's deployments. */
export const getServiceConnections = (id: string, signal?: AbortSignal) =>
  request<{ connections: Connection[] }>(`${at("services", id)}/connections`, { signal });

/** What Confirm and Discard act on; a membership's id is `<environment id>:<server id>`. */
export type DraftKind = "environment" | "server" | "service" | "deployment" | "connection" | "relation" | "membership";
/** An entry Confirm or Discard reached, named as people read it ("cache on app-1 in prod", "app-1 in prod"). */
export interface Reached {
  kind: DraftKind;
  id: string;
  name: string;
}
export const membershipId = (environmentId: string, serverId: string) => `${environmentId}:${serverId}`;
const draftUrl = (action: "confirm" | "discard", kind: DraftKind, id: string, dryRun: boolean) =>
  `/api/inventory/${kind}/${encodeURIComponent(id)}/${action}${dryRun ? "?dry_run=1" : ""}`;
/** Confirms the draft with every draft it needs; a dry run lists them and changes nothing. A membership has no
 * revision (null). */
export const confirmDraft = (kind: DraftKind, id: string, revision: number | null, dryRun = false) =>
  request<{ confirmed: Reached[] }>(draftUrl("confirm", kind, id, dryRun), send("POST", { revision }));
/** Deletes the draft with every draft that needs it; a dry run lists them and changes nothing. */
export const discardDraft = (kind: DraftKind, id: string, revision: number | null, dryRun = false) =>
  request<{ discarded: Reached[] }>(draftUrl("discard", kind, id, dryRun), send("POST", { revision }));

/** One write to apply: a create (no id), an update (id, revision, changes) or a delete (id, revision). `key` names the
 * form row a created item belongs to. */
export interface Change {
  kind: ItemKind;
  method: "POST" | "PUT" | "DELETE";
  id?: string;
  revision?: number;
  value?: object;
  key?: string;
}

export function applyChange<T>(change: Change): Promise<T | { id: string }> {
  if (change.method === "POST") return createItem<T>(change.kind, change.value!);
  if (change.method === "PUT") return updateItem<T>(change.kind, change.id!, change.revision!, change.value!);
  return deleteItem(change.kind, change.id!, change.revision!);
}

/** The saved items after a change was applied: a delete removes the item, a create or update puts the returned item
 * in place. */
export function afterChange<T extends { id: string }>(saved: T[], change: Change, result: T | { id: string }): T[] {
  const kept = saved.filter((item) => item.id !== (change.id ?? result.id));
  return change.method === "DELETE" ? kept : [...kept, result as T];
}

const DEPLOYMENT_FIELDS = [
  "server_id",
  "environment_id",
  "runtime",
  "runtime_name",
  "port",
  "log_location",
  "deployed_version",
] as const;
export type DeploymentRow = Pick<Deployment, (typeof DEPLOYMENT_FIELDS)[number]> & { key: string; id?: string };

/** The writes that turn the service's saved deployments into the form's rows: removed rows are deleted first (a
 * deleted deployment takes its connections with it), changed rows updated with only their changed fields, new rows
 * created. */
export function deploymentChanges(serviceId: string, saved: Deployment[], rows: DeploymentRow[]): Change[] {
  const byId = new Map(saved.map((item) => [item.id, item]));
  const kept = new Set(rows.flatMap((row) => (row.id ? [row.id] : [])));
  const changes: Change[] = saved
    .filter((item) => !kept.has(item.id))
    .map((item) => ({ kind: "deployments", method: "DELETE", id: item.id, revision: item.revision }));
  for (const row of rows) {
    const item = row.id ? byId.get(row.id) : undefined;
    if (!item) {
      const value = Object.fromEntries(DEPLOYMENT_FIELDS.map((field) => [field, row[field]]));
      changes.push({ kind: "deployments", method: "POST", value: { service_id: serviceId, ...value }, key: row.key });
      continue;
    }
    const changed = Object.fromEntries(
      DEPLOYMENT_FIELDS.filter((field) => row[field] !== item[field]).map((field) => [field, row[field]]),
    );
    if (Object.keys(changed).length)
      changes.push({ kind: "deployments", method: "PUT", id: item.id, revision: item.revision, value: changed });
  }
  return changes;
}

const relationKey = (relation: RelationValue) =>
  [relation.from_service_id, relation.kind, relation.to_service_id ?? "", relation.to_code_project_id ?? ""].join(" ");

/** The writes that turn the saved relations into the wanted ones (matched by ends and kind): deletes first, then
 * description updates, then creates. */
export function relationChanges(saved: Relation[], wanted: RelationValue[]): Change[] {
  const byKey = new Map(saved.map((item) => [relationKey(item), item]));
  const wantedKeys = new Set(wanted.map(relationKey));
  const changes: Change[] = saved
    .filter((item) => !wantedKeys.has(relationKey(item)))
    .map((item) => ({ kind: "relations", method: "DELETE", id: item.id, revision: item.revision }));
  for (const value of wanted) {
    const item = byKey.get(relationKey(value));
    if (!item) changes.push({ kind: "relations", method: "POST", value });
    else if (item.description !== value.description)
      changes.push({
        kind: "relations",
        method: "PUT",
        id: item.id,
        revision: item.revision,
        value: { description: value.description },
      });
  }
  return changes;
}

/** The names of the drafts a person's save confirms with it (a confirmed entry never needs a draft): each named
 * deployment that is a draft, with its draft service, server, membership ("db-1 in prod") and environment, and each
 * named draft service; once each. */
export function draftsUsed(
  services: ServiceAsset[],
  named: { deployments?: (string | null)[]; services?: (string | null)[] },
): string[] {
  const names = new Set<string>();
  for (const service of services)
    for (const deployment of service.deployments) {
      if (!named.deployments?.includes(deployment.id)) continue;
      if (deployment.status === "draft")
        names.add(`${service.name} on ${deployment.server.name} (${deployment.environment})`);
      if (service.status === "draft") names.add(service.name);
      if (deployment.server.status === "draft") names.add(deployment.server.name);
      if (deployment.membership_status === "draft") names.add(`${deployment.server.name} in ${deployment.environment}`);
      if (deployment.environment_status === "draft") names.add(deployment.environment);
    }
  for (const service of services)
    if (named.services?.includes(service.id) && service.status === "draft") names.add(service.name);
  return [...names];
}

/** A deployment as people read it: "api on web-1 (prod)". */
export interface DeploymentLabel {
  id: string;
  service_id: string;
  port: number | null;
  label: string;
}
/** Every deployment of the services, labelled, by id. */
export function deploymentLabels(services: ServiceAsset[]): Map<string, DeploymentLabel> {
  return new Map(
    services.flatMap((service) =>
      service.deployments.map((deployment) => [
        deployment.id,
        {
          id: deployment.id,
          service_id: service.id,
          port: deployment.port,
          label: `${service.name} on ${deployment.server.name} (${deployment.environment})`,
        },
      ]),
    ),
  );
}
/** How a connection reads: "api on web-1 (prod) → pg on db-1 (prod) :5432 postgres"; an external service target by
 * its name in `services`. */
export function connectionText(
  connection: Connection,
  deployments: Map<string, DeploymentLabel>,
  services: Map<string, string> = new Map(),
): string {
  const name = (id: string | null) => (id && deployments.get(id)?.label) || "an unknown deployment";
  const target =
    connection.to_host ??
    (connection.to_service_id
      ? (services.get(connection.to_service_id) ?? "an unknown service")
      : name(connection.to_deployment_id));
  const port = connection.port === null ? "" : ` :${connection.port}`;
  return `${name(connection.from_deployment_id)} → ${target}${port} ${connection.protocol}`;
}

export const RELATION_LABELS: Record<RelationKind, string> = {
  depends_on: "depends on",
  part_of: "is part of",
  built_from: "is built from",
};
/** How a relation reads: "api depends on pg", "worker is part of api", "api is built from shop". */
export function relationText(relation: Relation, names: Map<string, string>): string {
  const name = (id: string | null) => (id && names.get(id)) || "an unknown item";
  return `${name(relation.from_service_id)} ${RELATION_LABELS[relation.kind]} ${name(relation.to_service_id ?? relation.to_code_project_id)}`;
}
