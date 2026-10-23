import type { ServiceAsset, ServiceDeployment } from "~/features/servers/services";

/** Test fixtures: a service as GET /api/services lists it, and one of its deployments. */
export const serviceDeployment = (id: string, environment: string, server: string): ServiceDeployment =>
  ({
    id,
    server_id: `s-${server}`,
    environment_id: `e-${environment}`,
    environment,
    runtime: "docker",
    runtime_name: "",
    port: null,
    log_location: "",
    deployed_version: "",
    status: "confirmed",
    server: { id: `s-${server}`, name: server },
  }) as ServiceDeployment;

export const serviceAsset = (
  id: string,
  name: string,
  deployments: ServiceDeployment[] = [],
  extra: Partial<ServiceAsset> = {},
): ServiceAsset => ({
  id,
  project_id: "p",
  name,
  description: "",
  owner: "",
  address: null,
  status: "confirmed",
  revision: 1,
  service_type: "custom",
  service_role: "application",
  created_at: "2026-10-22T00:00:00Z",
  updated_at: "2026-10-22T00:00:00Z",
  repositories: [],
  deployments,
  dependencies: [],
  dependents: [],
  environments: [],
  ...extra,
});

/** A fetch stub answering "METHOD url" routes; it records each call with its JSON body. */
export function stubApi(routes: Record<string, (body: any) => [number, unknown]>) {
  const calls: { key: string; body: any }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${url}`;
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      calls.push({ key, body });
      if (!(key in routes)) throw new Error(`unexpected ${key}`);
      const [status, value] = routes[key](body);
      return status === 204 ? new Response(null, { status }) : new Response(JSON.stringify(value), { status });
    }),
  );
  return calls;
}
