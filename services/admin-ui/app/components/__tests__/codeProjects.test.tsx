import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { CodeProject } from "~/features/codeProjects/api";
import { renderCodeProjects, sectionLink } from "~/lib/__tests__/codeProjectRoutes";

const project: CodeProject = {
  id: "code-1",
  name: "Payments",
  description: "Payment sources",
  remote_url: "https://github.com/example/payments.git",
  requested_ref: null,
  clone_path: "payments",
  folder_paths: ["payments"],
  current_commit: "a".repeat(40),
  checkout_path: "payments",
  has_token: true,
  synced_at: null,
  sync_status: null,
  sync_error: null,
  sync_interval_minutes: null,
  next_sync_at: null,
  revision: 3,
};

function api(projects: CodeProject[] = []) {
  const fetch = vi.fn(async (raw: string, init?: RequestInit) => {
    const url = new URL(raw, "http://localhost");
    let body: unknown;
    if (url.pathname.endsWith("/archive")) body = { id: "code-1", deleted: [], errors: [] };
    else if (init?.method === "POST" || init?.method === "PUT") body = { id: "code-1", job_id: "job-1" };
    else if (url.pathname === "/api/code-projects") body = { projects };
    else if (url.pathname.startsWith("/api/code-projects/")) {
      const found = projects.find((item) => url.pathname === `/api/code-projects/${item.id}`);
      if (!found)
        return new Response(JSON.stringify({ error: { message: "Code project not found" } }), { status: 404 });
      body = { ...found, built_into: [], sync_jobs: [] };
    } else if (url.pathname === "/api/sessions/options") body = { discovery_prompts: [] };
    else if (url.pathname === "/api/sessions") body = { sessions: [] };
    else if (url.pathname === "/api/code/file")
      body = { path: url.searchParams.get("path"), text: 'print("source code")' };
    else {
      const path = url.searchParams.get("path") ?? "";
      body = {
        root: "/work/code",
        host_path: "/host/code",
        path,
        total: 2,
        next_offset: null,
        entries: path
          ? [{ name: "app.py", path: `${path}/app.py`, kind: "file", size: 20 }]
          : [
              { name: "api", path: "api", kind: "folder", size: 0 },
              { name: "shared", path: "shared", kind: "folder", size: 0 },
              { name: "escape", path: "escape", kind: "symlink", size: 0 },
            ],
      };
      if (url.searchParams.get("folders_only") === "true") {
        const children =
          path === ""
            ? ["api", "shared", "payments"]
            : path === "api"
              ? ["src"]
              : path === "api/src"
                ? ["billing"]
                : [];
        body = {
          root: "/work/code",
          host_path: "/host/code",
          path,
          total: children.length,
          next_offset: null,
          entries: children.map((name) => ({ name, path: path ? `${path}/${name}` : name, kind: "folder", size: 0 })),
        };
      }
    }
    return new Response(JSON.stringify(body), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

afterEach(() => vi.unstubAllGlobals());

/** A code projects page at `path` (the list by default). */
const show = (path = "/code-projects") => renderCodeProjects(path);
/** The New project sheet, opened from the list. */
async function newProject() {
  await userEvent.setup().click(await screen.findByRole("button", { name: "New project" }));
  return within(await screen.findByRole("dialog", { name: "New code project" }));
}
/** The project's Settings page. */
const settings = async () => {
  show("/code-projects/code-1/settings");
  return screen.findByLabelText("Project name");
};

const local: CodeProject = {
  ...project,
  id: "code-2",
  name: "Billing",
  remote_url: null,
  clone_path: null,
  current_commit: null,
  checkout_path: null,
  folder_paths: ["billing"],
  has_token: false,
};
const rows = () => screen.getAllByRole("row").slice(1);
const names = () => rows().map((row) => within(row).getAllByRole("cell")[0].textContent);

test("the list has Projects and Code folder sub-pages; folder is not taken as a project id", async () => {
  const user = userEvent.setup();
  const fetch = api([project]);
  const { router } = show();
  expect(await screen.findByRole("link", { name: "Payments" })).toHaveAttribute("href", "/code-projects/code-1");
  expect(sectionLink("Code projects sections", "Projects")).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("button", { name: "New project" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Code root" })).toBeNull(); // no folders on the list
  await user.click(sectionLink("Code projects sections", "Code folder"));
  expect(router.state.location.pathname).toBe("/code-projects/folder");
  expect(await screen.findByText("/host/code → /work/code")).toBeInTheDocument();
  expect(sectionLink("Code projects sections", "Code folder")).toHaveAttribute("aria-current", "page");
  expect(screen.queryByRole("button", { name: "New project" })).toBeNull();
  expect(fetch.mock.calls.some(([url]) => String(url).includes("/api/code-projects/folder"))).toBe(false);
});

test("the Projects table shows source, ref, commit, sync and code graph status and the Built into count", async () => {
  api([
    {
      ...project,
      requested_ref: "main",
      synced_at: "2026-10-20T10:00:00Z",
      sync_status: "succeeded",
      built_into_count: 2,
      code_index: {
        status: "ready",
        source_hash: "h",
        source_file_count: 1,
        engine_version: "1",
        stats: { nodeCount: 1, edgeCount: 0, fileCount: 1, filesByLanguage: {} },
        unresolved_references: 0,
        parse_error_files: [],
        missing_folders: [],
        processed_at: "2026-10-07T10:00:00Z",
      },
    },
    local,
  ]);
  show();
  await screen.findByRole("link", { name: "Payments" });
  const headers = screen.getAllByRole("columnheader").map((cell) => cell.textContent);
  expect(headers).toEqual(["Name", "Source", "Ref", "Commit", "Sync status", "Code graph", "Built into", "Actions"]);
  const payments = within(rows()[1]);
  expect(payments.getByText(project.remote_url!)).toBeInTheDocument();
  expect(payments.getByText("main")).toBeInTheDocument();
  expect(payments.getByText("aaaaaaaaaaaa")).toBeInTheDocument();
  expect(payments.getByText("succeeded")).toBeInTheDocument();
  expect(payments.getByText("ready")).toBeInTheDocument();
  expect(payments.getByText("2")).toBeInTheDocument();
  const billing = within(rows()[0]);
  expect(billing.getByText("local folder")).toBeInTheDocument();
  expect(billing.getByText("Local folders")).toBeInTheDocument();
  expect(billing.getByText("not indexed")).toBeInTheDocument();
  expect(billing.getByText("0")).toBeInTheDocument();
  expect(billing.queryByRole("button", { name: "Sync Billing now" })).toBeNull(); // nothing to pull
});

test("the table searches name and source, filters by statuses and sorts by header click", async () => {
  const user = userEvent.setup();
  const failed = { ...project, id: "code-3", name: "Admin", remote_url: "https://git.example/admin.git" };
  api([
    { ...project, sync_status: "succeeded", synced_at: "2026-10-20T10:00:00Z" },
    local,
    { ...failed, sync_status: "failed", synced_at: "2026-10-21T10:00:00Z" },
  ]);
  show();
  await screen.findByRole("link", { name: "Payments" });
  expect(names()).toEqual(["Admin", "Billing", "Payments"]);
  await user.click(screen.getByRole("button", { name: "Name" }));
  expect(names()).toEqual(["Payments", "Billing", "Admin"]);
  await user.click(screen.getByRole("button", { name: "Commit" }));
  expect(names()).toEqual(["Billing", "Payments", "Admin"]); // never synced first, then oldest
  await user.click(screen.getByRole("button", { name: "Sync status" }));
  expect(names()[0]).toBe("Admin"); // failed
  await user.type(screen.getByRole("searchbox", { name: "Search code projects" }), "git.example");
  expect(names()).toEqual(["Admin"]);
  await user.clear(screen.getByRole("searchbox", { name: "Search code projects" }));
  await user.type(screen.getByRole("searchbox", { name: "Search code projects" }), "bill");
  expect(names()).toEqual(["Billing"]);
  await user.clear(screen.getByRole("searchbox", { name: "Search code projects" }));
  await user.click(screen.getByRole("combobox", { name: "Sync status" }));
  await user.click(await screen.findByRole("option", { name: "failed" }));
  expect(names()).toEqual(["Admin"]);
  await user.click(screen.getByRole("combobox", { name: "Sync status" }));
  await user.click(await screen.findByRole("option", { name: "All sync statuses" }));
  await user.click(screen.getByRole("combobox", { name: "Code graph" }));
  await user.click(await screen.findByRole("option", { name: "not indexed" }));
  expect(names()).toHaveLength(3);
});

test("the table shows 20 projects per page", async () => {
  const user = userEvent.setup();
  api(
    Array.from({ length: 25 }, (_, n) => ({
      ...project,
      id: `p${n}`,
      name: `Project ${String(n + 1).padStart(2, "0")}`,
    })),
  );
  show();
  await screen.findByRole("link", { name: "Project 01" });
  expect(rows()).toHaveLength(20);
  expect(screen.getByText("Showing 1–20 of 25")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Next page" }));
  expect(names()).toEqual(["Project 21", "Project 22", "Project 23", "Project 24", "Project 25"]);
  await user.type(screen.getByRole("searchbox", { name: "Search code projects" }), "Project 0");
  expect(rows()).toHaveLength(9); // a search starts again at page 1
});

test("Sync now queues a pull from the row", async () => {
  const user = userEvent.setup();
  const fetch = api([project]);
  show();
  await user.click(await screen.findByRole("button", { name: "Sync Payments now" }));
  await screen.findByText("Git pull queued.");
  expect(
    fetch.mock.calls.some(([url, init]) => url === "/api/code-projects/code-1/pull" && init?.method === "POST"),
  ).toBe(true);
});

test("Delete asks for confirmation, keeps files by default and drops the project from the list", async () => {
  const user = userEvent.setup();
  const fetch = api([project]);
  show();
  await user.click(await screen.findByRole("button", { name: "Delete Payments" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent("code/payments");
  const box = screen.getByRole("checkbox", { name: /Also delete these folders and the code graph/ });
  expect(box).not.toBeChecked();
  expect(fetch.mock.calls.some(([url]) => String(url).includes("/archive"))).toBe(false);
  await user.click(screen.getByRole("button", { name: "Remove project" }));
  await screen.findByText("Payments removed. Files on disk were kept.");
  const call = fetch.mock.calls.find(([url]) => String(url).endsWith("/api/code-projects/code-1/archive"))!;
  expect(call[1]?.method).toBe("POST");
  expect(JSON.parse(call[1]!.body as string)).toEqual({ delete_files: false });
});

test("Delete with the delete box sends delete_files and reports what was deleted or left behind", async () => {
  const user = userEvent.setup();
  const fetch = api([project]);
  const base = fetch.getMockImplementation()!;
  fetch.mockImplementation(async (raw: string, init?: RequestInit) => {
    if (new URL(raw, "http://localhost").pathname.endsWith("/archive"))
      return new Response(
        JSON.stringify({
          id: "code-1",
          deleted: ["code/payments"],
          errors: ["code-index/code-1 could not be deleted: Permission denied"],
        }),
        { status: 200 },
      );
    return base(raw, init);
  });
  show();
  await user.click(await screen.findByRole("button", { name: "Delete Payments" }));
  await user.click(screen.getByRole("checkbox", { name: /Also delete these folders and the code graph/ }));
  await user.click(screen.getByRole("button", { name: "Remove project" }));
  await screen.findByText("Payments removed. Deleted code/payments.");
  expect(screen.getByText(/code-index\/code-1 could not be deleted: Permission denied/)).toBeInTheDocument();
  const call = fetch.mock.calls.find(([url]) => String(url).endsWith("/archive"))!;
  expect(JSON.parse(call[1]!.body as string)).toEqual({ delete_files: true });
});

test("a refused Delete shows the reason and keeps the project", async () => {
  const user = userEvent.setup();
  const fetch = api([project]);
  const base = fetch.getMockImplementation()!;
  fetch.mockImplementation(async (raw: string, init?: RequestInit) => {
    if (new URL(raw, "http://localhost").pathname.endsWith("/archive"))
      return new Response(
        JSON.stringify({
          error: {
            message:
              "code/payments is also used by code project Billing (code/payments). Nothing was removed; keep the files or unmap it there first.",
          },
        }),
        { status: 409 },
      );
    return base(raw, init);
  });
  show();
  await user.click(await screen.findByRole("button", { name: "Delete Payments" }));
  await user.click(screen.getByRole("checkbox", { name: /Also delete these folders and the code graph/ }));
  await user.click(screen.getByRole("button", { name: "Remove project" }));
  await screen.findByText(/code\/payments is also used by code project Billing/);
  expect(screen.getByRole("button", { name: "Delete Payments" })).toBeInTheDocument();
});

test("New project maps multiple existing folders into a local code project in a sheet", async () => {
  const fetch = api();
  show();
  const sheet = await newProject();
  fireEvent.change(sheet.getByLabelText("Project name"), { target: { value: "Local code" } });
  expect(sheet.getByRole("button", { name: "Save project" })).toBeDisabled();
  fireEvent.click(await sheet.findByRole("checkbox", { name: "Select folder api" }));
  fireEvent.click(sheet.getByRole("checkbox", { name: "Select folder shared" }));
  fireEvent.click(sheet.getByRole("button", { name: "Save project" }));
  await screen.findByText("Code project saved.");
  expect(screen.queryByRole("dialog", { name: "New code project" })).toBeNull();
  const body = JSON.parse(fetch.mock.calls.find(([, init]) => init?.method === "POST")![1]!.body as string);
  expect(body).toMatchObject({
    name: "Local code",
    remote_url: null,
    folder_paths: ["api", "shared"],
    clone_now: false,
  });
  expect(body).not.toHaveProperty("token");
  expect(fetch.mock.calls.filter(([url]) => url === "/api/code-projects").length).toBeGreaterThan(1); // reloaded
});

test("Git source asks for clone folder, cloning choice and automatic pull interval", async () => {
  const user = userEvent.setup();
  const fetch = api();
  show();
  await newProject();
  await user.type(screen.getByLabelText("Project name"), "Remote code");
  await user.click(screen.getByRole("combobox", { name: "Source" }));
  await user.click(screen.getByRole("option", { name: "Git repository" }));
  expect(screen.getByLabelText("Clone folder")).toBeRequired();
  expect(screen.getByLabelText("Clone folder")).toHaveValue("Remote code");
  fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "Payment API" } });
  expect(screen.getByLabelText("Clone folder")).toHaveValue("Payment API");
  expect(screen.getByRole("checkbox", { name: "Clone repository after saving" })).toBeChecked();
  fireEvent.change(screen.getByLabelText("Git repository URL"), { target: { value: "example/remote" } });
  fireEvent.change(screen.getByLabelText("Clone folder"), { target: { value: "remote-code" } });
  fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "Renamed API" } });
  expect(screen.getByLabelText("Clone folder")).toHaveValue("remote-code");
  await user.click(screen.getByRole("switch", { name: "Automatic pulls" }));
  fireEvent.change(screen.getByLabelText("Pull interval (minutes)"), { target: { value: "15" } });
  await user.click(screen.getByRole("button", { name: "Save and clone" }));
  await screen.findByText("Code project saved. Clone queued.");
  const body = JSON.parse(fetch.mock.calls.find(([, init]) => init?.method === "POST")![1]!.body as string);
  expect(body).toMatchObject({
    remote_url: "example/remote",
    clone_path: "remote-code",
    clone_now: true,
    sync_interval_minutes: 15,
  });
});

test("can save a Git project without cloning it", async () => {
  const user = userEvent.setup();
  const fetch = api();
  show();
  await newProject();
  fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "Later" } });
  await user.click(screen.getByRole("combobox", { name: "Source" }));
  await user.click(screen.getByRole("option", { name: "Git repository" }));
  fireEvent.change(screen.getByLabelText("Git repository URL"), { target: { value: "example/later" } });
  fireEvent.change(screen.getByLabelText("Clone folder"), { target: { value: "later" } });
  await user.click(screen.getByRole("checkbox", { name: "Clone repository after saving" }));
  await user.click(screen.getByRole("button", { name: "Save project" }));
  await screen.findByText("Code project saved.");
  const body = JSON.parse(fetch.mock.calls.find(([, init]) => init?.method === "POST")![1]!.body as string);
  expect(body.clone_now).toBe(false);
  expect(fetch.mock.calls.filter(([url]) => url.endsWith("/pull"))).toHaveLength(0);
});

test("new private repositories require credentials and submit them separately from the URL", async () => {
  const user = userEvent.setup();
  const fetch = api();
  show();
  await newProject();
  fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "Private code" } });
  await user.click(screen.getByRole("combobox", { name: "Source" }));
  await user.click(screen.getByRole("option", { name: "Git repository" }));
  fireEvent.change(screen.getByLabelText("Git repository URL"), { target: { value: project.remote_url } });
  expect(screen.queryByLabelText("Access token")).not.toBeInTheDocument();
  await user.click(screen.getByRole("switch", { name: "Private repository" }));
  for (const label of ["Git username", "Access token"]) {
    expect(screen.getByLabelText(label)).toBeRequired();
    expect(screen.getByLabelText(label)).toHaveAttribute("type", "password");
  }
  await user.click(screen.getByRole("button", { name: "Save and clone" }));
  expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  fireEvent.change(screen.getByLabelText("Git username"), { target: { value: "private-user" } });
  fireEvent.change(screen.getByLabelText("Access token"), { target: { value: "private-token" } });
  await user.click(screen.getByRole("button", { name: "Save and clone" }));
  await screen.findByText("Code project saved. Clone queued.");
  const body = JSON.parse(fetch.mock.calls.find(([, init]) => init?.method === "POST")![1]!.body as string);
  expect(body).toMatchObject({
    remote_url: project.remote_url,
    username: "private-user",
    token: "private-token",
    clone_now: true,
  });
  expect(screen.queryByLabelText("Access token")).not.toBeInTheDocument();
});

