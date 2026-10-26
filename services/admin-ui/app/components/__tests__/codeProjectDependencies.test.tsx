import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import routes from "~/routes";
import { renderCodeProjects, sectionLink } from "~/lib/__tests__/codeProjectRoutes";
import { stubApi } from "~/lib/__tests__/serviceFixture";
import { options as shared } from "~/lib/__tests__/agentOptionsFixture";

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

const project = {
  id: "c1",
  name: "Shop",
  description: "",
  remote_url: null,
  requested_ref: null,
  clone_path: "shop",
  folder_paths: [],
  current_commit: null,
  checkout_path: "shop",
  has_token: false,
  synced_at: null,
  sync_status: null,
  sync_error: null,
  sync_interval_minutes: null,
  next_sync_at: null,
  revision: 1,
  processing: null,
  code_index: null,
  built_into: [],
  sync_jobs: [],
};
const repository = (id: string, name: string) => ({ ...project, id, name });
const dependency = (id: string, other: string, extra = {}) => ({
  id,
  revision: 4,
  status: "confirmed",
  description: "",
  code_project: { id: other.toLowerCase(), name: other },
  ...extra,
});
const dependencies = {
  depends_on: [
    dependency("d1", "Ui-kit", { description: "buttons and forms" }),
    dependency("d2", "Money", { status: "draft" }),
  ],
  used_by: {
    code_projects: [dependency("d3", "Admin")],
    services: [{ id: "v9", name: "storefront", status: "confirmed", via: [{ id: "admin", name: "Admin" }] }],
  },
};
const writes = (calls: { key: string; body: any }[]) =>
  calls.filter((call) => !call.key.startsWith("GET ")).map((call) => [call.key, call.body]);

function api(extra = {}) {
  return stubApi({
    "GET /api/code-projects/c1": () => [200, project],
    "GET /api/code-projects/c1/dependencies": () => [200, dependencies],
    "GET /api/sessions/options?code_project_id=c1": () => [200, { ...shared, discovery_prompts: [] }],
    "GET /api/sessions?tag=code_projects%2Fc1": () => [200, { sessions: [] }],
    "GET /api/repositories": () => [
      200,
      { repositories: [project, repository("ui-kit", "Ui-kit"), repository("lodash", "Lodash")] },
    ],
    "POST /api/code-dependencies": (body: object) => [201, { ...body, id: "d-new", status: "confirmed", revision: 1 }],
    ...extra,
  });
}

afterEach(() => vi.unstubAllGlobals());

test("the routes: Dependencies is a sub-page of the code project", () => {
  const children = routes.find((item) => item.path === "code-projects/:codeProjectId")?.children;
  expect(children?.map((item) => (item.index ? "(index)" : item.path))).toContain("dependencies");
});

test("Dependencies lists what it depends on and what uses it, all linked; used by is read-only", async () => {
  api();
  renderCodeProjects("/code-projects/c1/dependencies");
  const depends = within(await screen.findByRole("region", { name: "Depends on" }));
  expect(depends.getByRole("link", { name: "Ui-kit" })).toHaveAttribute("href", "/code-projects/ui-kit");
  expect(depends.getByText("buttons and forms")).toBeInTheDocument();
  const used = within(screen.getByRole("region", { name: "Used by" }));
  expect(used.getByRole("link", { name: "Admin" })).toHaveAttribute("href", "/code-projects/admin");
  expect(used.getByRole("link", { name: "storefront" })).toHaveAttribute("href", "/services/v9/view");
  expect(used.queryByRole("button")).toBeNull();
  expect(sectionLink("Code project sections", "Dependencies")).toHaveAttribute(
    "href",
    "/code-projects/c1/dependencies",
  );
});

test("an agent-drafted dependency shows as a draft with Confirm and Discard", async () => {
  api();
  renderCodeProjects("/code-projects/c1/dependencies");
  const depends = within(await screen.findByRole("region", { name: "Depends on" }));
  expect(depends.getAllByText("Draft")).toHaveLength(1);
  expect(depends.getByRole("button", { name: "Confirm Money" })).toBeInTheDocument();
  expect(depends.getByRole("button", { name: "Discard Money" })).toBeInTheDocument();
  expect(depends.queryByRole("button", { name: "Confirm Ui-kit" })).toBeNull();
});

test("Add picks another code project in a sheet (not itself, not one already listed)", async () => {
  const calls = api();
  renderCodeProjects("/code-projects/c1/dependencies");
  await screen.findByRole("region", { name: "Depends on" });
  await userEvent.click(screen.getByRole("button", { name: "Add" }));
  const sheet = within(await screen.findByRole("dialog", { name: "Add a dependency" }));
  expect(await sheet.findByRole("button", { name: /Lodash/ })).toBeInTheDocument();
  expect(sheet.queryByRole("button", { name: /Shop/ })).toBeNull();
  expect(sheet.queryByRole("button", { name: /Ui-kit/ })).toBeNull();
  await userEvent.click(sheet.getByRole("button", { name: /Lodash/ }));
  await userEvent.click(sheet.getByRole("button", { name: "Add" }));
  await waitFor(() =>
    expect(writes(calls)).toEqual([
      ["POST /api/code-dependencies", { from_code_project_id: "c1", to_code_project_id: "lodash", description: "" }],
    ]),
  );
});

test("Remove asks first, then deletes the dependency with its revision", async () => {
  const calls = api({ "DELETE /api/code-dependencies/d1?revision=4": () => [200, { id: "d1" }] });
  renderCodeProjects("/code-projects/c1/dependencies");
  const depends = within(await screen.findByRole("region", { name: "Depends on" }));
  await userEvent.click(depends.getByRole("button", { name: "Remove Ui-kit" }));
  expect(writes(calls)).toEqual([]);
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove" }));
  await waitFor(() => expect(writes(calls)).toEqual([["DELETE /api/code-dependencies/d1?revision=4", null]]));
});
