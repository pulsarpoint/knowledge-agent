import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import RagJobsView from "../RagJobsView";
import RagSettingsView from "../RagSettingsView";
import RagSearchView from "../RagSearchView";
import type { RagConfig } from "../../lib/rag";

const sourceLocation = { host_root: "/srv/knowledge/rag-data", container_root: "/work/rag-data" };
const config: RagConfig = { revision: 3, source_root: "/work/rag-data", source_location: sourceLocation, embedding_model_id: null, settings: {
  chunk_size: 2000, chunk_overlap: 200, max_file_bytes: 2000000, extensions: [".md", ".txt"], embedding_enabled: false,
} };

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
