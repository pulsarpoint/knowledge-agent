import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import RagJobsView from "../RagJobsView";
import RagFilesView from "../RagFilesView";
import RagSettingsView from "../RagSettingsView";
import RagSearchView from "../RagSearchView";
import type { RagConfig, RagTree } from "../../lib/rag";

const sourceLocation = { host_root: "/srv/knowledge/rag-data", container_root: "/work/rag-data" };
const tree: RagTree = { revision: 2, source_root: "/work/rag-data", source_location: sourceLocation, folders: [{ id: "f1", path: "docs/payments", scope: "docs.payments", description: "Payment docs", present: true, indexed_files: 1, files: 1, indexed_at: null }], files: [{ path: "docs/payments/readme.md", folder: "docs/payments", size: 100, supported: true, indexed_hash: "oldhash", indexed_at: null }] };
const config: RagConfig = { revision: 3, source_root: "/work/rag-data", source_location: sourceLocation, embedding_model_id: null, settings: {
  chunk_size: 2000, chunk_overlap: 200, max_file_bytes: 2000000, extensions: [".md", ".txt"], embedding_enabled: false,
} };

test("folder processing defaults to incremental and source edits carry the current hash", async () => {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => new Response(JSON.stringify(url.includes("/file?") ? { path: "docs/payments/readme.md", content: "Original", hash: "currenthash" } : init?.method === "GET" ? tree : { id: "j1" })));
  vi.stubGlobal("fetch", fetch);
  render(<MemoryRouter><RagFilesView /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: "Expand docs" }));
  fireEvent.click(screen.getByRole("treeitem", { name: "payments" }));
  expect(screen.getByText(sourceLocation.host_root)).toBeInTheDocument();
  expect(screen.getByText(sourceLocation.container_root)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Process folder" }));
  await screen.findByText(/Processing queued/);
  const queued = fetch.mock.calls.find(([url]) => url.endsWith("/jobs"))!;
  expect(JSON.parse(queued[1]!.body as string)).toEqual({ folder_id: "f1", recursive: true, force: false, allow_empty: false });
  fireEvent.click(screen.getByRole("button", { name: "readme.md" }));
  fireEvent.change(await screen.findByLabelText("Content"), { target: { value: "Edited" } });
  fireEvent.click(screen.getByRole("button", { name: "Save file" }));
  await screen.findByText("File saved. Process its folder to update search.");
  const saved = fetch.mock.calls.find(([, init]) => init?.method === "PUT")!;
  expect(JSON.parse(saved[1]!.body as string)).toEqual({ path: "docs/payments/readme.md", content: "Edited", expected_hash: "currenthash" });
});

