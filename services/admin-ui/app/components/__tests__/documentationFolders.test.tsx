import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import DocumentationFoldersView, { DocumentationFolderView } from "~/features/documents/DocumentationFoldersView";

afterEach(() => vi.unstubAllGlobals());
const folders = {
  folders: [
    {
      folder: "payments",
      document_count: 2,
      updated_at: "2026-10-07T10:00:00Z",
      last_generated_at: "2026-10-07T10:00:00Z",
      code_projects: ["Payments API"],
    },
    {
      folder: "team/payments",
      document_count: 1,
      updated_at: "2026-10-07T10:00:00Z",
      last_generated_at: null,
      code_projects: [],
    },
  ],
};
function setup() {
  const fetch = vi.fn(async (_url: string) => Response.json(folders));
  vi.stubGlobal("fetch", fetch);
  render(
    <MemoryRouter>
      <DocumentationFoldersView />
    </MemoryRouter>,
  );
  return fetch;
}

test("folders show their documents and projects and call nothing but the folders list", async () => {
  const fetch = setup();
  expect(await screen.findByRole("link", { name: "team/payments" })).toHaveAttribute(
    "href",
    "/documentation/folders/team/payments",
  );
  expect(screen.getByText("Payments API")).toBeInTheDocument();
  expect(screen.queryByText(/RAG|collection/i)).toBeNull();
  expect(fetch.mock.calls.map(([url]) => url)).toEqual(["/api/documentation/folders"]);
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
