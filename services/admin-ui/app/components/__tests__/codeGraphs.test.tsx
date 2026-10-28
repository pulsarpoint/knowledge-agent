import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import CodeGraphsView from "~/features/codeProjects/CodeGraphsView";
import type { CodeProject } from "~/features/codeProjects/api";
import { Toaster } from "~/components/ui/sonner";
import { menuItems, rowAction, rowButtons } from "~/lib/__tests__/rowMenu";
import { expectOneToolbarRow } from "~/lib/__tests__/toolbarRow";

vi.mock("~/features/codeProjects/CodeGraphExplorer", () => ({
  default: ({ projectId, sourceHash }: { projectId: string; sourceHash: string }) => (
    <div>
      Explorer {projectId} {sourceHash}
    </div>
  ),
}));

const base = {
  description: "",
  remote_url: null,
  requested_ref: null,
  clone_path: null,
  current_commit: null,
  checkout_path: null,
  has_token: false,
  synced_at: null,
  sync_status: null,
  sync_error: null,
  sync_interval_minutes: null,
  next_sync_at: null,
  revision: 1,
};
const indexed: CodeProject = {
  ...base,
  id: "p1",
  name: "Payments",
  folder_paths: ["payments"],
  processing: { status: "succeeded", error: null },
  code_index: {
    status: "ready",
    source_hash: "h1",
    source_file_count: 12,
    engine_version: "1.6.0",
    stats: { nodeCount: 340, edgeCount: 910, fileCount: 12, filesByLanguage: { python: 10, typescript: 2 } },
    unresolved_references: 3,
    parse_error_files: ["bad.py"],
    missing_folders: [],
    processed_at: "2026-10-07T10:00:00Z",
  },
};
const missing: CodeProject = {
  ...base,
  id: "p2",
  name: "Billing",
  folder_paths: ["billing"],
  processing: null,
  code_index: null,
};
const running: CodeProject = {
  ...base,
  id: "p3",
  name: "Ledger",
  folder_paths: ["ledger"],
  processing: { status: "running", error: null },
  code_index: null,
};

