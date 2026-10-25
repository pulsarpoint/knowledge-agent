import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import routes from "~/routes";
import CodeProjectPage from "~/features/codeProjects/CodeProjectPage";
import CodeProjectsView from "~/features/codeProjects/CodeProjectsView";
import { resourceChatPath, resourceOf, resourceTag } from "~/features/sessions/api";
import { options as shared } from "~/lib/__tests__/agentOptionsFixture";

vi.mock("../Markdown", () => ({ default: ({ content }: { content: string }) => <div>{content}</div> }));
vi.mock("~/features/sessions/useSessionEvents", () => ({
  useSessionEvents: () => ({
    session: null,
    setSession: vi.fn(),
    error: "",
    setError: vi.fn(),
    events: [],
    older: "start",
    loadOlder: vi.fn(),
    toolCounts: undefined,
    reconnecting: false,
  }),
  useSessionNotice: () => ({ notice: null, clear: vi.fn(), stopping: vi.fn(), sent: vi.fn() }),
}));
vi.mock("~/features/sessions/RunWorkspace", () => ({
  default: ({ heading, empty }: Record<string, any>) => (
    <div>
      {heading}
      {empty}
    </div>
  ),
}));

const at = "2026-10-20T10:00:00Z";
const project = {
  id: "c1",
  name: "Shop",
  description: "The shop front end",
  remote_url: "https://github.com/example/shop.git",
  requested_ref: "main",
  clone_path: "shop",
  folder_paths: ["shop", "shared/ui"],
  current_commit: "0123456789abcdef0123456789abcdef01234567",
  checkout_path: "shop",
  has_token: false,
  synced_at: at,
  sync_status: "succeeded",
  sync_error: null,
  sync_interval_minutes: null,
  next_sync_at: null,
  revision: 2,
  processing: null,
  code_index: {
    status: "ready",
    source_hash: "h",
    source_file_count: 12,
    engine_version: "1",
    stats: { nodeCount: 40, edgeCount: 70, fileCount: 12, filesByLanguage: { typescript: 12 } },
    unresolved_references: 0,
    parse_error_files: [],
    missing_folders: [],
    processed_at: at,
  },
  built_into: [{ id: "v1", name: "storefront" }],
  sync_jobs: [
    { id: "j2", status: "succeeded", error: null, created_at: at, started_at: at, finished_at: at },
    { id: "j1", status: "failed", error: "Authentication failed", created_at: at, started_at: at, finished_at: at },
  ],
};
const overview = { id: "p9", name: "Code project overview", content: "Read the code project." };

