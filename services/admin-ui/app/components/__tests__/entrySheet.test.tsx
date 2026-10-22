import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import EntrySheet from "~/features/servers/EntrySheet";

const api = { kind: "service" as const, id: "api", name: "api" };
const deployment = (id: string, server: string, port: number | null) => ({
  id,
  server_id: server,
  environment_id: "prod",
  environment: "prod",
  runtime: "",
  runtime_name: "",
  port,
  log_location: "",
  deployed_version: "",
  server: { name: server },
});
const SERVICES = {
  services: [
    { id: "api", name: "api", deployments: [deployment("d-api", "web-1", 8080)] },
    { id: "pg", name: "pg", deployments: [deployment("d-pg", "db-1", 5432)] },
    { id: "worker", name: "worker", deployments: [] },
  ],
};
const connection = {
  id: "c1",
  from_deployment_id: "d-api",
  to_deployment_id: "d-pg",
  to_service_id: null,
  to_host: null,
  port: 5432,
  protocol: "postgres",
  description: "reads its database",
  revision: 1,
};
const relation = {
  id: "r1",
  from_service_id: "worker",
  kind: "part_of",
  to_service_id: "api",
  to_code_project_id: null,
  description: "",
  revision: 2,
};
const CONNECTIONS = "/api/services/api/connections";
const RELATIONS = "/api/services/api/relations";
/** The reads a service entry's sheet makes, with these connections and relations. */
const reads = (connections: unknown[] = [], relations: unknown[] = []) => ({
  ["GET /api/services"]: () => ({ json: SERVICES }),
  ["GET /api/repositories"]: () => ({ json: { repositories: [{ id: "shop", name: "shop" }] } }),
  [`GET ${CONNECTIONS}`]: () => ({ json: { connections } }),
  [`GET ${RELATIONS}`]: () => ({ json: { relations } }),
});
const DOCS = "/api/inventory/service/api/documents";
const none = { inventory_document: null, documents: [] };
/** One entry of the resource's document list (belonging to `api` unless another resource is given). */
const listed = (
  id: string,
  title: string,
  connection: "built_for" | "linked",
  tagged = false,
  resource = { type: "service", id: "api", name: "api" },
) => ({ id, title, tags: tagged ? ["inventory"] : [], description_short: "", connection, resource });

function server(routes: Record<string, (init?: RequestInit) => { status?: number; json?: unknown }>) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    const route = routes[key];
    if (!route) throw new Error(`unexpected ${key}`);
    const { status = 200, json } = route(init);
    return new Response(status === 204 ? null : JSON.stringify(json), { status });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const sheet = (onChanged = vi.fn()) =>
  render(
    <MemoryRouter>
      <EntrySheet entry={api} onClose={() => {}} onChanged={onChanged} />
    </MemoryRouter>,
  );
afterEach(() => vi.unstubAllGlobals());

