import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import RagCollectionsView from "../RagCollectionsView";
import RagCollectionDetail from "../RagCollectionDetail";
import type { RagCollection } from "../../lib/rag";

const base: RagCollection = {
  id: "c1", name: "jira", description: "Jira tickets", source_path: "rag-data/jira", search_mode: "full_text",
  embeddings_enabled: true, include: ["**/*.md"], exclude: [], chunk_size: 2000, chunk_overlap: 200, revision: 2,
  synced_revision: 2, last_synced_at: "2026-10-05T09:00:00Z", host_path: "/opt/ka/rag-data/jira", container_path: "/work/rag-data/jira",
  writable: true, folder_present: true, folder_changed_at: "2026-10-05T10:00:00Z", file_count: 75, truncated: false,
  skipped: { links: 0, special: 0, unreadable: 0, invalid_names: 0 }, indexed_files: 75, chunks: 975, embedded_chunks: 0, needs_refresh: true,
  status: "needs_refresh", job: null,
};

test("lists collections with mapping, sync times, embeddings and Refresh", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => init?.method === "POST"
    ? Response.json({ id: "job" }, { status: 202 })
    : Response.json({ collections: [base, { ...base, id: "c2", name: "documents", source_path: "generated-documents", status: "up_to_date", needs_refresh: false, embedded_chunks: 10, chunks: 10 }] }));
  vi.stubGlobal("fetch", fetchMock);
  render(<MemoryRouter><RagCollectionsView /></MemoryRouter>);
  const row = (await screen.findByText("jira")).closest("tr")!;
  expect(within(row).getByText("/opt/ka/rag-data/jira")).toBeInTheDocument();
  expect(within(row).getByText("0 / 975")).toBeInTheDocument();
  expect(within(row).getByText("Needs refresh")).toBeInTheDocument();
  const documents = screen.getByText("documents").closest("tr")!;
  expect(within(documents).queryByRole("button", { name: "Refresh" })).toBeNull();
  await userEvent.click(within(row).getByRole("button", { name: "Refresh" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/rag/collections/c1/sync", expect.objectContaining({ method: "POST" })));
});

test("missing folders explain themselves and failed syncs show the error", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ collections: [
    { ...base, status: "folder_missing", folder_present: false },
    { ...base, id: "c3", name: "code", status: "failed", job: { id: "j", status: "failed", error: "Select an enabled embedding model", created_at: "", finished_at: "" } }] })));
  render(<MemoryRouter><RagCollectionsView /></MemoryRouter>);
  expect(await screen.findByText("Folder missing")).toBeInTheDocument();
  expect(screen.getByText(/Select an enabled embedding model/)).toBeInTheDocument();
});

