import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import routes from "~/routes";
import { renderCodeProjects, sectionLink } from "~/lib/__tests__/codeProjectRoutes";
import { stubApi } from "~/lib/__tests__/serviceFixture";
import { options as shared } from "~/lib/__tests__/agentOptionsFixture";
import { menuItems, rowAction } from "~/lib/__tests__/rowMenu";

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
    services: [
      { id: "v9", name: "storefront", status: "confirmed", via: [{ id: "admin", name: "Admin", status: "confirmed" }] },
      { id: "v8", name: "beta", status: "confirmed", via: [{ id: "admin", name: "Admin", status: "draft" }] },
      { id: "v7", name: "gamma", status: "draft", via: [{ id: "admin", name: "Admin", status: "confirmed" }] },
    ],
  },
};
const writes = (calls: { key: string; body: any }[]) =>
  calls.filter((call) => !call.key.startsWith("GET ")).map((call) => [call.key, call.body]);

function api(extra = {}) {
  return stubApi({
    "GET /api/code-projects/c1": () => [200, project],
    "GET /api/code-projects/c1/dependencies": () => [200, dependencies],
    "GET /api/sessions/options?code_project_id=c1": () => [200, { ...shared, discovery_prompts: [] }],
    "GET /api/sessions?tag=code_projects%2Fc1&purpose=resource": () => [200, { sessions: [] }],
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
  expect(await depends.findByRole("link", { name: "Ui-kit" })).toHaveAttribute("href", "/code-projects/ui-kit");
  expect(depends.getByText("buttons and forms")).toBeInTheDocument();
  const used = within(screen.getByRole("region", { name: "Used by" }));
  expect(await used.findByRole("link", { name: "Admin" })).toHaveAttribute("href", "/code-projects/admin");
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
  expect(await depends.findByRole("button", { name: "Confirm Money" })).toBeInTheDocument();
  expect(depends.getAllByText("Draft")).toHaveLength(1);
  await userEvent.click(depends.getByRole("button", { name: "Actions for Money" }));
  expect(menuItems()).toContain("Discard");
  await userEvent.keyboard("{Escape}");
  expect(depends.queryByRole("button", { name: "Confirm Ui-kit" })).toBeNull();
});

test("Used by marks drafts: a draft service and one reached through a draft built-from link", async () => {
  api();
  renderCodeProjects("/code-projects/c1/dependencies");
  const used = within(await screen.findByRole("region", { name: "Used by" }));
  const row = async (name: string) => (await used.findByRole("link", { name })).closest("li") as HTMLElement;
  expect(within(await row("storefront")).queryByText("Draft")).toBeNull();
  expect(within(await row("beta")).getByText("Draft")).toBeInTheDocument();
  expect(within(await row("gamma")).getByText("Draft")).toBeInTheDocument();
});

test("Confirm checks first (dry run), then confirms the draft dependency with its revision", async () => {
  const reached = { confirmed: [{ kind: "code_dependency", id: "d2", name: "Shop depends on Money" }] };
  const calls = api({
    "POST /api/inventory/code_dependency/d2/confirm?dry_run=1": () => [200, reached],
    "POST /api/inventory/code_dependency/d2/confirm": () => [200, reached],
  });
  renderCodeProjects("/code-projects/c1/dependencies");
  const depends = within(await screen.findByRole("region", { name: "Depends on" }));
  await userEvent.click(await depends.findByRole("button", { name: "Confirm Money" }));
  const sheet = within(await screen.findByRole("dialog", { name: "Confirm Money?" }));
  await sheet.findByText("Nothing else is confirmed with it.");
  await userEvent.click(sheet.getByRole("button", { name: "Confirm" }));
  await waitFor(() =>
    expect(writes(calls)).toEqual([
      ["POST /api/inventory/code_dependency/d2/confirm?dry_run=1", { revision: 4 }],
      ["POST /api/inventory/code_dependency/d2/confirm", { revision: 4 }],
    ]),
  );
});

test("Discard checks first (dry run), then discards the draft dependency with its revision", async () => {
  const reached = { discarded: [] };
  const calls = api({
    "POST /api/inventory/code_dependency/d2/discard?dry_run=1": () => [200, reached],
    "POST /api/inventory/code_dependency/d2/discard": () => [200, reached],
  });
  renderCodeProjects("/code-projects/c1/dependencies");
  const depends = within(await screen.findByRole("region", { name: "Depends on" }));
  await rowAction(userEvent, "Money", "Discard", depends);
  const sheet = within(await screen.findByRole("dialog", { name: "Discard Money?" }));
  await sheet.findByText("Nothing else is deleted with it.");
  await userEvent.click(sheet.getByRole("button", { name: "Discard" }));
  await waitFor(() =>
    expect(writes(calls)).toEqual([
      ["POST /api/inventory/code_dependency/d2/discard?dry_run=1", { revision: 4 }],
      ["POST /api/inventory/code_dependency/d2/discard", { revision: 4 }],
    ]),
  );
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
  await rowAction(userEvent, "Ui-kit", "Remove", depends);
  expect(writes(calls)).toEqual([]);
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove" }));
  await waitFor(() => expect(writes(calls)).toEqual([["DELETE /api/code-dependencies/d1?revision=4", null]]));
});

test("Dependencies explains both lists", async () => {
  api();
  renderCodeProjects("/code-projects/c1/dependencies");
  expect(await screen.findByText("Code projects this one uses as libraries.")).toBeInTheDocument();
  expect(
    screen.getByText(
      "Code projects that use this one, and the services built from them: they are affected when it changes.",
    ),
  ).toBeInTheDocument();
});