test("shows a service's connections and relations in words, and its documents", async () => {
  server({
    ...reads([connection], [relation]),
    [`GET ${DOCS}`]: () => ({
      json: {
        inventory_document: {
          id: "d0",
          title: "api overview",
          markdown: "# api",
          version: 1,
          is_draft: false,
          published: true,
        },
        documents: [
          listed("d0", "api overview", "built_for", true),
          listed("d2", "Design", "built_for"),
          listed("d1", "Runbook", "linked"),
          listed("d3", "Disk notes", "built_for", false, { type: "server", id: "s1", name: "prod-1" }),
        ],
      },
    }),
  });
  sheet();
  const connections = within(await screen.findByRole("region", { name: "Connections" }));
  expect(await connections.findByText("api on web-1 (prod) → pg on db-1 (prod) :5432 postgres")).toBeInTheDocument();
  expect(connections.getByText("reads its database")).toBeInTheDocument();
  expect(
    within(screen.getByRole("region", { name: "Relations" })).getByText("worker is part of api"),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open in Services" })).toHaveAttribute("href", "/services");
  const documents = within(screen.getByRole("region", { name: "Documents" }));
  expect(await documents.findByRole("link", { name: "Runbook" })).toHaveAttribute("href", "/documents/d1");
  expect(documents.getByRole("link", { name: "api overview" })).toHaveAttribute("href", "/documents/d0");
  expect(documents.getByText("Inventory")).toBeInTheDocument();
  // only a linked link can be removed: the inventory document's and other built-for links stay
  expect(documents.getByRole("button", { name: "Remove link: Runbook" })).toBeInTheDocument();
  expect(documents.queryByRole("button", { name: "Remove link: Design" })).toBeNull();
  expect(documents.queryByRole("button", { name: "Remove link: api overview" })).toBeNull();
  expect(documents.getAllByRole("link", { name: "api overview" })).toHaveLength(1);
  // a child's document is on the resource page's list, not among the entry's own links
  expect(documents.queryByRole("link", { name: "Disk notes" })).toBeNull();
});

test("adds a connection to another deployment with the target's port prefilled", async () => {
  const user = userEvent.setup();
  const onChanged = vi.fn();
  let posted: unknown = null;
  const fetch = server({
    ...reads(),
    [`GET ${DOCS}`]: () => ({ json: none }),
    ["POST /api/connections"]: (init) => {
      posted = JSON.parse(String(init?.body));
      return { status: 201, json: connection };
    },
  });
  sheet(onChanged);
  const form = within(await screen.findByRole("region", { name: "New connection" }));
  expect(await form.findByRole("combobox", { name: "From" })).toHaveTextContent("api on web-1 (prod)");
  await user.click(form.getByRole("combobox", { name: "To" }));
  expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual([
    "pg on db-1 (prod)",
    "An external host",
  ]);
  await user.click(screen.getByRole("option", { name: "pg on db-1 (prod)" }));
  expect(form.getByLabelText("Port")).toHaveValue(5432);
  await user.clear(form.getByLabelText("Protocol"));
  await user.type(form.getByLabelText("Protocol"), "postgres");
  await user.type(form.getByLabelText("Description"), "orders");
  await user.click(form.getByRole("button", { name: "Add connection" }));
  await waitFor(() =>
    expect(posted).toEqual({
      from_deployment_id: "d-api",
      to_deployment_id: "d-pg",
      to_service_id: null,
      to_host: null,
      port: 5432,
      protocol: "postgres",
      description: "orders",
    }),
  );
  expect(onChanged).toHaveBeenCalled();
  await waitFor(() => expect(fetch.mock.calls.filter((call) => call[0] === CONNECTIONS)).toHaveLength(2));
});

test("adds a connection to an external host", async () => {
  const user = userEvent.setup();
  let posted: unknown = null;
  server({
    ...reads(),
    [`GET ${DOCS}`]: () => ({ json: none }),
    ["POST /api/connections"]: (init) => {
      posted = JSON.parse(String(init?.body));
      return { status: 201, json: connection };
    },
  });
  sheet();
  const form = within(await screen.findByRole("region", { name: "New connection" }));
  await user.click(await form.findByRole("combobox", { name: "To" }));
  await user.click(screen.getByRole("option", { name: "An external host" }));
  await user.type(form.getByLabelText("Host"), "smtp.example.com");
  await user.type(form.getByLabelText("Port"), "587");
  await user.clear(form.getByLabelText("Protocol"));
  await user.type(form.getByLabelText("Protocol"), "smtp");
  await user.click(form.getByRole("button", { name: "Add connection" }));
  await waitFor(() =>
    expect(posted).toEqual({
      from_deployment_id: "d-api",
      to_deployment_id: null,
      to_service_id: null,
      to_host: "smtp.example.com",
      port: 587,
      protocol: "smtp",
      description: "",
    }),
  );
});

test("edits a connection, shows a conflict and reloads, and removes it with its revision", async () => {
  const user = userEvent.setup();
  let current = 1;
  let put: unknown = null;
  const fetch = server({
    ...reads([connection], []),
    [`GET ${CONNECTIONS}`]: () => ({ json: { connections: [{ ...connection, revision: current }] } }),
    [`GET ${DOCS}`]: () => ({ json: none }),
    ["PUT /api/connections/c1"]: (init) => {
      put = JSON.parse(String(init?.body));
      current = 2;
      return { status: 409, json: { error: { message: "This connection changed since you read it." } } };
    },
    ["DELETE /api/connections/c1?revision=2"]: () => ({ json: { id: "c1" } }),
  });
  sheet();
  const text = "api on web-1 (prod) → pg on db-1 (prod) :5432 postgres";
  await user.click(await screen.findByRole("button", { name: `Edit ${text}` }));
  const edit = within(screen.getByRole("form", { name: `Edit ${text}` }));
  await user.clear(edit.getByLabelText("Description"));
  await user.type(edit.getByLabelText("Description"), "replica");
  await user.click(edit.getByRole("button", { name: "Save connection" }));
  expect(await screen.findByText("This connection changed since you read it.")).toBeInTheDocument();
  expect(put).toEqual({
    from_deployment_id: "d-api",
    to_deployment_id: "d-pg",
    to_service_id: null,
    to_host: null,
    port: 5432,
    protocol: "postgres",
    description: "replica",
    revision: 1,
  });
  await waitFor(() => expect(fetch.mock.calls.filter((call) => call[0] === CONNECTIONS)).toHaveLength(2));
  await user.click(screen.getByRole("button", { name: `Remove ${text}` }));
  await waitFor(() => expect(fetch.mock.calls.some((call) => call[0] === "/api/connections/c1?revision=2")).toBe(true));
});

test("adds a built_from relation to a code project and removes a relation", async () => {
  const user = userEvent.setup();
  let posted: unknown = null;
  const fetch = server({
    ...reads([], [relation]),
    [`GET ${DOCS}`]: () => ({ json: none }),
    ["POST /api/relations"]: (init) => {
      posted = JSON.parse(String(init?.body));
      return { status: 201, json: relation };
    },
    ["DELETE /api/relations/r1?revision=2"]: () => ({ json: { id: "r1" } }),
  });
  sheet();
  const form = within(await screen.findByRole("region", { name: "New relation" }));
  await user.click(form.getByRole("combobox", { name: "Relation" }));
  expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual([
    "depends on",
    "is part of",
    "is built from",
  ]);
  await user.click(screen.getByRole("option", { name: "is built from" }));
  await user.click(form.getByRole("combobox", { name: "Target" }));
  expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual(["shop"]);
  await user.click(screen.getByRole("option", { name: "shop" }));
  await user.click(form.getByRole("button", { name: "Add relation" }));
  await waitFor(() =>
    expect(posted).toEqual({
      from_service_id: "api",
      kind: "built_from",
      to_service_id: null,
      to_code_project_id: "shop",
      description: "",
    }),
  );
  await user.click(await screen.findByRole("button", { name: "Remove worker is part of api" }));
  await waitFor(() => expect(fetch.mock.calls.some((call) => call[0] === "/api/relations/r1?revision=2")).toBe(true));
});

test("a server entry explains that connections belong to services", async () => {
  const fetch = server({ ["GET /api/inventory/server/s1/documents"]: () => ({ json: none }) });
  render(
    <MemoryRouter>
      <EntrySheet entry={{ kind: "server", id: "s1", name: "prod-1" }} onClose={() => {}} onChanged={() => {}} />
    </MemoryRouter>,
  );
  expect(await screen.findByText(/Connections and relations belong to services/)).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "New connection" })).toBeNull();
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
});

