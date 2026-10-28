import { connectionTable, visibleConnections, type ConnectionView } from "./connectionTable";
import type { Connection } from "./inventory";
import { serviceAsset as asset, serviceDeployment } from "~/lib/__tests__/serviceFixture";

const services = [
  asset("v1", "payments-api", [serviceDeployment("d1", "prod", "app-1"), serviceDeployment("d3", "staging", "app-2")]),
  asset("v2", "postgres", [serviceDeployment("d2", "prod", "db-1")]),
  asset("v3", "nginx", [serviceDeployment("d4", "prod", "edge-1")]),
  asset("v4", "stripe", [], { service_type: "external" }),
];
const connection = (id: string, from: string, to: Partial<Connection>, port: number | null, protocol: string) =>
  ({
    id,
    from_deployment_id: from,
    to_deployment_id: null,
    to_service_id: null,
    to_host: null,
    port,
    protocol,
    description: "",
    status: "confirmed",
    revision: 1,
    ...to,
  }) as Connection;

const outDeployment = connection("c1", "d1", { to_deployment_id: "d2" }, 5432, "tcp");
const outHost = connection("c2", "d3", { to_host: "smtp.example.com" }, null, "smtp");
const outExternal = connection("c3", "d1", { to_service_id: "v4" }, 443, "https");
const inDeployment = connection("c4", "d4", { to_deployment_id: "d1" }, 8000, "http");
const inExternal = connection("c5", "d2", { to_service_id: "v1" }, 80, "http");
const unrelated = connection("c6", "d4", { to_deployment_id: "d2" }, 5432, "tcp");
const rows = connectionTable(
  services,
  [outDeployment, outHost, outExternal, inDeployment, inExternal, unrelated],
  ["d1", "d3"],
  "v1",
);
const row = (id: string) => rows.find((item) => item.item.id === id)!;

test("an outgoing connection to a deployment: its server and service, and this side's server", () => {
  expect(row("c1")).toEqual({
    key: "c1-out",
    direction: "out",
    server: "db-1",
    serviceId: "v2",
    service: "postgres",
    host: null,
    port: 5432,
    protocol: "tcp",
    deployment: "app-1",
    name: "to postgres on db-1 :5432 tcp",
    item: outDeployment,
  });
});

test("an outgoing connection to a host: the host as its server, no service; a null port", () => {
  expect(row("c2")).toMatchObject({
    direction: "out",
    server: "smtp.example.com",
    serviceId: null,
    service: null,
    host: "smtp.example.com",
    port: null,
    deployment: "app-2",
    name: "to smtp.example.com smtp",
  });
});

test("an outgoing connection to an external service: no server, the service", () => {
  expect(row("c3")).toMatchObject({
    direction: "out",
    server: null,
    serviceId: "v4",
    service: "stripe",
    host: null,
    deployment: "app-1",
    name: "to stripe :443 https",
  });
});

test("an incoming connection from a deployment: its server and service, and the deployment it goes to", () => {
  expect(row("c4")).toMatchObject({
    key: "c4-in",
    direction: "in",
    server: "edge-1",
    serviceId: "v3",
    service: "nginx",
    deployment: "app-1",
    name: "from nginx on edge-1 :8000 http",
  });
});

test("an incoming connection to the service itself (external) has no deployment of its own; others are left out", () => {
  expect(row("c5")).toMatchObject({ direction: "in", server: "db-1", service: "postgres", deployment: null });
  expect(rows.map((item) => item.key)).toEqual(["c1-out", "c2-out", "c3-out", "c4-in", "c5-in"]);
});

const view = (extra: Partial<ConnectionView> = {}): ConnectionView => ({
  search: "",
  direction: "all",
  protocol: "all",
  sort: "direction",
  descending: false,
  ...extra,
});
const keys = (extra: Partial<ConnectionView>) => visibleConnections(rows, view(extra)).map((item) => item.item.id);

test("search covers server, service and host", () => {
  expect(keys({ search: "SMTP.example" })).toEqual(["c2"]);
  expect(keys({ search: "nginx" })).toEqual(["c4"]);
  expect(keys({ search: "db-1" })).toEqual(["c1", "c5"]);
  expect(keys({ search: "https" })).toEqual([]);
});

test("the direction and protocol filters", () => {
  expect(keys({ direction: "in" })).toEqual(["c4", "c5"]);
  expect(keys({ direction: "out", protocol: "https" })).toEqual(["c3"]);
  expect(keys({ protocol: "http" })).toEqual(["c4", "c5"]);
});

test("sorting: out before in, port numerically with no port last, and each column both ways", () => {
  expect(keys({})).toEqual(["c1", "c2", "c3", "c4", "c5"]);
  expect(keys({ sort: "port" })).toEqual(["c5", "c3", "c1", "c4", "c2"]);
  expect(keys({ sort: "port", descending: true })).toEqual(["c2", "c4", "c1", "c3", "c5"]);
  expect(keys({ sort: "server" })).toEqual(["c1", "c5", "c4", "c2", "c3"]);
  expect(keys({ sort: "service" })).toEqual(["c4", "c1", "c5", "c3", "c2"]);
  expect(keys({ sort: "protocol" })).toEqual(["c4", "c5", "c3", "c2", "c1"]);
  expect(keys({ sort: "deployment" })).toEqual(["c1", "c3", "c4", "c2", "c5"]);
});
