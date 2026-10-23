import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import StorageOverviewView from "../StorageOverviewView";

const locations = [
  { id: "data", container_path: "/work/data", host_path: "/opt/ka", variable: "DATA_ROOT", status: "available" },
  {
    id: "code",
    container_path: "/work/data/code",
    host_path: "/opt/ka/code",
    variable: "CODE_FOLDER",
    status: "available",
  },
  { id: "bootstrap", container_path: "/work/data/docs", host_path: null, variable: "DOCS_FOLDER", status: "available" },
];
const entry = (name: string, path: string, kind = "folder") => ({
  name,
  path,
  kind,
  size: kind === "file" ? 42 : null,
  modified_at: "2026-09-14T10:00:00Z",
});

function mockStorage() {
  const fetchMock = vi.fn(async (input: string) => {
    const url = new URL(input, "http://test");
    let body: unknown = { path: "", total: 0, next_offset: null, entries: [] };
    if (url.pathname === "/api/storage") body = { locations };
    if (url.pathname === "/api/storage/code/entries")
      body =
        url.searchParams.get("path") === "billing"
          ? { path: "billing", total: 1, next_offset: null, entries: [entry("README.md", "billing/README.md", "file")] }
          : { path: "", total: 1, next_offset: null, entries: [entry("billing", "billing")] };
    if (url.pathname === "/api/storage/code/file")
      body = { path: "billing/README.md", text: "# Billing\n<script>not executable</script>" };
    return new Response(JSON.stringify(body));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

test("lists DATA_ROOT and its folders with host paths and .env variables", async () => {
  mockStorage();
  render(
    <MemoryRouter>
      <StorageOverviewView />
    </MemoryRouter>,
  );
  expect(await screen.findByText("/opt/ka")).toBeInTheDocument();
  expect(screen.getByText("Host · DATA_ROOT")).toBeInTheDocument();
  expect(screen.getByText("/opt/ka/code")).toBeInTheDocument();
  expect(screen.getByText("Host · CODE_FOLDER")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Browse Data folder" })).toHaveAttribute(
    "href",
    "/storage?area=data&path=&offset=0#files",
  );
  expect(screen.queryByText(/RAG|Jira export|document export/i)).toBeNull();
  expect(screen.queryByText(/rag-data|Relative to the Compose file directory/)).toBeNull();
  expect(screen.getByText(/PostgreSQL stores managed documents and versions/)).toBeInTheDocument();
  expect(screen.getByText(/Documents seeded on a fresh install/)).toBeInTheDocument();
  expect(screen.queryByText(/Upload selected files|Legacy startup files|original startup directory/)).toBeNull();
});

test("opens nested folders and previews text, then switches storage roots without keeping stale entries", async () => {
  const fetchMock = mockStorage();
  render(
    <MemoryRouter>
      <StorageOverviewView />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByRole("link", { name: "billing" }));
  fireEvent.click(await screen.findByRole("button", { name: "README.md" }));
  const preview = await screen.findByRole("dialog", { name: "billing/README.md" });
  expect(await within(preview).findByText(/# Billing/)).toBeInTheDocument();
  expect(preview.querySelector("script")).toBeNull();
  fireEvent.click(within(preview).getByRole("button", { name: "Close" }));
  fireEvent.click(screen.getByRole("link", { name: "Browse Bootstrap docs" }));
  expect(await screen.findByText("This folder is empty")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "README.md" })).toBeNull();
  expect(fetchMock.mock.calls.some(([url]) => url.includes("/bootstrap/entries?path=&offset=0"))).toBe(true);
});

test("opens a directory from a deep link and keeps the overview usable when that directory is unavailable", async () => {
  const fetchMock = mockStorage();
  fetchMock.mockImplementation(
    async (input) =>
      new Response(
        JSON.stringify(input === "/api/storage" ? { locations } : { error: { message: "Folder not found." } }),
        { status: input === "/api/storage" ? 200 : 404 },
      ),
  );
  render(
    <MemoryRouter initialEntries={["/storage?area=bootstrap&path=old%2Fv1"]}>
      <StorageOverviewView />
    </MemoryRouter>,
  );
  expect(await screen.findByText("Folder not found.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "old" })).toHaveAttribute(
    "href",
    "/storage?area=bootstrap&path=old&offset=0#files",
  );
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => url === "/api/storage").length).toBe(2));
});
