import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import AdminLayout from "../AdminLayout";
import Home from "~/routes/home";
import { useWidePage } from "~/lib/widePage";

test("administration has its own navigation without reader pages", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ is_admin: true }))),
  );
  render(
    <MemoryRouter initialEntries={["/documents"]}>
      <AdminLayout>
        <div>Admin content</div>
      </AdminLayout>
    </MemoryRouter>,
  );
  expect(await screen.findByText("Admin content")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Platform" })).toBeNull();
  expect(screen.queryByRole("link", { name: "Collections" })).toBeNull();
  expect(screen.queryByText("RAG")).toBeNull();
  expect(screen.getAllByRole("link").filter((link) => link.getAttribute("href")?.startsWith("/rag"))).toEqual([]);
  expect(screen.queryByRole("link", { name: "Activity" })).toBeNull();
  expect(screen.getByRole("link", { name: "Agent runs" })).toHaveAttribute("href", "/activity/runs");
  expect(screen.getByRole("link", { name: "Schedules" })).toHaveAttribute("href", "/schedules");
  expect(screen.getByRole("link", { name: "Events" })).toHaveAttribute("href", "/events");
  expect(screen.getByRole("link", { name: "Notifications" })).toHaveAttribute("href", "/notifications");
  expect(screen.getByRole("link", { name: "Storage" })).toHaveAttribute("href", "/storage");
  const code = within(screen.getByRole("navigation", { name: "Code" }));
  expect(code.getByRole("link", { name: "Projects" })).toHaveAttribute("href", "/code-projects");
  expect(code.getByRole("link", { name: "Code graphs" })).toHaveAttribute("href", "/code-graphs");
  expect(screen.queryByRole("link", { name: "Code projects" })).toBeNull();
  const environments = within(screen.getByRole("navigation", { name: "Environments" }));
  expect(environments.getByRole("link", { name: "Environments" })).toHaveAttribute("href", "/environments");
  expect(environments.getByRole("link", { name: "Services" })).toHaveAttribute("href", "/services");
  expect(environments.getByRole("link", { name: "Servers" })).toHaveAttribute("href", "/servers");
  expect(environments.getByRole("link", { name: "System map" })).toHaveAttribute("href", "/system-map");
  const administration = within(screen.getByRole("navigation", { name: "Administration" }));
  for (const name of ["Environments", "Services", "Servers"])
    expect(administration.queryByRole("link", { name })).toBeNull();
  expect(administration.getByRole("link", { name: "Prompts" })).toHaveAttribute("href", "/prompts");
  const agents = within(screen.getByRole("navigation", { name: "Agents" }));
  expect(agents.getByRole("link", { name: "All agents" })).toHaveAttribute("href", "/agents");
  expect(agents.getByRole("link", { name: "Output schemas" })).toHaveAttribute("href", "/output-schemas");
  expect(agents.queryByRole("link", { name: "Multi-agent board" })).toBeNull();
  expect(agents.queryByRole("link", { current: "page" })).toBeNull();
  expect(screen.queryByRole("link", { name: "Agents" })).toBeNull();
  const docs = within(screen.getByRole("navigation", { name: "Docs" }));
  expect(docs.getByRole("link", { name: "Documents" })).toHaveAttribute("href", "/documents");
  expect(docs.queryByRole("link", { name: "Prompts" })).toBeNull();
  expect(docs.queryByRole("link", { name: "Generate documents" })).toBeNull();
  expect(docs.queryByRole("link", { name: "Sessions" })).toBeNull();
  expect(docs.getByRole("link", { name: "Folders" })).toHaveAttribute("href", "/documentation/folders");
  expect(screen.queryByRole("link", { name: "Auto pull" })).toBeNull();
  expect(screen.getByRole("link", { name: "Schedules" })).toHaveAttribute("href", "/schedules");
  expect(screen.getByRole("link", { name: "Skills" })).toHaveAttribute("href", "/skills");
  expect(screen.queryByText("Application")).toBeNull();
  expect(screen.queryByText("Administration / docs")).toBeNull();
  for (const name of ["Chat", "Gaps"]) expect(screen.queryByRole("link", { name })).toBeNull();
});