test("selects nested folders across branches and keeps selections when collapsed and reopened", async () => {
  const user = userEvent.setup();
  const fetch = api();
  show();
  await newProject();
  await user.type(screen.getByLabelText("Project name"), "Billing");
  await screen.findByRole("checkbox", { name: "Select folder api" });
  expect(fetch.mock.calls.some(([url]) => url.includes("path=api&"))).toBe(false);
  await user.click(screen.getByRole("button", { name: "Expand api" }));
  await user.click(await screen.findByRole("button", { name: "Expand api/src" }));
  await user.click(await screen.findByRole("checkbox", { name: "Select folder api/src/billing" }));
  await user.click(screen.getByRole("checkbox", { name: "Select folder shared" }));
  await user.click(screen.getByRole("button", { name: "Collapse api" }));
  expect(screen.queryByRole("checkbox", { name: "Select folder api/src/billing" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Unmap api/src/billing" })).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Expand api" }));
  expect(await screen.findByRole("checkbox", { name: "Select folder api/src/billing" })).toBeChecked();
  await user.click(screen.getByRole("button", { name: "Save project" }));
  await screen.findByText("Code project saved.");
  const body = JSON.parse(fetch.mock.calls.find(([, init]) => init?.method === "POST")![1]!.body as string);
  expect(body.folder_paths).toEqual(["api/src/billing", "shared"]);
});

