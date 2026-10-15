import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import EntrySheet from "~/features/servers/EntrySheet";

const api = { kind: "service" as const, id: "api", name: "api" };
const link = {
  id: "l1",
  kind: "connects_to",
  from: { kind: "service", id: "api", name: "api" },
  to: { kind: "server", id: "s1", name: "prod-1" },
  description: "reads its database",
  attributes: { port: "5432" },
  revision: 1,
};
const LINKS = "/api/inventory/links?entry_kind=service&entry_id=api";
const DOCS = "/api/inventory/service/api/documents";
const none = { inventory_document: null, documents: [] };

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

test("shows the entry's links and documents with a link to its page", async () => {
  server({
    [`GET ${LINKS}`]: () => ({ json: { links: [link] } }),
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
          { id: "d1", title: "Runbook", connection: "linked" },
          { id: "d2", title: "Design", connection: "built_for" },
        ],
      },
    }),
  });
  sheet();
  expect(await screen.findByText("connects to → prod-1")).toBeInTheDocument();
  expect(screen.getByText("reads its database · port: 5432")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open in Services" })).toHaveAttribute("href", "/services");
  const documents = within(screen.getByRole("region", { name: "Documents" }));
  expect(await documents.findByRole("link", { name: "Runbook" })).toHaveAttribute("href", "/documents/d1");
  expect(documents.getByRole("link", { name: "api overview" })).toHaveAttribute("href", "/documents/d0");
  expect(documents.getByText("Inventory")).toBeInTheDocument();
  // only a linked link can be removed: the inventory document's and other built-for links stay
  expect(documents.getByRole("button", { name: "Remove link: Runbook" })).toBeInTheDocument();
  expect(documents.queryByRole("button", { name: "Remove link: Design" })).toBeNull();
  expect(documents.queryByRole("button", { name: "Remove link: api overview" })).toBeNull();
});

test("connects to an entry: kinds follow the entry, targets follow the kind", async () => {
  const user = userEvent.setup();
  const onChanged = vi.fn();
  let posted: unknown = null;
  const fetch = server({
    [`GET ${LINKS}`]: () => ({ json: { links: [] } }),
    [`GET ${DOCS}`]: () => ({ json: none }),
    ["GET /api/map/search?kind=service&q=wor"]: () => ({
      json: { entries: [{ entry_kind: "service", entry_id: "w", name: "worker" }] },
    }),
    ["POST /api/inventory/links"]: (init) => {
      posted = JSON.parse(String(init?.body));
      return { status: 201, json: { link } };
    },
  });
  sheet(onChanged);
  const connect = within(await screen.findByRole("region", { name: "Connect" }));
  await user.click(connect.getByRole("combobox", { name: "Connection kind" }));
  expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual([
    "connects to",
    "depends on",
    "built from",
  ]);
  await user.click(screen.getByRole("option", { name: "depends on" }));
  await user.type(connect.getByLabelText("Attributes"), "protocol: grpc");
  await user.type(connect.getByRole("textbox", { name: "Find an entry" }), "wor");
  await user.click(connect.getByRole("button", { name: "Find" }));
  await user.click(await connect.findByRole("button", { name: "Connect: worker" }));
  await waitFor(() =>
    expect(posted).toEqual({
      from: { kind: "service", id: "api" },
      to: { kind: "service", id: "w" },
      kind: "depends_on",
      description: "",
      attributes: { protocol: "grpc" },
    }),
  );
  expect(onChanged).toHaveBeenCalled();
  expect(fetch.mock.calls.filter((call) => call[0] === LINKS)).toHaveLength(2); // reloaded after connecting
});

test("edits a link, shows a conflict, and removes it", async () => {
  const user = userEvent.setup();
  server({
    [`GET ${LINKS}`]: () => ({ json: { links: [link] } }),
    [`GET ${DOCS}`]: () => ({ json: none }),
    ["PUT /api/inventory/links/l1"]: () => ({
      status: 409,
      json: { error: { message: "This connection was changed by someone else. Reload and try again." } },
    }),
    ["DELETE /api/inventory/links/l1"]: () => ({ status: 204 }),
  });
  sheet();
  await user.click(await screen.findByRole("button", { name: "Edit connects to → prod-1" }));
  const description = screen.getAllByLabelText("Description")[0]; // the edit form comes before Connect
  await user.clear(description);
  await user.type(description, "replica");
  await user.click(screen.getByRole("button", { name: "Save connection" }));
  expect(
    await screen.findByText("This connection was changed by someone else. Reload and try again."),
  ).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Remove connects to → prod-1" }));
  await waitFor(() => expect(screen.queryByText(/changed by someone else/)).toBeNull());
});

test("Link a document adds a linked link; Remove link removes it", async () => {
  const user = userEvent.setup();
  let linked = false;
  let posted: unknown = null;
  const onChanged = vi.fn();
  const docs = () => ({
    inventory_document: null,
    documents: linked ? [{ id: "d1", title: "Runbook", connection: "linked" }] : [],
  });
  server({
    [`GET ${LINKS}`]: () => ({ json: { links: [] } }),
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

test("after a stale-revision save the links are fetched again, so Edit reopens the current revision", async () => {
  const user = userEvent.setup();
  let current = 1;
  const fetch = server({
    [`GET ${LINKS}`]: () => ({ json: { links: [{ ...link, revision: current }] } }),
    [`GET ${DOCS}`]: () => ({ json: none }),
    ["PUT /api/inventory/links/l1"]: () => {
      current = 2;
      return {
        status: 409,
        json: { error: { message: "This connection was changed by someone else. Reload and try again." } },
      };
    },
  });
  sheet();
  await user.click(await screen.findByRole("button", { name: "Edit connects to → prod-1" }));
  await user.click(screen.getByRole("button", { name: "Save connection" }));
  expect(await screen.findByText(/changed by someone else/)).toBeInTheDocument(); // the message stays visible
  await waitFor(() => expect(fetch.mock.calls.filter((call) => call[0] === LINKS)).toHaveLength(2));
});