test("Link a document adds a linked link; Remove link removes it", async () => {
  const user = userEvent.setup();
  let linked = false;
  let posted: unknown = null;
  const onChanged = vi.fn();
  const docs = () => ({
    inventory_document: null,
    documents: linked ? [listed("d1", "Runbook", "linked")] : [],
  });
  server({
    ...reads(),
    [`GET ${DOCS}`]: () => ({ json: docs() }),
    ["GET /api/documentation/documents?q=run"]: () => ({
      json: { documents: [{ id: "d1", slug: "runbook", title: "Runbook" }] },
    }),
    [`POST ${DOCS}`]: (init) => {
      posted = JSON.parse(String(init?.body));
      linked = true;
      return { status: 201, json: docs() };
    },
    [`DELETE ${DOCS}/d1`]: () => {
      linked = false;
      return { json: docs() };
    },
  });
  sheet(onChanged);
  const documents = within(await screen.findByRole("region", { name: "Documents" }));
  expect(documents.getByText("Link a document")).toBeInTheDocument();
  await user.type(documents.getByRole("textbox", { name: "Find a document" }), "run");
  await user.click(documents.getByRole("button", { name: "Find" }));
  await user.click(await documents.findByRole("button", { name: "Link: Runbook" }));
  await waitFor(() => expect(posted).toEqual({ document_id: "d1" }));
  expect(onChanged).toHaveBeenCalled();
  await user.click(await documents.findByRole("button", { name: "Remove link: Runbook" }));
  await waitFor(() => expect(documents.getByText("No documents are linked.")).toBeInTheDocument());
});
