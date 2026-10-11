import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import DocumentationPromptsView from "../DocumentationPromptsView";
import DocumentationPromptsRedirect from "../../routes/documentationPrompts";
import DocumentationPromptDetailRedirect from "../../routes/documentationPromptDetail";
import DocumentationPromptNewRedirect from "../../routes/documentationPromptNew";
import routes from "../../routes";

const prompts = [
  { id: "p1", name: "Discovery", description: "Look around", tags: ["server-discovery", "ssh"], enabled: true, active_version_id: "v1", active_version: 1, version_count: 1, revision: 1 },
  { id: "p2", name: "Runbook", description: "", tags: ["documentation", "service"], enabled: true, active_version_id: "v2", active_version: 1, version_count: 1, revision: 1 },
];
function api(routes: Record<string, unknown>) {
  const fetch = vi.fn(async (url: string) => {
    if (!(url in routes)) throw new Error(`unexpected ${url}`);
    return new Response(JSON.stringify(routes[url]), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
function Where() { const l = useLocation(); return <output data-testid="where">{l.pathname + l.search + l.hash}</output>; }
afterEach(() => vi.unstubAllGlobals());

test("lists prompts with their tags and filters by tag", async () => {
  const user = userEvent.setup();
  api({ "/api/documentation/prompts": { prompts }, "/api/documentation/prompts/tags": { tags: [{ tag: "documentation", count: 1 }, { tag: "server-discovery", count: 1 }, { tag: "service", count: 1 }, { tag: "ssh", count: 1 }] },
        "/api/documentation/prompts?tag=ssh": { prompts: [prompts[0]] } });
  render(<MemoryRouter initialEntries={["/prompts"]}><Routes><Route path="/prompts" element={<DocumentationPromptsView />} /></Routes></MemoryRouter>);
  const row = (await screen.findByRole("link", { name: "Discovery" })).closest("tr")!;
  expect(within(row).getByText("server-discovery")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Discovery" })).toHaveAttribute("href", "/prompts/p1");
  await user.click(screen.getByRole("combobox", { name: "Filter by tag" }));
  await user.click(screen.getByRole("option", { name: "ssh" }));
  await waitFor(() => expect(screen.queryByRole("link", { name: "Runbook" })).toBeNull());
});

test("old addresses redirect to the Prompts page", async () => {
  render(<MemoryRouter initialEntries={["/documentation/prompts/p1"]}><Routes>
    <Route path="/documentation/prompts" element={<DocumentationPromptsRedirect />} />
    <Route path="/documentation/prompts/:promptId" element={<DocumentationPromptDetailRedirect />} />
    <Route path="*" element={<Where />} /></Routes></MemoryRouter>);
  await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/prompts/p1"));
});

test("old addresses keep the query string and hash", async () => {
  render(<MemoryRouter initialEntries={["/documentation/prompts?tag=ssh#top"]}><Routes>
    <Route path="/documentation/prompts" element={<DocumentationPromptsRedirect />} />
    <Route path="*" element={<Where />} /></Routes></MemoryRouter>);
  await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/prompts?tag=ssh#top"));
});

test("the other old addresses keep the query string and hash", async () => {
  render(<MemoryRouter initialEntries={["/documentation/prompts/p1?x=1#v"]}><Routes>
    <Route path="/documentation/prompts/:promptId" element={<DocumentationPromptDetailRedirect />} />
    <Route path="*" element={<Where />} /></Routes></MemoryRouter>);
  await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/prompts/p1?x=1#v"));
  render(<MemoryRouter initialEntries={["/documentation/prompts/new?x=1#v"]}><Routes>
    <Route path="/documentation/prompts/new" element={<DocumentationPromptNewRedirect />} />
    <Route path="*" element={<Where />} /></Routes></MemoryRouter>);
  await waitFor(() => expect(screen.getAllByTestId("where")[1].textContent).toBe("/prompts/new?x=1#v"));
});

test("a tag filter that matches nothing says so", async () => {
  const user = userEvent.setup();
  api({ "/api/documentation/prompts": { prompts }, "/api/documentation/prompts/tags": { tags: [{ tag: "ssh", count: 1 }] },
        "/api/documentation/prompts?tag=ssh": { prompts: [] } });
  render(<MemoryRouter initialEntries={["/prompts"]}><Routes><Route path="/prompts" element={<DocumentationPromptsView />} /></Routes></MemoryRouter>);
  await screen.findByRole("link", { name: "Discovery" });
  await user.click(screen.getByRole("combobox", { name: "Filter by tag" }));
  await user.click(screen.getByRole("option", { name: "ssh" }));
  expect(await screen.findByText("No prompts have this tag.")).toBeInTheDocument();
  expect(screen.queryByText("No prompts yet")).toBeNull();
});

test("route ids are unique", () => {
  const ids: string[] = [];
  const walk = (list: any[]) => list.forEach((r) => { ids.push(r.id ?? r.file); if (r.children) walk(r.children); });
  walk(routes as any[]);
  // react-router derives the id from the file when none is given; two routes on one file need an explicit id.
  expect(ids.filter((id, i) => ids.indexOf(id) !== i)).toEqual([]);
});