test("the Code folder browses folders and previews file contents without following symlinks", async () => {
  const fetch = api();
  show("/code-projects/folder");
  expect(await screen.findByRole("button", { name: "escape" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "api" }));
  fireEvent.click(await screen.findByRole("button", { name: "app.py" }));
  expect(await screen.findByLabelText("Code file content")).toHaveTextContent('print("source code")');
  expect(fetch.mock.calls.some(([url]) => url === "/api/code/file?path=api%2Fapp.py")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Code root" }));
  expect(await screen.findByRole("button", { name: "shared" })).toBeVisible();
  expect(screen.queryByLabelText("Code file content")).not.toBeInTheDocument();
});

test("Settings edits in place: preserves hidden credentials unless removal is explicit and sends the revision", async () => {
  const user = userEvent.setup();
  const fetch = api([project]);
  await settings();
  expect(screen.getByLabelText("Git repository URL")).toBeDisabled();
  expect(screen.getByLabelText("Clone folder")).toBeDisabled();
  expect(screen.getByRole("switch", { name: "Private repository" })).toBeChecked();
  for (const label of ["Git username", "Access token"]) {
    expect(screen.getByLabelText(label)).toHaveAttribute("type", "password");
    expect(screen.getByLabelText(label)).toHaveValue("");
    expect(screen.getByLabelText(label)).not.toBeRequired();
  }
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled(); // nothing changed yet
  await user.type(screen.getByLabelText("Branch, tag, or commit (optional)"), "main");
  await user.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("Code project saved.");
  let bodies = fetch.mock.calls
    .filter(([, init]) => init?.method === "PUT")
    .map(([, init]) => JSON.parse(init!.body as string));
  expect(bodies[0]).toMatchObject({ revision: 3, clone_now: false, folder_paths: ["payments"], requested_ref: "main" });
  expect(bodies[0]).not.toHaveProperty("token");
  expect(bodies[0]).not.toHaveProperty("username");
  await user.click(await screen.findByRole("switch", { name: "Private repository" }));
  expect(screen.queryByLabelText("Access token")).not.toBeInTheDocument();
  expect(screen.getByText(/Saving will remove the saved username and token/)).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(fetch.mock.calls.filter(([, init]) => init?.method === "PUT")).toHaveLength(2));
  bodies = fetch.mock.calls
    .filter(([, init]) => init?.method === "PUT")
    .map(([, init]) => JSON.parse(init!.body as string));
  expect(bodies[1].token).toBeNull();
  expect(bodies[1]).not.toHaveProperty("username");
});

