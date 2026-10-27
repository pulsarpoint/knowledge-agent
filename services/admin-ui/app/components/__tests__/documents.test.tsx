import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import DocumentEditorView from "~/features/documents/DocumentEditorView";
import DocumentsView from "~/features/documents/DocumentsView";
import { DocumentationPromptEditorView } from "~/features/documents/DocumentationPromptsView";
import { Toaster } from "~/components/ui/sonner";

beforeEach(() => {
  render(<Toaster />);
});
vi.mock("../Markdown", () => ({ default: ({ content }: { content: string }) => <article>{content}</article> }));
const NativeRequest = globalThis.Request;
// React Router's Node Request cannot consume jsdom's AbortSignal. Fetch is mocked in this suite.
beforeEach(() =>
  vi.stubGlobal(
    "Request",
    class extends NativeRequest {
      constructor(input: RequestInfo | URL, init?: RequestInit) {
        super(input, { ...init, signal: undefined });
      }
    },
  ),
);
afterEach(() => vi.unstubAllGlobals());
const resources = {
  code_projects: [{ id: "repo-1", name: "Payments code" }],
  services: [{ id: "service-1", name: "Payments API" }],
  servers: [{ id: "server-1", name: "Production host" }],
  deployments: [
    {
      id: "deployment-1",
      name: "Payments API / Production host / prod",
      service_id: "service-1",
      server_id: "server-1",
    },
  ],
};
const first = {
  version: 1,
  title: "Payments runbook",
  description: "Original description",
  purpose: "runbook",
  audience: "both",
  tags: [],
  markdown: "# Original",
  content_hash: "a".repeat(64),
  source_snapshot: { ...resources, code_projects: [{ ...resources.code_projects[0], git_commit: "a".repeat(40) }] },
  origin: "manual",
  from_draft: false,
  session_id: null,
  created_by: null,
  created_at: "2026-09-13T10:00:00Z",
  live: false,
};
const document = {
  id: "doc-1",
  slug: "payments-runbook",
  title: "Payments runbook",
  description: "Current description",
  purpose: "runbook",
  audience: "both",
  tags: [],
  code_project_ids: ["repo-1"],
  service_ids: [],
  server_ids: [],
  deployment_ids: [],
  revision: 2,
  version: 2,
  origin: "manual",
  from_draft: false,
  markdown: "# Current",
  source_snapshot: { ...resources, code_projects: [{ ...resources.code_projects[0], git_commit: "b".repeat(40) }] },
  versions: [
    {
      version: 2,
      title: "Payments runbook",
      origin: "manual",
      created_at: first.created_at,
      from_draft: false,
      live: true,
    },
    {
      version: 1,
      title: "Payments runbook",
      origin: "manual",
      created_at: first.created_at,
      from_draft: false,
      live: false,
    },
  ],
  drafts: [],
  resource_links: [],
  updated_at: "2026-09-13T11:00:00Z",
};
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
function setup(url: string, saveStatus = 200) {
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (init?.method === "PUT" || init?.method === "POST")
      return saveStatus === 200
        ? response({ id: "doc-1", revision: 3, version: 3 })
        : response({ error: { message: "This document changed. Reload before saving." } }, saveStatus);
    if (init?.method === "DELETE") return response({ id: "doc-1" });
    if (path.endsWith("/resources")) return response(resources);
    if (path.includes("/versions/")) return response(first);
    if (path.includes("/drafts")) return response({ drafts: [] });
    if (path.includes("/documents?")) return response({ documents: [document], total: 1 });
    return response(document);
  });
  vi.stubGlobal("fetch", fetch);
  const router = createMemoryRouter(
    [
      { path: "/documents", element: <DocumentsView /> },
      { path: "/documents/new", element: <DocumentEditorView /> },
      { path: "/documents/:documentId", element: <DocumentEditorView /> },
    ],
    { initialEntries: [url] },
  );
  render(<RouterProvider router={router} />);
  return { fetch, router };
}
function tab(name: string) {
  fireEvent.mouseDown(screen.getByRole("tab", { name }), { button: 0, ctrlKey: false });
}

