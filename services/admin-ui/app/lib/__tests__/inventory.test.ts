import {
  connectionText,
  deploymentLabels,
  relationText,
  type Connection,
  type Relation,
} from "~/features/servers/inventory";
import type { ServiceAsset } from "~/features/servers/services";

const relation: Relation = {
  id: "r1",
  from_service_id: "api",
  kind: "depends_on",
  to_service_id: "pg",
  to_code_project_id: null,
  description: "orders",
  status: "confirmed",
  revision: 3,
};

const service = (id: string, name: string, deployments: { id: string; server: string; environment: string }[]) =>
  ({
    id,
    name,
    deployments: deployments.map((item) => ({
      id: item.id,
      environment: item.environment,
      port: 5432,
      server: { name: item.server },
    })),
  }) as unknown as ServiceAsset;

test("connections and relations read in words", () => {
  const labels = deploymentLabels([
    service("api", "api", [{ id: "d1", server: "web-1", environment: "prod" }]),
    service("pg", "pg", [{ id: "d2", server: "db-1", environment: "prod" }]),
  ]);
  expect(labels.get("d2")).toEqual({ id: "d2", service_id: "pg", port: 5432, label: "pg on db-1 (prod)" });
  const connection: Connection = {
    id: "c1",
    from_deployment_id: "d1",
    to_deployment_id: "d2",
    to_service_id: null,
    to_host: null,
    port: 5432,
    protocol: "postgres",
    description: "",
    status: "confirmed",
    revision: 1,
  };
  expect(connectionText(connection, labels)).toBe("api on web-1 (prod) → pg on db-1 (prod) :5432 postgres");
  expect(
    connectionText(
      { ...connection, to_deployment_id: null, to_host: "smtp.example.com", port: 587, protocol: "smtp" },
      labels,
    ),
  ).toBe("api on web-1 (prod) → smtp.example.com :587 smtp");
  expect(
    connectionText(
      { ...connection, to_deployment_id: null, to_service_id: "stripe", port: 443, protocol: "https" },
      labels,
      new Map([["stripe", "stripe"]]),
    ),
  ).toBe("api on web-1 (prod) → stripe :443 https");
  const names = new Map([
    ["api", "api"],
    ["pg", "pg"],
    ["shop", "shop"],
  ]);
  expect(relationText(relation, names)).toBe("api depends on pg");
  expect(
    relationText({ ...relation, kind: "built_from", to_service_id: null, to_code_project_id: "shop" }, names),
  ).toBe("api is built from shop");
});
