import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import DocumentationFoldersView, { DocumentationFolderView } from "~/features/documents/DocumentationFoldersView";

afterEach(() => vi.unstubAllGlobals());
const folders = {
  generated_folder: "generated-documents",
  folders: [
    {
      folder: "payments",
      document_count: 2,
      updated_at: "2026-10-07T10:00:00Z",
      last_generated_at: "2026-10-07T10:00:00Z",
      code_projects: ["Payments API"],
      source_path: "generated-documents/payments",
    },
    {
      folder: "team/payments",
      document_count: 1,
      updated_at: "2026-10-07T10:00:00Z",
      last_generated_at: null,
      code_projects: [],
      source_path: "generated-documents/team/payments",
    },
  ],
};
const collections = {
  collections: [
    { id: "c1", name: "payments-docs", paths: [{ id: "p1", source_path: "generated-documents/payments" }] },
    { id: "c2", name: "jira", paths: [{ id: "p2", source_path: "jira" }] },
    { id: "c3", name: "all-docs", paths: [{ id: "p3", source_path: "generated-documents" }] },
    { id: "c4", name: "lookalike", paths: [{ id: "p4", source_path: "generated-documents/pay" }] },
  ],
};

function setup(rag: () => Response = () => Response.json(collections)) {
  const fetch = vi.fn(async (url: string, _init?: RequestInit) =>
    url === "/api/rag/collections?status=false" ? rag() : Response.json(folders),
  );
  vi.stubGlobal("fetch", fetch);
  render(
    <MemoryRouter>
      <DocumentationFoldersView />
    </MemoryRouter>,
  );
  return fetch;
}

test("folders show their documents, projects and the RAG collections that include them, read-only", async () => {
  const fetch = setup();
  expect(await screen.findByRole("link", { name: "team/payments" })).toHaveAttribute(
    "href",
    "/documentation/folders/team/payments",
  );
  expect(screen.getByText("Payments API")).toBeInTheDocument();
  expect(await screen.findByRole("link", { name: "payments-docs" })).toHaveAttribute("href", "/rag/collections/c1");
  expect(screen.getAllByRole("link", { name: "all-docs" })).toHaveLength(2);
  expect(screen.getAllByText("through generated-documents")).toHaveLength(2);
  expect(screen.queryByRole("link", { name: "jira" })).toBeNull();
  expect(screen.queryByRole("link", { name: "lookalike" })).toBeNull();
  expect(screen.getByRole("link", { name: "Index team/payments in RAG → Collections" })).toHaveAttribute(
    "href",
    "/rag/collections?folder=generated-documents%2Fteam%2Fpayments",
  );
  expect(screen.queryByRole("button", { name: /Disconnect|Index/ })).toBeNull();
  expect(
    fetch.mock.calls.every(
      ([, init]) => !(init as RequestInit | undefined)?.method || (init as RequestInit).method === "GET",
    ),
  ).toBe(true);
});

test("folders still list when RAG is unavailable", async () => {
  setup(() => Response.json({ error: { message: "RAG service is unavailable" } }, { status: 502 }));
  expect(await screen.findByRole("link", { name: "payments" })).toBeInTheDocument();
  expect(await screen.findByText("RAG collections unavailable: RAG service is unavailable")).toBeInTheDocument();
});

test("opening a folder lists its documents linking to Documents", async () => {
  const fetch = vi.fn(async (_url: string) =>
    Response.json({
      folder: "team/payments",
      documents: [
        {
          id: "d1",
          slug: "api-reference",
          title: "Payments – API reference",
          version: 2,
          updated_at: "2026-10-07T10:00:00Z",
          last_generated_at: "2026-10-07T10:00:00Z",
          code_projects: ["Payments API"],
        },
      ],
    }),
  );
  vi.stubGlobal("fetch", fetch);
  render(
    <MemoryRouter>
      <DocumentationFolderView folder="team/payments" />
    </MemoryRouter>,
  );
  expect(await screen.findByRole("link", { name: "Payments – API reference" })).toHaveAttribute(
    "href",
    "/documents/d1",
  );
  expect(screen.getByText("Payments API")).toBeInTheDocument();
  expect(fetch.mock.calls[0][0]).toBe("/api/documentation/folders/team/payments");
});
