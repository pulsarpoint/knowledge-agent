import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import routes from "~/routes";
import { renderCodeProjects, sectionLink } from "~/lib/__tests__/codeProjectRoutes";
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
        "GET /api/sessions/options?code_project_id=c1": { ...shared, discovery_prompts: prompts },
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

const show = (url = "/code-projects/c1") => renderCodeProjects(url);

afterEach(() => vi.unstubAllGlobals());

test("the page shows the code project's header and opens Overview: details, folders, graph, syncs and Built into", async () => {
  api();
  const { router } = show();
  expect(await screen.findByRole("heading", { name: "Shop", level: 2 })).toBeInTheDocument();
  await waitFor(() => expect(router.state.location.pathname).toBe("/code-projects/c1/overview"));
  expect(screen.getByRole("link", { name: "All code projects" })).toHaveAttribute("href", "/code-projects");
  expect(screen.getByRole("link", { name: project.remote_url })).toHaveAttribute("href", project.remote_url);
  const header = within(screen.getByTestId("code-project-header"));
  expect(header.getByText("main")).toBeInTheDocument();
  expect(header.getByText("0123456789ab")).toBeInTheDocument();
  expect(header.getByText("Sync succeeded")).toBeInTheDocument();
  expect(header.getByText("Code graph ready")).toBeInTheDocument();
  for (const name of ["Chat", "Sync now"]) expect(screen.getByRole("button", { name })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull(); // Settings replaces the Edit sheet
  expect(await screen.findByRole("button", { name: "Discover…" })).toBeInTheDocument();
  expect(sectionLink("Code project sections", "Overview")).toHaveAttribute("aria-current", "page");
  expect(sectionLink("Code project sections", "Documents")).toHaveAttribute("href", "/code-projects/c1/documents");
  expect(sectionLink("Code project sections", "Settings")).toHaveAttribute("href", "/code-projects/c1/settings");
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

test("an old ?view= link opens its sub-page and keeps the rest of the query", async () => {
  api();
  const { router } = show("/code-projects/c1?view=documents&chat=r1");
  await waitFor(() => expect(router.state.location.pathname).toBe("/code-projects/c1/documents"));
  expect(router.state.location.search).toBe("?chat=r1");
});

test("the Documents sub-page lists the code project's documents with New document", async () => {
  api();
  show("/code-projects/c1/documents");
  const documents = within(await screen.findByRole("region", { name: "Documents" }));
  expect(await documents.findByRole("link", { name: "Shop overview" })).toHaveAttribute("href", "/documents/d1");
  expect(documents.getByRole("link", { name: "New document" })).toHaveAttribute(
    "href",
    "/documents/new?resource=code_project&resource_id=c1",
  );
  expect(documents.getByRole("button", { name: "Delete Shop overview" })).toBeInTheDocument();
});

test("Chat opens the chat panel on the code project, and it stays open across sub-pages", async () => {
  const calls = api();
  show();
  await userEvent.click(await screen.findByRole("button", { name: "Chat" }));
  expect(await screen.findByTestId("resource-chat")).toBeInTheDocument();
  expect(calls).toContain("GET /api/sessions?tag=code_projects%2Fc1");
  expect(calls).toContain("GET /api/sessions/options?code_project_id=c1");
  await userEvent.click(sectionLink("Code project sections", "Documents"));
  expect(await screen.findByRole("region", { name: "Documents" })).toBeInTheDocument();
  expect(screen.getByTestId("resource-chat")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Conversations" }));
  expect(await screen.findByRole("dialog", { name: "Conversations" })).toBeInTheDocument();
});

test("Discover… is shown only when discovery prompts for code projects exist", async () => {
  api([]);
  show();
  await screen.findByRole("heading", { name: "Shop", level: 2 });
  await waitFor(() => expect(screen.getByRole("button", { name: "Chat" })).toBeInTheDocument());
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(screen.queryByRole("button", { name: "Discover…" })).not.toBeInTheDocument();
});

test("Sync now queues a pull, and Settings shows the project's fields to edit in place", async () => {
  const calls = api();
  show();
  await userEvent.click(await screen.findByRole("button", { name: "Sync now" }));
  await waitFor(() => expect(calls).toContain("POST /api/code-projects/c1/pull"));
  expect(await screen.findByText("Git pull queued.")).toBeInTheDocument();
  await userEvent.click(sectionLink("Code project sections", "Settings"));
  expect(await screen.findByLabelText("Project name")).toHaveValue("Shop");
  expect(screen.getByLabelText("Git repository URL")).toHaveValue(project.remote_url);
  expect(screen.getByLabelText("Clone folder")).toHaveValue("shop");
  expect(screen.getByLabelText("Branch, tag, or commit (optional)")).toHaveValue("main");
  expect(screen.getByRole("button", { name: "Unmap shared/ui" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Discard" })).toBeDisabled();
});

test("a code project chat's path and tag", () => {
  expect(resourceChatPath({ type: "code_project", id: "c 1" }, "r1")).toBe("/code-projects/c%201/overview?chat=r1");
  expect(resourceChatPath({ type: "code_project", id: "c1" })).toBe("/code-projects/c1/overview");
  expect(resourceTag({ type: "code_project", id: "c1" })).toBe("code_projects/c1");
  expect(resourceOf(["code_projects/c1"])).toEqual({ type: "code_project", id: "c1" });
  const children = (path: string) =>
    routes.find((item) => item.path === path)?.children?.map((item) => (item.index ? "(index)" : item.path));
  expect(children("code-projects")).toEqual(["(index)", "folder"]);
  expect(children("code-projects/:codeProjectId")).toEqual(["(index)", "overview", "documents", "settings"]);
});

test("the code projects list links each name to its page", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ projects: [project] }), { status: 200 })),
  );
  show("/code-projects");
  expect(await screen.findByRole("link", { name: "Shop" })).toHaveAttribute("href", "/code-projects/c1");
  expect(screen.getByRole("button", { name: "Delete Shop" })).toBeInTheDocument();
});