test("Settings: replacing only the token does not send a blank username", async () => {
  const fetch = api([project]);
  await settings();
  fireEvent.change(screen.getByLabelText("Access token"), { target: { value: "new-private-token" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("Code project saved.");
  const body = JSON.parse(fetch.mock.calls.find(([, init]) => init?.method === "PUT")![1]!.body as string);
  expect(body.token).toBe("new-private-token");
  expect(body).not.toHaveProperty("username");
});

test("Settings expands selected folder ancestors and allows removing a nested mapping", async () => {
  const fetch = api([{ ...local, id: "code-1", folder_paths: ["api/src/billing", "shared"] }]);
  await settings();
  expect(await screen.findByRole("checkbox", { name: "Select folder api/src/billing" })).toBeChecked();
  fireEvent.click(screen.getByRole("checkbox", { name: "Select folder api/src/billing" }));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("Code project saved.");
  const body = JSON.parse(fetch.mock.calls.find(([, init]) => init?.method === "PUT")![1]!.body as string);
  expect(body.folder_paths).toEqual(["shared"]);
  expect(body.revision).toBe(3);
});

test("Settings: Discard drops the changes, and leaving with unsaved changes asks first", async () => {
  const user = userEvent.setup();
  api([project]);
  const { router } = show("/code-projects/code-1/settings");
  const name = await screen.findByLabelText("Project name");
  await user.clear(name);
  await user.type(name, "Renamed");
  await user.click(screen.getByRole("button", { name: "Discard" }));
  expect(screen.getByLabelText("Project name")).toHaveValue("Payments");
  expect(screen.getByRole("button", { name: "Discard" })).toBeDisabled();
  await user.type(screen.getByLabelText("Project name"), " API");
  await user.click(sectionLink("Code project sections", "Overview"));
  const dialog = within(await screen.findByRole("alertdialog", { name: "Unsaved changes" }));
  await user.click(dialog.getByRole("button", { name: "Stay" }));
  expect(router.state.location.pathname).toBe("/code-projects/code-1/settings");
  expect(screen.getByLabelText("Project name")).toHaveValue("Payments API");
  await user.click(sectionLink("Code project sections", "Overview"));
  await user.click(
    within(await screen.findByRole("alertdialog", { name: "Unsaved changes" })).getByRole("button", { name: "Leave" }),
  );
  await waitFor(() => expect(router.state.location.pathname).toBe("/code-projects/code-1/overview"));
});

test("code projects have no Searchable switch", async () => {
  api([project]);
  show();
  expect((await screen.findAllByText("Payments")).length).toBeGreaterThan(0);
  expect(screen.queryByRole("switch", { name: /Searchable/ })).toBeNull();
});

test("Settings: Save waits while a sync or code graph job runs", async () => {
  const user = userEvent.setup();
  api([{ ...project, sync_status: "running" }]);
  await settings();
  await user.type(screen.getByLabelText("Branch, tag, or commit (optional)"), "main");
  expect(
    screen.getByText("A sync or code graph job is running; settings can be saved when it finishes."),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Discard" })).toBeEnabled();
});

test("the table goes back a page when its last page empties", async () => {
  const user = userEvent.setup();
  const projects = Array.from({ length: 21 }, (_, n) => ({
    ...project,
    id: `p${n}`,
    name: `Project ${String(n + 1).padStart(2, "0")}`,
  }));
  const fetch = api(projects);
  const base = fetch.getMockImplementation()!;
  fetch.mockImplementation(async (raw: string, init?: RequestInit) => {
    if (String(raw).endsWith("/archive")) projects.pop();
    return base(raw, init);
  });
  show();
  await screen.findByRole("link", { name: "Project 01" });
  await user.click(screen.getByRole("button", { name: "Next page" }));
  await user.click(screen.getByRole("button", { name: "Delete Project 21" }));
  await user.click(await screen.findByRole("button", { name: "Remove project" }));
  await screen.findByText("Project 21 removed. Files on disk were kept.");
  await waitFor(() => expect(rows()).toHaveLength(20));
  expect(screen.getByText("Page 1 of 1")).toBeInTheDocument();
});
