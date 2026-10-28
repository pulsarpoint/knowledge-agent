import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { documentRoute } from "~/lib/__tests__/pageRoutes";

vi.mock("../Markdown", () => ({ default: ({ content }: { content: string }) => <article>{content}</article> }));
const NativeRequest = globalThis.Request;
beforeEach(() =>
  vi.stubGlobal(
    "Request",
    class extends NativeRequest {
      constructor(input: RequestInfo | URL, init?: RequestInit) {
        super(input, { ...init, signal: undefined });
      }
    },
  ),
);
afterEach(() => vi.unstubAllGlobals());

const at = "2026-10-12T10:00:00Z";
const later = "2026-10-13T09:00:00Z";
const earlier = "2026-10-11T08:00:00Z";
const resources = {
  code_projects: [],
  services: [{ id: "svc-1", name: "Payments API" }],
  servers: [],
  deployments: [],
  environments: [
    { id: "env-1", name: "Prod" },
    { id: "env-2", name: "Staging" },
  ],
};
const builtFor = {
  type: "environment",
  id: "env-1",
  name: "Prod",
  connection: "built_for",
  added_how: "agent_run",
  added_by: "ana@example.com",
  session_id: "s1",
  created_at: at,
};
const linked = {
  type: "service",
  id: "svc-1",
  name: "Payments API",
  connection: "linked",
  added_how: "person",
  added_by: "bo@example.com",
  session_id: null,
  created_at: at,
};
const document = {
  id: "doc-1",
  slug: "runbook",
  title: "Runbook",
  description: "",
  purpose: "runbook",
  audience: "both",
  tags: [],
  code_project_ids: [],
  service_ids: ["svc-1"],
  server_ids: [],
  deployment_ids: [],
  environment_ids: ["env-1"],
  revision: 2,
  version: 1,
  content_hash: "a",
  origin: "generated",
  from_draft: true,
  created_at: at,
  updated_at: at,
  markdown: "# Runbook",
  source_snapshot: { ...resources, services: [], environments: [] },
  versions: [{ version: 1, title: "Runbook", origin: "generated", created_at: at, from_draft: true, live: true }],
  drafts: [],
  resource_links: [builtFor, linked],
};
/** The same document as a brand-new draft: its links are saved on Linked resources (a live document's on Connections). */
const draft = { ...document, is_draft: true, published: false, versions: [] };
const BUILT_FOR = "This document was built for environment Staging; that link can't be removed.";

function api(routes: Record<string, (body: any) => [number, unknown]>) {
  const calls: { key: string; body: any }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${url}`;
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      calls.push({ key, body });
      if (!(key in routes)) throw new Error(`unexpected ${key}`);
      const [status, value] = routes[key](body);
      return new Response(JSON.stringify(value), { status });
    }),
  );
  return calls;
}
function show(url: string) {
  const router = createMemoryRouter([documentRoute], {
    initialEntries: [url],
  });
  render(<RouterProvider router={router} />);
  return router;
}

test("Linked resources locks a built-for environment (ticked, disabled, noted); a linked service toggles", async () => {
  const calls = api({
    "GET /api/documentation/documents/doc-1": () => [200, draft],
    "GET /api/documentation/resources": () => [200, resources],
    "PUT /api/documentation/documents/doc-1/links": () => [
      200,
      { id: "doc-1", revision: 3, resource_links: [builtFor] },
    ],
  });
  show("/documents/doc-1?tab=resources");
  const prod = await screen.findByRole("checkbox", { name: "Prod" });
  expect(prod).toBeChecked();
  expect(prod).toBeDisabled();
  expect(screen.getByText("Built for this document")).toBeInTheDocument();
  expect(screen.getAllByText("Built for this document")).toHaveLength(1);
  const service = screen.getByRole("checkbox", { name: "Payments API" });
  expect(service).toBeChecked();
  expect(service).toBeEnabled();
  fireEvent.click(service);
  expect(service).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Save links" }));
  await waitFor(() =>
    expect(calls.find((c) => c.key === "PUT /api/documentation/documents/doc-1/links")?.body).toEqual({
      revision: 2,
      code_project_ids: [],
      service_ids: [],
      server_ids: [],
      deployment_ids: [],
      environment_ids: ["env-1"],
    }),
  );
});

test("Save links refused for a built-for link shows why under Linked resources and reloads the links, keeping the text", async () => {
  // The page was loaded before Staging became a built-for link (Review Focus 3).
  const stale = { ...draft, environment_ids: ["env-1"], resource_links: [builtFor, linked] };
  const fresh = {
    ...draft,
    revision: 3,
    environment_ids: ["env-1", "env-2"],
    resource_links: [builtFor, linked, { ...builtFor, id: "env-2", name: "Staging", created_at: later }],
  };
  let loads = 0;
  const calls = api({
    "GET /api/documentation/documents/doc-1": () => [200, loads++ === 0 ? stale : fresh],
    "GET /api/documentation/resources": () => [200, resources],
    "PUT /api/documentation/documents/doc-1/links": () => [409, { error: { message: BUILT_FOR } }],
  });
  show("/documents/doc-1");
  fireEvent.change(await screen.findByLabelText("Markdown"), { target: { value: "# Runbook, edited" } });
  fireEvent.mouseDown(screen.getByRole("tab", { name: "Linked resources" }), { button: 0, ctrlKey: false });
  const panel = within(await screen.findByRole("tabpanel", { name: "Linked resources" }));
  fireEvent.click(panel.getByRole("checkbox", { name: "Payments API" }));
  fireEvent.click(panel.getByRole("button", { name: "Save links" }));
  expect(await panel.findByText(BUILT_FOR)).toBeInTheDocument();
  await waitFor(() => expect(panel.getByRole("checkbox", { name: "Staging" })).toBeDisabled());
  expect(panel.getByRole("checkbox", { name: "Staging" })).toBeChecked();
  expect(panel.getAllByText("Built for this document")).toHaveLength(2);
  expect(calls.filter((c) => c.key === "GET /api/documentation/documents/doc-1")).toHaveLength(2);
  expect(screen.getByLabelText("Markdown")).toHaveValue("# Runbook, edited");
});

test("Link history (Connections, Resources) lists the links added and removed, with how, when and who, and links the run", async () => {
  const history = [
    {
      ...linked,
      id: "svc-2",
      name: "Billing",
      created_at: earlier,
      removed_at: later,
      removed_by: "bo@example.com",
    },
    { ...builtFor, removed_at: null, removed_by: null },
  ];
  api({
    "GET /api/documentation/documents/doc-1": () => [200, document],
    "GET /api/documentation/resources": () => [200, resources],
    "GET /api/documentation/documents/doc-1/resource-history": () => [200, { history }],
    "GET /api/documentation/documents/doc-1/connections": () => [
      200,
      { revision: 2, resources: [], depends_on: [], used_by: [], built_from: [] },
    ],
  });
  show("/documents/doc-1/connections");
  fireEvent.click(await screen.findByRole("button", { name: "Link history" }));
  const section = within(await screen.findByRole("region", { name: "Link history" }));
  const lines = await section.findAllByRole("listitem");
  const when = (value: string) => new Date(value).toLocaleString();
  expect(lines.map((line) => line.textContent)).toEqual([
    `Removed Billing (service) · ${when(later)} · bo@example.com`,
    `Added Prod (environment) · built for · agent run · ${when(at)} · ana@example.com`,
    `Added Billing (service) · linked · person · ${when(earlier)} · bo@example.com`,
  ]);
  expect(section.getByRole("link", { name: "agent run" })).toHaveAttribute("href", "/sessions/s1");
});
