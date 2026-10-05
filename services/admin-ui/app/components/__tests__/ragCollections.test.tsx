import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import RagCollectionsView from "../RagCollectionsView";
import RagCollectionDetail from "../RagCollectionDetail";
import type { CollectionPath, RagCollection } from "../../lib/rag";

const jiraPath: CollectionPath = {
  id: "p1", collection_id: "c1", source_path: "rag-data/jira", owner_kind: "jira", owner_id: null, owner_key: null,
  created_at: "2026-10-05T09:00:00Z", files: 75, host_path: "/opt/ka/rag-data/jira", container_path: "/work/rag-data/jira", present: true,
};
const codePath: CollectionPath = { ...jiraPath, id: "p2", source_path: "code/payments", owner_kind: "code_project", owner_id: "proj-1",
  owner_key: "payments", files: 12, host_path: "/opt/ka/code/payments", container_path: "/work/code/payments" };
const notesPath: CollectionPath = { ...jiraPath, id: "p3", source_path: "rag-data/notes", owner_kind: null, files: 3,
  host_path: "/opt/ka/rag-data/notes", container_path: "/work/rag-data/notes" };
const base: RagCollection = {
  id: "c1", name: "jira", description: "Jira tickets", search_mode: "full_text", embeddings_enabled: true, include: ["**/*.md"],
  exclude: [], chunk_size: 2000, chunk_overlap: 200, revision: 2, synced_revision: 2, last_synced_at: "2026-10-05T09:00:00Z",
  paths: [codePath, jiraPath, notesPath], folder_present: true, missing_paths: [], folder_changed_at: "2026-10-05T10:00:00Z",
  file_count: 90, truncated: false, skipped: { links: 0, special: 0, unreadable: 0, invalid_names: 0 },
  indexed_files: 90, chunks: 975, embedded_chunks: 0, needs_refresh: true, status: "needs_refresh", job: null,
};
const unowned: RagCollection = { ...base, id: "c2", name: "notes", paths: [notesPath] };
const projects = { projects: [{ id: "proj-1", name: "Payments API" }] };
const noFiles = { files: [], truncated: false, skipped: { links: 0, special: 0, unreadable: 0, invalid_names: 0 }, missing_paths: [] };

test("lists collections with their paths and sources, without New collection", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/code-projects") return Response.json(projects);
    if (init?.method === "POST") return Response.json({ id: "job" }, { status: 202 });
    return Response.json({ collections: [base, { ...unowned, status: "up_to_date", needs_refresh: false }] });
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<MemoryRouter><RagCollectionsView /></MemoryRouter>);
  const row = (await screen.findByRole("link", { name: "jira" })).closest("tr")!;
  expect(within(row).getByText("/opt/ka/rag-data/jira")).toBeInTheDocument();
  expect(within(row).getByText("Jira")).toBeInTheDocument();
  expect(await within(row).findByRole("link", { name: "Code project Payments API" })).toHaveAttribute("href", "/code-projects");
  expect(within(row).getAllByText("Unowned")).toHaveLength(1);
  expect(within(row).getByText("75 files")).toBeInTheDocument();
  expect(within(row).getByText("0 / 975")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "New collection" })).toBeNull();
  const notes = screen.getByRole("link", { name: "notes" }).closest("tr")!;
  expect(within(notes).queryByRole("button", { name: "Refresh" })).toBeNull();
  await userEvent.click(within(row).getByRole("button", { name: "Refresh" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/rag/collections/c1/sync", expect.objectContaining({ method: "POST" })));
});

test("missing paths are named and failed syncs show the error", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => url === "/api/code-projects" ? Response.json(projects) : Response.json({ collections: [
    { ...base, status: "folder_missing", folder_present: false, missing_paths: ["rag-data/jira"], paths: [{ ...jiraPath, present: false }] },
    { ...unowned, status: "failed", job: { id: "j", status: "failed", error: "Select an enabled embedding model", created_at: "", finished_at: "" } }] })));
  render(<MemoryRouter><RagCollectionsView /></MemoryRouter>);
  expect(await screen.findByText("Folder missing")).toBeInTheDocument();
  expect(screen.getByText("Missing: rag-data/jira")).toBeInTheDocument();
  expect(screen.getByText(/Select an enabled embedding model/)).toBeInTheDocument();
});