test("opening a file in the tree selects its parent and refresh keeps the branch open", async () => {
  const user = userEvent.setup();
  const fetch = vi.fn(async (url: string) => new Response(JSON.stringify(url.includes("/file?")
    ? { path: "docs/payments/readme.md", content: "Original", hash: "currenthash" } : tree)));
  vi.stubGlobal("fetch", fetch);
  render(<MemoryRouter><RagFilesView /></MemoryRouter>);
  await user.click(await screen.findByRole("button", { name: "Expand docs" }));
  await user.click(screen.getByRole("button", { name: "Expand docs/payments" }));
  await user.click(screen.getByRole("treeitem", { name: "readme.md" }));
  expect(await screen.findByLabelText("Content")).toHaveValue("Original");
  expect(screen.getByLabelText("File path")).toHaveValue("docs/payments/readme.md");
  await user.keyboard("{Escape}");
  expect(screen.getByLabelText("Folder description")).toHaveValue("Payment docs");
  expect(screen.getByRole("treeitem", { name: "payments" })).toHaveAttribute("aria-selected", "true");
  await user.click(screen.getByRole("button", { name: "Refresh folders" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Refresh folders" })).toBeEnabled());
  expect(screen.getByRole("treeitem", { name: "readme.md" })).toBeInTheDocument();
  expect(fetch.mock.calls.filter(([url]) => url.includes("/file?"))).toHaveLength(1);
});

test("a file deep link opens the editor and reveals its nested tree path", async () => {
  window.history.replaceState({}, "", "/rag/files?path=docs%2Fpayments%2Freadme.md");
  vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(url.includes("/file?")
    ? { path: "docs/payments/readme.md", content: "Linked document", hash: "currenthash" } : tree))));
  try {
    render(<MemoryRouter><RagFilesView /></MemoryRouter>);
    expect(await screen.findByLabelText("Content")).toHaveValue("Linked document");
    fireEvent.keyDown(screen.getByLabelText("Content"), { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByRole("treeitem", { name: "readme.md" })).toBeInTheDocument();
    expect(screen.getByLabelText("Folder description")).toHaveValue("Payment docs");
  } finally {
    window.history.replaceState({}, "", "/");
  }
});

test("settings keep the source root read-only and save only processing and model selection", async () => {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => new Response(JSON.stringify(url === "/api/llm" ? { models: [] } : init?.method === "PUT" ? { ...config, revision: 4, settings: JSON.parse(init.body as string).settings } : config)));
  vi.stubGlobal("fetch", fetch);
  render(<MemoryRouter><RagSettingsView /></MemoryRouter>);
  fireEvent.change(await screen.findByLabelText("Chunk size (characters)"), { target: { value: "3000" } });
  expect(screen.getByText(sourceLocation.host_root)).toBeInTheDocument();
  expect(screen.getByText(sourceLocation.container_root)).toBeInTheDocument();
  expect(screen.getByText("Host folder (RG_DATA)")).toBeInTheDocument();
  expect(screen.queryByDisplayValue(sourceLocation.host_root)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Save RAG settings" }));
  await screen.findByText(/RAG settings saved/);
  const saved = JSON.parse(fetch.mock.calls.find(([, init]) => init?.method === "PUT")![1]!.body as string);
  expect(saved.revision).toBe(3); expect(saved.settings.chunk_size).toBe(3000);
  expect(saved.embedding_model_id).toBeNull(); expect(screen.queryByLabelText("Embedding API key")).not.toBeInTheDocument();
  expect(saved).not.toHaveProperty("embedding_key"); expect(saved.settings).not.toHaveProperty("source_root");
  expect(saved).not.toHaveProperty("source_location");
});

test("search sends folder scope and renders evidence as text", async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify({ mode: "full_text", folder: "docs.payments.*", results: [{ id: "1", citation: "S1", path: "docs/payments/a.md", kind: "file", text: "<script>untrusted()</script>", indexed_at: "2026-09-09T00:00:00Z", content_hash: "abcdef123456" }] })));
  vi.stubGlobal("fetch", fetch);
  render(<RagSearchView />);
  fireEvent.change(screen.getByLabelText("Search query"), { target: { value: "retries" } });
  fireEvent.change(screen.getByLabelText("Folder scope"), { target: { value: "docs.payments.*" } });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  expect(await screen.findByText("<script>untrusted()</script>")).toBeInTheDocument();
  expect(document.querySelector("script")).toBeNull();
  await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/rag/search", expect.objectContaining({ method: "POST", body: JSON.stringify({ query: "retries", folder: "docs.payments.*", mode: "full_text", top: 8 }) })));
});

test("folder deletion requires confirmation and Cancel makes no delete request", async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify(tree)));
  vi.stubGlobal("fetch", fetch);
  render(<MemoryRouter><RagFilesView /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: "Expand docs" }));
  fireEvent.click(screen.getByRole("treeitem", { name: "payments" }));
  fireEvent.click(screen.getByRole("button", { name: "Delete folder" }));
  const dialog = screen.getByRole("alertdialog");
  expect(within(dialog).getByRole("heading", { name: "Delete “docs/payments”?" })).toBeInTheDocument();
  expect(within(dialog).getByText(/indexed text, folder descriptions, and embeddings/)).toBeInTheDocument();
  expect(fetch.mock.calls).toHaveLength(1);
  expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(fetch.mock.calls).toHaveLength(1);
  expect(screen.getByRole("treeitem", { name: "payments" })).toBeInTheDocument();
});

