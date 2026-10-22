import {
  afterChange,
  connectionText,
  deploymentChanges,
  deploymentLabels,
  relationChanges,
  relationText,
  type Connection,
  type Deployment,
  type Relation,
} from "~/features/servers/inventory";
import type { ServiceAsset } from "~/features/servers/services";

const deployment: Deployment = {
  id: "d1",
  service_id: "api",
  server_id: "web",
  environment_id: "prod",
  runtime: "docker",
  runtime_name: "api",
  port: 8080,
  log_location: "",
  deployed_version: "v1",
  revision: 2,
};
const row = ({ id, revision, service_id, ...fields }: Deployment) => ({ ...fields, id, key: id });

test("deployment changes delete removed rows first, update only changed fields and create new rows", () => {
  const gone = { ...deployment, id: "d2", revision: 5 };
  const changes = deploymentChanges(
    "api",
    [deployment, gone],
    [
      { ...row(deployment), deployed_version: "v2" },
      { ...row(deployment), id: undefined, key: "new", environment_id: "test", port: null },
    ],
  );
  expect(changes).toEqual([
    { kind: "deployments", method: "DELETE", id: "d2", revision: 5 },
    { kind: "deployments", method: "PUT", id: "d1", revision: 2, value: { deployed_version: "v2" } },
    {
      kind: "deployments",
      method: "POST",
      key: "new",
      value: {
        service_id: "api",
        server_id: "web",
        environment_id: "test",
        runtime: "docker",
        runtime_name: "api",
        port: null,
        log_location: "",
        deployed_version: "v1",
      },
    },
  ]);
  expect(deploymentChanges("api", [deployment], [row(deployment)])).toEqual([]);
});

const relation: Relation = {
  id: "r1",
  from_service_id: "api",
  kind: "depends_on",
  to_service_id: "pg",
  to_code_project_id: null,
  description: "orders",
  revision: 3,
};

test("relation changes match by ends and kind: delete, update the description, create", () => {
  const built = { ...relation, id: "r2", kind: "built_from" as const, to_service_id: null, to_code_project_id: "shop" };
  const { id, revision, ...value } = relation;
  expect(
    relationChanges(
      [relation, built],
      [
        { ...value, description: "orders and refunds" },
        { ...value, kind: "part_of", description: "" },
      ],
    ),
  ).toEqual([
    { kind: "relations", method: "DELETE", id: "r2", revision: 3 },
    { kind: "relations", method: "PUT", id: "r1", revision: 3, value: { description: "orders and refunds" } },
    { kind: "relations", method: "POST", value: { ...value, kind: "part_of", description: "" } },
  ]);
  expect(relationChanges([relation], [value])).toEqual([]);
});

test("after a change the saved list drops a deleted item and holds the returned one", () => {
  const updated = { ...relation, revision: 4 };
  expect(afterChange([relation], { kind: "relations", method: "PUT", id: "r1" }, updated)).toEqual([updated]);
  expect(afterChange([relation], { kind: "relations", method: "DELETE", id: "r1" }, { id: "r1" })).toEqual([]);
  const created = { ...relation, id: "r9" };
  expect(afterChange([relation], { kind: "relations", method: "POST" }, created)).toEqual([relation, created]);
});

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
    to_host: null,
    port: 5432,
    protocol: "postgres",
    description: "",
    revision: 1,
  };
  expect(connectionText(connection, labels)).toBe("api on web-1 (prod) → pg on db-1 (prod) :5432 postgres");
  expect(
    connectionText(
      { ...connection, to_deployment_id: null, to_host: "smtp.example.com", port: 587, protocol: "smtp" },
      labels,
    ),
  ).toBe("api on web-1 (prod) → smtp.example.com :587 smtp");
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