function api(prompts = [overview]) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (raw: string, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${raw}`;
      calls.push(key);
      const routes: Record<string, unknown> = {
        "GET /api/code-projects/c1": project,
        "GET /api/sessions/options?code_project_id=c1": { ...shared, prompts, default_prompt_id: null },
        "GET /api/sessions?tag=code_projects%2Fc1": { sessions: [] },
        "GET /api/inventory/code_project/c1/documents": {
          inventory_document: null,
          documents: [
            {
              id: "d1",
              title: "Shop overview",
              tags: [],
              description_short: "What the shop code does",
              is_draft: false,
              draft_waiting: false,
              revision: 1,
              linked_elsewhere: [],
              resource: { type: "code_project", id: "c1", name: "Shop" },
            },
          ],
        },
        "GET /api/tools": { tools: [] },
        "POST /api/code-projects/c1/pull": { job_id: "j3" },
      };
      if (!(key in routes))
        return new Response(JSON.stringify({ error: { message: `unexpected ${key}` } }), { status: 404 });
      return new Response(JSON.stringify(routes[key]), { status: 200 });
    }),
  );
  return calls;
}

function show(url = "/code-projects/c1") {
  render(
    <MemoryRouter initialEntries={[url]}>
      <CodeProjectPage codeProjectId="c1" />
    </MemoryRouter>,
  );
}

afterEach(() => vi.unstubAllGlobals());

test("the page shows the code project's header and its Overview: details, folders, graph, syncs and Built into", async () => {
  api();
  show();
  expect(await screen.findByRole("heading", { name: "Shop", level: 1 })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Code projects" })).toHaveAttribute("href", "/code-projects");
  expect(screen.getByRole("link", { name: project.remote_url })).toHaveAttribute("href", project.remote_url);
  const header = within(screen.getByTestId("code-project-header"));
  expect(header.getByText("main")).toBeInTheDocument();
  expect(header.getByText("0123456789ab")).toBeInTheDocument();
  expect(header.getByText("Sync succeeded")).toBeInTheDocument();
  expect(header.getByText("Code graph ready")).toBeInTheDocument();
  for (const name of ["Chat", "Sync now", "Edit"]) expect(screen.getByRole("button", { name })).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: "Discover…" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tab", { name: "Documents" })).toBeInTheDocument();
  expect(screen.getByText("The shop front end")).toBeInTheDocument();
  const folders = within(screen.getByRole("region", { name: "Checkout folders" }));
  expect(folders.getByText("code/shop")).toBeInTheDocument();
  expect(folders.getByText("code/shared/ui")).toBeInTheDocument();
  const graph = within(screen.getByRole("region", { name: "Code graph" }));
  expect(graph.getByText(/12 indexed files/)).toBeInTheDocument();
  expect(graph.getByRole("button", { name: "Re-index" })).toBeInTheDocument();
  const syncs = within(screen.getByRole("region", { name: "Recent syncs" }));
  expect(syncs.getAllByRole("listitem")).toHaveLength(2);
  expect(syncs.getByText("Authentication failed")).toBeInTheDocument();
  const built = within(screen.getByRole("region", { name: "Built into" }));
  expect(built.getByRole("link", { name: "storefront" })).toHaveAttribute("href", "/services/v1");
});

test("the Documents tab lists the code project's documents with New document", async () => {
  api();
  show("/code-projects/c1?view=documents");
  const documents = within(await screen.findByRole("region", { name: "Documents" }));
  expect(await documents.findByRole("link", { name: "Shop overview" })).toHaveAttribute("href", "/documents/d1");
  expect(documents.getByRole("link", { name: "New document" })).toHaveAttribute(
    "href",
    "/documents/new?resource=code_project&resource_id=c1",
  );
  expect(documents.getByRole("button", { name: "Delete Shop overview" })).toBeInTheDocument();
});

test("Chat opens the chat panel on the code project", async () => {
  const calls = api();
  show();
  await userEvent.click(await screen.findByRole("button", { name: "Chat" }));
  expect(await screen.findByTestId("resource-chat")).toBeInTheDocument();
  expect(calls).toContain("GET /api/sessions?tag=code_projects%2Fc1");
  expect(calls).toContain("GET /api/sessions/options?code_project_id=c1");
});

test("Discover… is shown only when prompts for code projects exist", async () => {
  api([]);
  show();
  await screen.findByRole("heading", { name: "Shop", level: 1 });
  await waitFor(() => expect(screen.getByRole("button", { name: "Chat" })).toBeInTheDocument());
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(screen.queryByRole("button", { name: "Discover…" })).not.toBeInTheDocument();
});

test("Sync now queues a pull and Edit opens the code project form in a sheet", async () => {
  const calls = api();
  show();
  await userEvent.click(await screen.findByRole("button", { name: "Sync now" }));
  await waitFor(() => expect(calls).toContain("POST /api/code-projects/c1/pull"));
  await userEvent.click(screen.getByRole("button", { name: "Edit" }));
  const sheet = within(await screen.findByRole("dialog", { name: "Edit code project" }));
  expect(sheet.getByLabelText("Project name")).toHaveValue("Shop");
});

test("a code project chat's path and tag", () => {
  expect(resourceChatPath({ type: "code_project", id: "c 1" }, "r1")).toBe("/code-projects/c%201?chat=r1");
  expect(resourceTag({ type: "code_project", id: "c1" })).toBe("code_projects/c1");
  expect(resourceOf(["code_projects/c1"])).toEqual({ type: "code_project", id: "c1" });
  expect(routes.map((item) => [item.path, item.file])).toEqual(
    expect.arrayContaining([["code-projects/:codeProjectId", "./routes/codeProject.tsx"]]),
  );
});

test("the code projects list links each name to its page", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (raw: string) => {
      const url = new URL(raw, "http://localhost");
      const body =
        url.pathname === "/api/code-projects"
          ? { projects: [project] }
          : { root: "/work/code", host_path: null, path: "", total: 0, next_offset: null, entries: [] };
      return new Response(JSON.stringify(body), { status: 200 });
    }),
  );
  render(
    <MemoryRouter>
      <CodeProjectsView />
    </MemoryRouter>,
  );
  expect(await screen.findByRole("link", { name: "Shop" })).toHaveAttribute("href", "/code-projects/c1");
  expect(screen.getByRole("button", { name: "Actions for Shop" })).toBeInTheDocument();
});