test("confirming deletion keeps the dialog busy until cleanup returns and removes the selected folder", async () => {
  let finish!: (response: Response) => void;
  let deleted = false;
  const fetch = vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "DELETE") {
      deleted = true;
      return new Promise<Response>((resolve) => { finish = resolve; });
    }
    return new Response(JSON.stringify(deleted ? { ...tree, revision: 3, folders: [], files: [] } : tree));
  });
  vi.stubGlobal("fetch", fetch);
  render(<MemoryRouter><RagFilesView /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: "Expand docs" }));
  fireEvent.click(screen.getByRole("treeitem", { name: "payments" }));
  fireEvent.click(screen.getByRole("button", { name: "Delete folder" }));
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete folder and data" }));
  expect(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" })).toBeDisabled();
  expect(within(screen.getByRole("alertdialog")).getByRole("button", { name: /Delete folder and data/ })).toBeDisabled();
  expect(fetch.mock.calls.find(([, init]) => init?.method === "DELETE")![0]).toBe("/api/rag/folders/f1?revision=2&confirmed=true");
  await act(async () => { finish(new Response(JSON.stringify({ cleanup_pending: false }))); });
  await screen.findByText("Folder, source files, indexed text, and embeddings deleted.");
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(screen.queryByRole("treeitem", { name: "payments" })).toBeNull();
});

test("a stale deletion keeps the confirmation dialog open and preserves the folder", async () => {
  const fetch = vi.fn(async (_url: string, init?: RequestInit) => new Response(JSON.stringify(init?.method === "DELETE" ? { error: { message: "Workspace changed; refresh before saving" } } : tree), { status: init?.method === "DELETE" ? 409 : 200 }));
  vi.stubGlobal("fetch", fetch);
  render(<MemoryRouter><RagFilesView /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: "Expand docs" }));
  fireEvent.click(screen.getByRole("treeitem", { name: "payments" }));
  fireEvent.click(screen.getByRole("button", { name: "Delete folder" }));
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete folder and data" }));
  expect(await within(screen.getByRole("alertdialog")).findByText("Workspace changed; refresh before saving")).toBeInTheDocument();
  expect(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" })).toBeEnabled();
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
  expect(screen.getByRole("treeitem", { name: "payments" })).toBeInTheDocument();
});

test("deleted folders cannot be rerun from job history", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ jobs: [{ id: "job1", path: "docs/payments", status: "succeeded", input: { recursive: true, force: false }, result: {}, error: null, created_at: "2026-09-09T00:00:00Z", folder_deleted_at: "2026-09-09T01:00:00Z" }] }))));
  render(<RagJobsView />);
  expect(await screen.findByText("Folder deleted")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Run again" })).toBeNull();
});


test('RAG selects a saved embedding connection and never offers chat models', async () => {
  const user = userEvent.setup();
  const fetch = vi.fn(async (url: string, init?: RequestInit) => new Response(JSON.stringify(url === '/api/llm'
    ? { models: [{ id: 'embed', name: 'Document vectors', model: 'embedding-test', api_format: 'embeddings', embedding_dimensions: 3, embedding_version: '1', enabled: true }, { id: 'chat', name: 'Chat model', api_format: 'responses', enabled: true }] }
    : init?.method === 'PUT' ? { ...config, ...JSON.parse(init.body as string), revision: 4 } : config)));
  vi.stubGlobal('fetch', fetch);
  render(<MemoryRouter><RagSettingsView /></MemoryRouter>);
  await user.click(await screen.findByLabelText('Enable embedding processing and vector search'));
  expect(screen.getByRole('button', { name: 'Save RAG settings' })).toBeDisabled();
  await user.click(screen.getByRole('combobox', { name: 'Embedding model' }));
  expect(screen.queryByRole('option', { name: 'Chat model' })).not.toBeInTheDocument();
  await user.click(screen.getByRole('option', { name: 'Document vectors' }));
  expect(screen.getByRole('link', { name: 'LLM Settings' })).toHaveAttribute('href', '/llm-settings');
  await user.click(screen.getByRole('button', { name: 'Save RAG settings' }));
  await screen.findByText(/RAG settings saved/);
  const saved = JSON.parse(fetch.mock.calls.find(([, init]) => init?.method === 'PUT')![1]!.body as string);
  expect(saved.embedding_model_id).toBe('embed');
  expect(saved.settings.embedding_enabled).toBe(true);
  expect(saved.settings).not.toHaveProperty('embedding_model');
});
