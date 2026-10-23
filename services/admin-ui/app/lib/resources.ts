/** The kinds of resource a document links to or an agent run may use, each described once: its key in option lists,
 * its id-list field in requests, and its labels. Each screen picks its own subset, in its own order, with `kindsOf`. */
export const RESOURCE_KINDS = [
  { key: "servers", field: "server_ids", label: "Server", plural: "Servers" },
  { key: "services", field: "service_ids", label: "Service", plural: "Services" },
  { key: "environments", field: "environment_ids", label: "Environment", plural: "Environments" },
  { key: "code_projects", field: "code_project_ids", label: "Code project", plural: "Code projects" },
  { key: "deployments", field: "deployment_ids", label: "Deployment", plural: "Specific deployments" },
] as const;

export type ResourceKind = (typeof RESOURCE_KINDS)[number];
export type ResourceKey = ResourceKind["key"];
export type ResourceField = ResourceKind["field"];
export type KindOf<K extends ResourceKey> = Extract<ResourceKind, { key: K }>;
export type FieldOf<K extends ResourceKey> = KindOf<K>["field"];
/** The id lists of some kinds, e.g. `ResourceIds<'servers' | 'services'>` is `{ server_ids: string[]; service_ids: string[] }`. */
export type ResourceIds<K extends ResourceKey> = { [F in FieldOf<K>]: string[] };

/** The kinds with these keys, in the order given. */
export const kindsOf = <const K extends ResourceKey>(keys: readonly K[]): KindOf<K>[] =>
  keys.map((key) => RESOURCE_KINDS.find((kind) => kind.key === key) as KindOf<K>);

/** Empty id lists for these kinds. */
export const emptyIds = <K extends ResourceKey>(kinds: readonly KindOf<K>[]) =>
  Object.fromEntries(kinds.map((kind) => [kind.field, []])) as unknown as ResourceIds<K>;

/** The id lists of these kinds taken from a value that holds them (a missing list is empty). */
export const pickIds = <K extends ResourceKey>(
  kinds: readonly KindOf<K>[],
  value: Partial<Record<ResourceField, string[]>>,
) => Object.fromEntries(kinds.map((kind) => [kind.field, value[kind.field] ?? []])) as unknown as ResourceIds<K>;

/** The id lists of these kinds from lists of resources keyed by kind (e.g. a version's source snapshot). */
export const idsOf = <K extends ResourceKey>(
  kinds: readonly KindOf<K>[],
  value: Partial<Record<ResourceKey, { id: string }[]>>,
) =>
  Object.fromEntries(
    kinds.map((kind) => [kind.field, (value[kind.key] ?? []).map((item) => item.id)]),
  ) as unknown as ResourceIds<K>;

/** What a document links: environments, code projects, services, servers and specific deployments. */
export const DOCUMENT_KINDS = kindsOf(["environments", "code_projects", "services", "servers", "deployments"]);
/** What an agent run may be given: servers, services, environments and code projects. */
export const AGENT_KINDS = kindsOf(["servers", "services", "environments", "code_projects"]);
export type DocumentKey = (typeof DOCUMENT_KINDS)[number]["key"];
export type AgentKey = (typeof AGENT_KINDS)[number]["key"];