test("creates a document with multiple resource types and deployment context", async () => {
  const { fetch } = setup("/documents/new");
  fireEvent.change(await screen.findByLabelText("Title"), { target: { value: "New runbook" } });
  expect(screen.getByLabelText("Slug")).toHaveValue("new-runbook");
  fireEvent.change(screen.getByLabelText("Markdown"), { target: { value: "# How to investigate" } });
  tab("Linked resources");
  fireEvent.click(await screen.findByRole("checkbox", { name: "Payments code" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Payments API / Production host / prod" }));
  expect(screen.getByRole("checkbox", { name: "Payments API" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Production host" })).toBeChecked();
  expect(screen.queryByRole("button", { name: "Create document" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
  const [url, init] = fetch.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(url).toBe("/api/documentation/drafts"); // every save is a draft: a new document starts as a brand-new draft
  expect(JSON.parse(init!.body as string)).toMatchObject({
    slug: "new-runbook",
    markdown: "# How to investigate",
    resource_ids: {
      code_project_ids: ["repo-1"],
      service_ids: ["service-1"],
      server_ids: ["server-1"],
      deployment_ids: ["deployment-1"],
    },
  });
});

test("previews a saved version and loads it as an unsaved draft without changing history", async () => {
  const { fetch } = setup("/documents/doc-1");
  await screen.findByLabelText("Title");
  tab("Version history");
  expect(await screen.findByRole("button", { name: /^Version 2 · Current/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^Version 1/ })).not.toHaveTextContent("Current");
  fireEvent.click(screen.getByRole("button", { name: /^Version 1/ }));
  expect(await screen.findByText("# Original")).toBeInTheDocument();
  expect(fetch.mock.calls.some(([url]) => url === "/api/documentation/documents/doc-1/versions/1")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Use version as draft" }));
  expect(await screen.findByLabelText("Markdown")).toHaveValue("# Original");
  expect(screen.getByLabelText("Short description")).toHaveValue("Original description");
  expect(fetch.mock.calls.some(([, init]) => init?.method === "PUT" || init?.method === "POST")).toBe(false);
  expect(screen.queryByRole("button", { name: "Save new version" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
  const [url, init] = fetch.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(url).toBe("/api/documentation/drafts"); // the version's text becomes the person's draft, not a live version
  expect(JSON.parse(init!.body as string)).toMatchObject({ document_id: "doc-1", markdown: "# Original" });
  expect(fetch.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(false);
});

test("failed save retains edits and navigating away requires confirmation", async () => {
  setup("/documents/doc-1", 409);
  await screen.findByLabelText("Title");
  fireEvent.change(screen.getByLabelText("Markdown"), { target: { value: "# Unsaved work" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("This document changed");
  expect(screen.getByLabelText("Markdown")).toHaveValue("# Unsaved work");
  fireEvent.click(screen.getByRole("link", { name: "All documents" }));
  const dialog = await screen.findByRole("alertdialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Stay" }));
  expect(screen.getByLabelText("Markdown")).toHaveValue("# Unsaved work");
});

test("document deletion requires confirmation and Cancel never sends DELETE", async () => {
  const { fetch } = setup("/documents/doc-1");
  fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
  expect(fetch.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete document" }));
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith("/api/documentation/documents/doc-1?revision=2&confirmed=true", {
      method: "DELETE",
    }),
  );
});

test("prompt edits create inactive versions, a save shows a toast, active versions cannot be deleted", async () => {
  const prompt = {
    id: "prompt-1",
    name: "Architecture",
    description: "Analyze code",
    tags: ["documentation", "code"],
    enabled: true,
    revision: 3,
    active_version_id: "p1",
    versions: [
      { id: "p2", version: 2, content: "Newer instructions", activated_at: null, created_at: first.created_at },
      {
        id: "p1",
        version: 1,
        content: "Active instructions",
        activated_at: first.created_at,
        created_at: first.created_at,
      },
    ],
  };
  const fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
    init?.method === "PUT" ? response({ id: "prompt-1", revision: 4, version_id: "p3" }) : response(prompt),
  );
  vi.stubGlobal("fetch", fetch);
  const router = createMemoryRouter([{ path: "/prompts/:promptId", element: <DocumentationPromptEditorView /> }], {
    initialEntries: ["/prompts/prompt-1"],
  });
  render(<RouterProvider router={router} />);
  await screen.findByLabelText("Name");
  expect(screen.getByRole("button", { name: "Delete v1" })).toBeDisabled();
  expect(screen.getByText("Inactive version", { exact: true })).toBeInTheDocument();
  expect(screen.queryByText(/draft/i)).toBeNull();
  fireEvent.change(screen.getByLabelText("Prompt text"), { target: { value: "Improved instructions" } });
  expect(screen.getByRole("button", { name: "Activate v2" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Save prompt" }));
  await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(true));
  const saved = JSON.parse(fetch.mock.calls.find(([, init]) => init?.method === "PUT")![1]!.body as string);
  expect(saved).toMatchObject({ revision: 3, content: "Improved instructions" });
  expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  expect(await screen.findByText("Prompt “Architecture” saved.")).toBeInTheDocument();
});