test("new collection posts the form and validates name and folder", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => init?.method === "POST"
    ? Response.json({ id: "new" }) : Response.json({ collections: [] }));
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(<MemoryRouter><RagCollectionsView /></MemoryRouter>);
  await user.click(await screen.findByRole("button", { name: "New collection" }));
  await user.type(screen.getByLabelText("Name"), "payments-code");
  await user.clear(screen.getByLabelText("Folder"));
  await user.type(screen.getByLabelText("Folder"), "code/payments");
  await user.clear(screen.getByLabelText("Include patterns"));
  await user.type(screen.getByLabelText("Include patterns"), "**/*.py");
  await user.click(screen.getByRole("button", { name: "Create collection" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/rag/collections", expect.objectContaining({ method: "POST" })));
  const body = JSON.parse((fetchMock.mock.calls.find(([, i]) => (i as RequestInit)?.method === "POST")![1] as RequestInit).body as string);
  expect(body).toMatchObject({ name: "payments-code", source_path: "code/payments", include: ["**/*.py"] });
});

test("detail shows files with chunk counts and loads chunks on selection", async () => {
  const fetchMock = vi.fn(async (url: string) => {
    if (url.includes("/chunks")) return Response.json({ chunks: [{ position: 0, text: "First chunk text", embedded: false }] });
    if (url.endsWith("/files")) return Response.json({ files: [{ path: "DEMO-1.md", size: 10, mtime: 1, indexed_at: "2026-10-05T09:00:00Z", content_hash: "abc", chunks: 13, embedded_chunks: 0, stale: false }], truncated: false, skipped: { links: 0, special: 0, unreadable: 0, invalid_names: 0 } });
    return Response.json(base);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(<MemoryRouter><RagCollectionDetail id="c1" /></MemoryRouter>);
  const row = (await screen.findByText("DEMO-1.md")).closest("tr")!;
  expect(within(row).getByText("13")).toBeInTheDocument();
  expect(within(row).getByText("0 embedded")).toBeInTheDocument();
  await user.click(within(row).getByRole("button", { name: "DEMO-1.md" }));
  expect(await screen.findByText("First chunk text")).toBeInTheDocument();
  expect(screen.getByText("Not embedded")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/api/rag/collections/c1/chunks?path=DEMO-1.md"), expect.anything());
});

test("?name= opens the matching collection", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ collections: [base] })));
  render(<MemoryRouter initialEntries={["/rag/collections?name=jira"]}><Routes>
    <Route path="/rag/collections" element={<RagCollectionsView />} /><Route path="/rag/collections/:id" element={<div>detail page</div>} /></Routes></MemoryRouter>);
  expect(await screen.findByText("detail page")).toBeInTheDocument();
});

test("file editing and upload address the collection by name", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes("/api/rag/file?")) return Response.json({ collection: "jira", path: "a.md", content: "Old", hash: "h1" });
    if (url === "/api/rag/file") return Response.json({ path: "a.md", hash: "h2" });
    if (url.endsWith("/files")) return Response.json({ files: [], truncated: false, skipped: { links: 0, special: 0, unreadable: 0, invalid_names: 0 } });
    return Response.json(base);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(<MemoryRouter><RagCollectionDetail id="c1" /></MemoryRouter>);
  await user.type(await screen.findByLabelText("File path"), "a.md");
  await user.click(screen.getByRole("button", { name: "Open file" }));
  await user.clear(await screen.findByLabelText("Content"));
  await user.type(screen.getByLabelText("Content"), "New");
  await user.click(screen.getByRole("button", { name: "Save file" }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([, i]) => (i as RequestInit)?.method === "PUT")).toBe(true));
  expect(fetchMock.mock.calls.find(([u]) => String(u).includes("/api/rag/file?"))![0]).toBe("/api/rag/file?collection=jira&path=a.md");
  const put = fetchMock.mock.calls.find(([, i]) => (i as RequestInit)?.method === "PUT")!;
  expect(JSON.parse((put[1] as RequestInit).body as string)).toEqual({ collection: "jira", path: "a.md", content: "New", expected_hash: "h1" });
  await waitFor(() => expect(screen.getByLabelText("Upload file")).toBeEnabled());
  await user.upload(screen.getByLabelText("Upload file"), Object.assign(new File(["hello"], "up.md", { type: "text/markdown" }), { text: async () => "hello" }));
  await waitFor(() => expect(fetchMock.mock.calls.filter(([, i]) => (i as RequestInit)?.method === "PUT")).toHaveLength(2));
  const up = fetchMock.mock.calls.filter(([, i]) => (i as RequestInit)?.method === "PUT")[1];
  expect(JSON.parse((up[1] as RequestInit).body as string)).toEqual({ collection: "jira", path: "up.md", content: "hello", expected_hash: null });
});

test("a revision conflict reloads the collection so the next edit uses the new revision", async () => {
  let revision = 2;
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "PUT") return Response.json({ error: { message: "Collection changed; refresh before saving" } }, { status: 409 });
    if (url.endsWith("/files")) return Response.json({ files: [], truncated: false, skipped: { links: 0, special: 0, unreadable: 0, invalid_names: 0 } });
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
