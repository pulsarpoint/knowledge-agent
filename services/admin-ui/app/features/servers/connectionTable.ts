import type { Connection } from "./inventory";
import type { ServiceAsset } from "./services";

/** One row of a service's Connections table: out (from one of its deployments) or in (to one of them, or to the
 * service itself when it is external). `server`, `service` and `host` are the other end's: an outgoing connection to a
 * host has the host as its server and no service, one to an external service no server. `deployment` is this
 * service's side: the server of the deployment it goes from (out) or to (in); none for a connection to the service
 * itself. `name` is how it reads in its menu and removal ("to postgres on db-1 :5432 tcp"). */
export interface ConnectionRow {
  key: string;
  direction: "out" | "in";
  server: string | null;
  serviceId: string | null;
  service: string | null;
  host: string | null;
  port: number | null;
  protocol: string;
  deployment: string | null;
  name: string;
  item: Connection;
}

/** The rows of a service's Connections table: those out from its deployments (`deploymentIds`), then those in. */
export function connectionTable(
  services: ServiceAsset[],
  connections: Connection[],
  deploymentIds: string[],
  serviceId: string,
): ConnectionRow[] {
  const ours = new Set(deploymentIds);
  const names = new Map(services.map((item) => [item.id, item.name]));
  const where = new Map(
    services.flatMap((service) =>
      service.deployments.map((item) => [item.id, { server: item.server.name, serviceId: service.id }] as const),
    ),
  );
  const row = (
    item: Connection,
    direction: ConnectionRow["direction"],
    other: { server: string | null; serviceId: string | null; host: string | null },
    deployment: string | null,
  ): ConnectionRow => {
    const service = other.serviceId ? (names.get(other.serviceId) ?? null) : null;
    const end = [service, other.server && (service ? `on ${other.server}` : other.server)].filter(Boolean).join(" ");
    const port = item.port === null ? "" : ` :${item.port}`;
    return {
      key: `${item.id}-${direction}`,
      direction,
      ...other,
      service,
      port: item.port,
      protocol: item.protocol,
      deployment,
      name: `${direction === "out" ? "to" : "from"} ${end || "an unknown end"}${port} ${item.protocol}`,
      item,
    };
  };
  const serverOf = (id: string | null) => (id && where.get(id)?.server) || null;
  return [
    ...connections
      .filter((item) => ours.has(item.from_deployment_id))
      .map((item) => {
        const target = item.to_deployment_id ? where.get(item.to_deployment_id) : undefined;
        const other = item.to_host
          ? { server: item.to_host, serviceId: null, host: item.to_host }
          : item.to_service_id
            ? { server: null, serviceId: item.to_service_id, host: null }
            : { server: target?.server ?? null, serviceId: target?.serviceId ?? null, host: null };
        return row(item, "out", other, serverOf(item.from_deployment_id));
      }),
    ...connections
      .filter((item) => ours.has(item.to_deployment_id ?? "") || item.to_service_id === serviceId)
      .map((item) => {
        const source = where.get(item.from_deployment_id);
        const other = { server: source?.server ?? null, serviceId: source?.serviceId ?? null, host: null };
        return row(item, "in", other, serverOf(item.to_deployment_id));
      }),
  ];
}

export const ALL = "all";
export type ConnectionSort = "direction" | "server" | "service" | "port" | "protocol" | "deployment";
/** What the table shows: the search, the direction (out, in or ALL) and protocol (or ALL) filters, and the order. */
export interface ConnectionView {
  search: string;
  direction: string;
  protocol: string;
  sort: ConnectionSort;
  descending: boolean;
}

/** Text ascending, with no value last. */
const byText = (a: string | null, b: string | null) =>
  a === b ? 0 : a === null ? 1 : b === null ? -1 : a.localeCompare(b);
const compare: Record<ConnectionSort, (a: ConnectionRow, b: ConnectionRow) => number> = {
  direction: (a, b) => Number(a.direction === "in") - Number(b.direction === "in"),
  server: (a, b) => byText(a.server, b.server),
  service: (a, b) => byText(a.service, b.service),
  port: (a, b) => (a.port === b.port ? 0 : a.port === null ? 1 : b.port === null ? -1 : a.port - b.port),
  protocol: (a, b) => byText(a.protocol, b.protocol),
  deployment: (a, b) => byText(a.deployment, b.deployment),
};

/** The rows whose server, service or host has the search, with the chosen direction and protocol, in the chosen
 * order (ties keep their order: out before in). */
export function visibleConnections(rows: ConnectionRow[], view: ConnectionView): ConnectionRow[] {
  const search = view.search.trim().toLowerCase();
  return rows
    .filter(
      (row) =>
        (!search || [row.server, row.service, row.host].some((value) => value?.toLowerCase().includes(search))) &&
        (view.direction === ALL || row.direction === view.direction) &&
        (view.protocol === ALL || row.protocol === view.protocol),
    )
    .sort((a, b) => (view.descending ? -1 : 1) * compare[view.sort](a, b));
}