test("detail lists full paths and chunks, with no upload or file editor", async () => {
  const fetchMock = vi.fn(async (url: string) => {
    if (url === "/api/code-projects") return Response.json(projects);
    if (url.includes("/chunks")) return Response.json({ chunks: [{ position: 0, text: "First chunk text", embedded: false }] });
    if (url.endsWith("/files")) return Response.json({ ...noFiles, files: [{ path: "rag-data/jira/DEMO-1.md", source: "rag-data/jira", size: 10, mtime: 1,
      indexed_at: "2026-10-05T09:00:00Z", content_hash: "abc", chunks: 13, embedded_chunks: 0, stale: false }] });
    return Response.json(base);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(<MemoryRouter><RagCollectionDetail id="c1" /></MemoryRouter>);
  const file = await screen.findByRole("button", { name: "rag-data/jira/DEMO-1.md" });
  expect(within(file.closest("tr")!).getByText("13")).toBeInTheDocument();
  await user.click(file);
  expect(await screen.findByText("First chunk text")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/api/rag/collections/c1/chunks?path=rag-data%2Fjira%2FDEMO-1.md"), expect.anything());
  expect(screen.queryByLabelText("Upload file")).toBeNull();
  expect(screen.queryByLabelText("File path")).toBeNull();
});

test("Edit changes search settings only", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/code-projects") return Response.json(projects);
    if (url.endsWith("/files")) return Response.json(noFiles);
    return Response.json(init?.method === "PUT" ? { ...base, revision: 3 } : base);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(<MemoryRouter><RagCollectionDetail id="c1" /></MemoryRouter>);
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  expect(screen.queryByLabelText("Name")).toBeNull();
  expect(screen.queryByLabelText("Folder")).toBeNull();
  await user.clear(screen.getByLabelText("Include patterns"));
  await user.type(screen.getByLabelText("Include patterns"), "**/*.py");
  await user.click(screen.getByRole("button", { name: "Save collection" }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([, i]) => (i as RequestInit)?.method === "PUT")).toBe(true));
  const put = fetchMock.mock.calls.find(([, i]) => (i as RequestInit)?.method === "PUT")!;
  expect(put[0]).toBe("/api/rag/collections/c1");
  expect(JSON.parse((put[1] as RequestInit).body as string)).toEqual({ description: "Jira tickets", search_mode: "full_text",
    embeddings_enabled: true, include: ["**/*.py"], exclude: [], chunk_size: 2000, chunk_overlap: 200, revision: 2 });
});

test("Remove appears only when no source owns a path", async () => {
  let current = base;
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/code-projects") return Response.json(projects);
    if (url.endsWith("/files")) return Response.json(noFiles);
    if (init?.method === "DELETE") return Response.json({ deleted_files: 3 });
    return Response.json(current);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  const view = render(<MemoryRouter><RagCollectionDetail id="c1" /></MemoryRouter>);
  await screen.findByRole("button", { name: "Edit" });
  expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
  expect(screen.getByText(/sources own paths in this collection/)).toBeInTheDocument();
  view.unmount();
  current = unowned;
  render(<MemoryRouter initialEntries={["/rag/collections/c2"]}><Routes>
    <Route path="/rag/collections/:id" element={<RagCollectionDetail id="c2" />} />
    <Route path="/rag/collections" element={<div>collections page</div>} /></Routes></MemoryRouter>);
  await user.click(await screen.findByRole("button", { name: "Remove" }));
  await user.click(screen.getByRole("button", { name: "Remove collection" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/rag/collections/c2?revision=2&confirmed=true", expect.objectContaining({ method: "DELETE" })));
  expect(await screen.findByText("collections page")).toBeInTheDocument();
});

test("an unowned path can be detached; owned paths cannot", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/code-projects") return Response.json(projects);
    if (url.endsWith("/files")) return Response.json(noFiles);
    if (init?.method === "DELETE") return Response.json({ removed: 1 });
    return Response.json(base);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(<MemoryRouter><RagCollectionDetail id="c1" /></MemoryRouter>);
  await user.click(await screen.findByRole("button", { name: "Detach rag-data/notes" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/rag/collections/by-name/jira/paths?source_path=rag-data%2Fnotes",
    expect.objectContaining({ method: "DELETE" })));
  expect(await screen.findByText("Path detached. Its files were removed from search.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Detach rag-data/jira" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Detach code/payments" })).toBeNull();
});

test("?name= opens the matching collection", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ collections: [base] })));
  render(<MemoryRouter initialEntries={["/rag/collections?name=jira"]}><Routes>
    <Route path="/rag/collections" element={<RagCollectionsView />} /><Route path="/rag/collections/:id" element={<div>detail page</div>} /></Routes></MemoryRouter>);
  expect(await screen.findByText("detail page")).toBeInTheDocument();
});

test("a revision conflict reloads the collection so the next edit uses the new revision", async () => {
  let revision = 2;
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/code-projects") return Response.json(projects);
    if (init?.method === "PUT") return Response.json({ error: { message: "Collection changed; refresh before saving" } }, { status: 409 });
    if (url.endsWith("/files")) return Response.json(noFiles);
    const row = { ...base, revision }; revision = 5; return Response.json(row);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(<MemoryRouter><RagCollectionDetail id="c1" /></MemoryRouter>);
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  await user.click(screen.getByRole("button", { name: "Save collection" }));
  expect(await screen.findByText("Collection changed; refresh before saving")).toBeInTheDocument();
  await waitFor(() => expect(fetchMock.mock.calls.filter(([u, i]) => String(u).endsWith("/collections/c1") && !(i as RequestInit)?.method?.match(/PUT/)).length).toBeGreaterThan(1));
  await user.keyboard("{Escape}");
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  await user.click(screen.getByRole("button", { name: "Save collection" }));
  await waitFor(() => expect(fetchMock.mock.calls.filter(([, i]) => (i as RequestInit)?.method === "PUT")).toHaveLength(2));
  const puts = fetchMock.mock.calls.filter(([, i]) => (i as RequestInit)?.method === "PUT");
  expect(JSON.parse((puts[1][1] as RequestInit).body as string).revision).toBe(5);
});