function api(projects: CodeProject[]) {
  const fetch = vi.fn(async (raw: string, init?: RequestInit) => {
    const url = new URL(raw, "http://localhost");
    if (init?.method === "POST" && url.pathname.endsWith("/process"))
      return new Response(JSON.stringify({ job_id: "job-1" }), { status: 202 });
    return new Response(JSON.stringify({ projects }), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

test("lists every project with its graph summary and opens a graph", async () => {
  const user = userEvent.setup();
  api([indexed, missing, running]);
  render(
    <MemoryRouter>
      <CodeGraphsView />
      <Toaster />
    </MemoryRouter>,
  );
  const row = (await screen.findByRole("cell", { name: "Payments" })).closest("tr")!;
  expect(row).toHaveTextContent("ready");
  expect(row).toHaveTextContent("12");
  expect(row).toHaveTextContent("340");
  expect(row).toHaveTextContent("910");
  expect(row).toHaveTextContent("python, typescript");
  expect(row).toHaveTextContent("1 parse error");
  const billing = screen.getByRole("cell", { name: "Billing" }).closest("tr")!;
  expect(billing).toHaveTextContent("Not generated");
  expect(within(billing).queryByRole("button", { name: "View graph for Billing" })).toBeNull();
  const ledger = screen.getByRole("cell", { name: "Ledger" }).closest("tr")!;
  expect(ledger).toHaveTextContent("running");
  // Without a graph the row's one action is Generate graph (disabled while generating), and there is no menu
  expect(rowButtons(ledger)).toEqual(["Generate graph for Ledger"]);
  expect(within(ledger).getByRole("button", { name: "Generate graph for Ledger" })).toBeDisabled();
  expect(rowButtons(billing)).toEqual(["Generate graph for Billing"]);
  // With a graph it is View graph inline and Regenerate graph in the menu
  expect(rowButtons(row)).toEqual(["View graph for Payments", "Actions for Payments"]);
  await user.click(within(row).getByRole("button", { name: "Actions for Payments" }));
  expect(menuItems()).toEqual(["Regenerate graph"]);
  await user.keyboard("{Escape}");
  await user.click(within(row).getByRole("button", { name: "View graph for Payments" }));
  // the explorer opens in a side sheet over the list, not below it
  const sheet = await screen.findByRole("dialog", { name: "Payments · Code graph" });
  expect(within(sheet).getByText("Explorer p1 h1")).toBeInTheDocument();
  await user.click(within(sheet).getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.queryByText("Explorer p1 h1")).toBeNull();
});

test("Regenerate graph is in an indexed row's menu: it queues processing; while generating it is off with a hint", async () => {
  const user = userEvent.setup();
  const fetch = api([indexed, { ...indexed, id: "p4", name: "Shop", processing: { status: "running", error: null } }]);
  render(
    <MemoryRouter>
      <CodeGraphsView />
      <Toaster />
    </MemoryRouter>,
  );
  await user.click(await screen.findByRole("button", { name: "Actions for Shop" }));
  const busy = screen.getByRole("menuitem", { name: "Regenerate graph" });
  expect(busy).toHaveAttribute("aria-disabled", "true");
  expect(busy).toHaveAccessibleDescription("Generating…");
  await user.keyboard("{Escape}");
  await rowAction(user, "Payments", "Regenerate graph");
  await screen.findByText("Code graph generation queued for Payments.");
  expect(
    fetch.mock.calls.some(
      ([url, init]) => String(url).endsWith("/api/code-projects/p1/process") && init?.method === "POST",
    ),
  ).toBe(true);
});

test("Generate graph queues processing and reloads", async () => {
  const user = userEvent.setup();
  const fetch = api([missing]);
  render(
    <MemoryRouter>
      <CodeGraphsView />
      <Toaster />
    </MemoryRouter>,
  );
  await user.click(await screen.findByRole("button", { name: "Generate graph for Billing" }));
  await screen.findByText("Code graph generation queued for Billing.");
  expect(
    fetch.mock.calls.some(
      ([url, init]) => String(url).endsWith("/api/code-projects/p2/process") && init?.method === "POST",
    ),
  ).toBe(true);
  expect(fetch.mock.calls.filter(([url]) => String(url).endsWith("/api/code-projects")).length).toBeGreaterThan(1);
});

test("empty state points to Code projects", async () => {
  api([]);
  render(
    <MemoryRouter>
      <CodeGraphsView />
      <Toaster />
    </MemoryRouter>,
  );
  expect(await screen.findByText("No code projects yet")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Code projects" })).toHaveAttribute("href", "/code-projects");
});

test("an old link with the project in the URL (?project=) opens its graph in the sheet", async () => {
  api([indexed]);
  render(
    <MemoryRouter initialEntries={["/code-graphs?project=p1"]}>
      <CodeGraphsView />
      <Toaster />
    </MemoryRouter>,
  );
  const sheet = await screen.findByRole("dialog", { name: "Payments · Code graph" });
  expect(within(sheet).getByText("Explorer p1 h1")).toBeInTheDocument();
});

test("the code graph list: Skeleton rows while loading, search and sort by a header", async () => {
  const user = userEvent.setup();
  api([indexed, missing, running]);
  render(
    <MemoryRouter>
      <CodeGraphsView />
    </MemoryRouter>,
  );
  expect(screen.getAllByTestId("skeleton-row").length).toBeGreaterThan(0);
  await screen.findByRole("cell", { name: "Payments" });
  expectOneToolbarRow(
    screen.getByRole("searchbox", { name: "Search code graphs" }),
    screen.getByRole("button", { name: "Refresh" }),
  );
  const names = () =>
    screen
      .getAllByRole("row")
      .slice(1)
      .map((row) => within(row).getAllByRole("cell")[0].textContent);
  expect(names()).toEqual(["Billing", "Ledger", "Payments"]);
  await user.click(screen.getByRole("button", { name: "Code project" }));
  expect(names()).toEqual(["Payments", "Ledger", "Billing"]);
  await user.type(screen.getByRole("searchbox", { name: "Search code graphs" }), "led");
  expect(names()).toEqual(["Ledger"]);
});
