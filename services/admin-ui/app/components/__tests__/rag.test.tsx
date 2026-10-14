import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import RagSettingsView from "~/features/rag/RagSettingsView";
import RagSearchView from "~/features/rag/RagSearchView";
import type { RagConfig } from "~/features/rag/api";

const config: RagConfig = {
  revision: 3,
  settings: { max_file_bytes: 2000000 },
  embedding_model_id: null,
  data_root: { container_root: "/work/data", host_root: "/srv/knowledge", jira_folder: "jira" },
};

test("settings show the data folder read-only and save only max file size and model selection", async () => {
  const fetch = vi.fn(
    async (url: string, init?: RequestInit) =>
      new Response(
        JSON.stringify(
          url === "/api/llm"
            ? { models: [] }
            : init?.method === "PUT"
              ? { ...config, revision: 4, settings: JSON.parse(init.body as string).settings }
              : config,
        ),
      ),
  );
  vi.stubGlobal("fetch", fetch);
  render(
    <MemoryRouter>
      <RagSettingsView />
    </MemoryRouter>,
  );
  fireEvent.change(await screen.findByLabelText("Maximum file size (bytes)"), { target: { value: "3000000" } });
  expect(screen.getByText("/srv/knowledge")).toBeInTheDocument();
  expect(screen.getByText("/work/data")).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "jira" })).toBeInTheDocument();
  expect(screen.queryByLabelText("Chunk size (characters)")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Save RAG settings" }));
  await screen.findByText(/RAG settings saved/);
  const saved = JSON.parse(fetch.mock.calls.find(([, init]) => init?.method === "PUT")![1]!.body as string);
  expect(saved).toEqual({ revision: 3, settings: { max_file_bytes: 3000000 }, embedding_model_id: null });
});

test("search picks collections, sends no mode by default and renders evidence as text", async () => {
  const fetch = vi.fn(
    async (url: string) =>
      new Response(
        JSON.stringify(
          url.endsWith("/collections")
            ? {
                collections: [
                  { id: "c1", name: "jira" },
                  { id: "c2", name: "docs" },
                ],
              }
            : {
                collections: ["jira"],
                fallback: ["jira"],
                results: [
                  {
                    id: "1",
                    citation: "S1",
                    path: "a.md",
                    collection: "jira",
                    score: 0.5,
                    text: "<script>untrusted()</script>",
                    indexed_at: "2026-09-09T00:00:00Z",
                    content_hash: "abcdef123456",
                    position: 0,
                    job_id: "j",
                  },
                ],
              },
        ),
      ),
  );
  vi.stubGlobal("fetch", fetch);
  render(<RagSearchView />);
  fireEvent.click(await screen.findByRole("checkbox", { name: "jira" }));
  fireEvent.change(screen.getByLabelText("Search query"), { target: { value: "retries" } });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  expect(await screen.findByText("<script>untrusted()</script>")).toBeInTheDocument();
  expect(document.querySelector("script")).toBeNull();
  expect(screen.getByText("jira/a.md")).toBeInTheDocument();
  expect(screen.getByText("Keyword search used for: jira")).toBeInTheDocument();
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      "/api/rag/search",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ query: "retries", top: 10, collections: ["jira"] }),
      }),
    ),
  );
});

test("search sends the chosen mode", async () => {
  const user = userEvent.setup();
  const fetch = vi.fn(
    async (url: string) =>
      new Response(
        JSON.stringify(
          url.endsWith("/collections") ? { collections: [] } : { collections: [], fallback: [], results: [] },
        ),
      ),
  );
  vi.stubGlobal("fetch", fetch);
  render(<RagSearchView />);
  await user.type(screen.getByLabelText("Search query"), "x");
  await user.click(screen.getByRole("combobox", { name: "Search type" }));
  await user.click(screen.getByRole("option", { name: "Hybrid" }));
  await user.click(screen.getByRole("button", { name: "Search" }));
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      "/api/rag/search",
      expect.objectContaining({ body: JSON.stringify({ query: "x", top: 10, mode: "hybrid" }) }),
    ),
  );
});

test("RAG selects a saved embedding connection and never offers chat models", async () => {
  const user = userEvent.setup();
  const fetch = vi.fn(
    async (url: string, init?: RequestInit) =>
      new Response(
        JSON.stringify(
          url === "/api/llm"
            ? {
                models: [
                  {
                    id: "embed",
                    name: "Document vectors",
                    model: "embedding-test",
                    api_format: "embeddings",
                    embedding_dimensions: 3,
                    embedding_version: "1",
                    enabled: true,
                  },
                  { id: "chat", name: "Chat model", api_format: "responses", enabled: true },
                ],
              }
            : init?.method === "PUT"
              ? { ...config, ...JSON.parse(init.body as string), revision: 4 }
              : config,
        ),
      ),
  );
  vi.stubGlobal("fetch", fetch);
  render(
    <MemoryRouter>
      <RagSettingsView />
    </MemoryRouter>,
  );
  await user.click(await screen.findByRole("combobox", { name: "Embedding model" }));
  expect(screen.queryByRole("option", { name: "Chat model" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("option", { name: "Document vectors" }));
  expect(screen.getByRole("link", { name: "LLM Settings" })).toHaveAttribute("href", "/llm-settings");
  await user.click(screen.getByRole("button", { name: "Save RAG settings" }));
  await screen.findByText(/RAG settings saved/);
  const saved = JSON.parse(fetch.mock.calls.find(([, init]) => init?.method === "PUT")![1]!.body as string);
  expect(saved.embedding_model_id).toBe("embed");
  expect(saved.settings).toEqual({ max_file_bytes: 2000000 });
});