test("denied admin discovery does not mount administrative content", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ error: { message: "admin access required" } }), { status: 403 })),
  );
  render(
    <MemoryRouter>
      <AdminLayout>
        <div>Admin content</div>
      </AdminLayout>
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText("admin access required")).toBeInTheDocument());
  expect(screen.queryByText("Admin content")).toBeNull();
});

test("the Events link stays active on the Handlers tab", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ is_admin: true }))),
  );
  render(
    <MemoryRouter initialEntries={["/events/handlers"]}>
      <AdminLayout>
        <div>Handlers content</div>
      </AdminLayout>
    </MemoryRouter>,
  );
  expect(await screen.findByText("Handlers content")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Events" })).toHaveAttribute("data-active", "true");
  expect(screen.getByRole("heading", { level: 1, name: "Events" })).toBeInTheDocument();
  vi.unstubAllGlobals();
});

test("the Notifications page has its heading", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ is_admin: true }))),
  );
  render(
    <MemoryRouter initialEntries={["/notifications"]}>
      <AdminLayout>
        <div>List</div>
      </AdminLayout>
    </MemoryRouter>,
  );
  expect(await screen.findByText("List")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Notifications" })).toHaveAttribute("data-active", "true");
  expect(screen.getByRole("heading", { level: 1, name: "Notifications" })).toBeInTheDocument();
});

test("an output schema page has its heading and keeps Output schemas current", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ is_admin: true }))),
  );
  render(
    <MemoryRouter initialEntries={["/output-schemas/s1"]}>
      <AdminLayout>
        <div>Editor</div>
      </AdminLayout>
    </MemoryRouter>,
  );
  expect(await screen.findByText("Editor")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Output schemas" })).toHaveAttribute("data-active", "true");
  expect(screen.getByRole("heading", { level: 1, name: "Output schemas" })).toBeInTheDocument();
});

test("a server page has its own heading and keeps Servers current in the navigation", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ is_admin: true }))),
  );
  render(
    <MemoryRouter initialEntries={["/servers/s1"]}>
      <AdminLayout>
        <h1>web-1</h1>
      </AdminLayout>
    </MemoryRouter>,
  );
  expect(await screen.findByRole("heading", { name: "web-1" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Servers" })).toBeNull();
  expect(screen.getByRole("link", { name: "Servers" })).toHaveAttribute("data-active", "true");
});

test("a page that asks for the full width gets it, and gives it back when it stops", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ is_admin: true }))),
  );
  function Wide({ on }: { on: boolean }) {
    useWidePage(on);
    return <div>Wide content</div>;
  }
  const { rerender } = render(
    <MemoryRouter initialEntries={["/documents/doc-1"]}>
      <AdminLayout>
        <Wide on={false} />
      </AdminLayout>
    </MemoryRouter>,
  );
  const content = (text: string) => screen.getByText(text).closest("main"); // the page's own main, inside the sidebar inset
  await screen.findByText("Wide content");
  expect(content("Wide content")).toHaveClass("max-w-6xl");
  rerender(
    <MemoryRouter initialEntries={["/documents/doc-1"]}>
      <AdminLayout>
        <Wide on />
      </AdminLayout>
    </MemoryRouter>,
  );
  expect(content("Wide content")).not.toHaveClass("max-w-6xl");
  rerender(
    <MemoryRouter initialEntries={["/documents/doc-1"]}>
      <AdminLayout>
        <div>Other page</div>
      </AdminLayout>
    </MemoryRouter>,
  );
  expect(content("Other page")).toHaveClass("max-w-6xl");
});

test("the home page opens Documents", () => {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route index element={<Home />} />
        <Route path="documents" element={<div>Documents page</div>} />
      </Routes>
    </MemoryRouter>,
  );
  expect(screen.getByText("Documents page")).toBeInTheDocument();
});

test("an agent's Chat tab keeps the Agents title and description visible", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ is_admin: true }))),
  );
  render(
    <MemoryRouter initialEntries={["/agents/code/chat"]}>
      <AdminLayout>
        <div>Agent chat</div>
      </AdminLayout>
    </MemoryRouter>,
  );
  expect(await screen.findByText("Agent chat")).toBeInTheDocument();
  const title = screen.getByRole("heading", { level: 1, name: "Agents" });
  expect(title.parentElement).not.toHaveClass("sr-only");
  expect(
    screen.getByText("Create agents with their own models, instructions, resources, and conversations."),
  ).toBeInTheDocument();
});
