import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import SkillsView from "~/features/agents/SkillsView";
import { Toaster } from "~/components/ui/sonner";
import {
  checkSkillMd,
  fileChanges,
  githubTree,
  sourceLabel,
  treeRows,
  type SkillSummary,
} from "~/features/agents/skills";

const NativeRequest = globalThis.Request;
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

const at = "2026-10-25T10:00:00Z";
const pdf: SkillSummary = {
  id: "s1",
  name: "pdf-forms",
  description: "Fill PDF forms.",
  enabled: true,
  revision: 2,
  source: {
    kind: "git",
    url: "https://github.com/acme/skills",
    ref: "main",
    path: "pdf-forms",
    commit: "abc123def4567890",
  },
  folder: "skills/s1/abc123def456",
  version: "abc123def456",
  created_at: at,
  updated_at: at,
};
const notes: SkillSummary = {
  ...pdf,
  id: "s2",
  name: "notes",
  description: "Write notes.",
  revision: 1,
  source: { kind: "upload", file: "notes.zip" },
  folder: "skills/s2/20261025T100000Z",
  version: "20261025T100000Z",
};

type Route = (body: unknown) => [number, unknown];
function api(routes: Record<string, Route>) {
  const calls: { key: string; body: unknown; headers?: HeadersInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${url}`;
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : (init?.body ?? null);
      calls.push({ key, body, headers: init?.headers });
      if (!(key in routes)) throw new Error(`unexpected ${key}`);
      const [status, value] = routes[key](body);
      return status === 204 ? new Response(null, { status }) : new Response(JSON.stringify(value), { status });
    }),
  );
  return calls;
}
function show() {
  const router = createMemoryRouter(
    [
      { path: "/skills", element: <SkillsView /> },
      { path: "/skills/:skillId", element: <p>Editor</p> },
    ],
    { initialEntries: ["/skills"] },
  );
  render(
    <>
      <RouterProvider router={router} />
      <Toaster />
    </>,
  );
  return router;
}

test("the page says every agent sees every enabled skill and each row has its enabled switch", async () => {
  api({ "GET /api/skills": () => [200, { skills: [notes, { ...pdf, enabled: false }] }] });
  show();
  expect(await screen.findByText("Every agent sees every enabled skill; disable a skill to hide it.")).toBeVisible();
  expect(screen.getByRole("switch", { name: "notes enabled" })).toBeChecked();
  expect(screen.getByRole("switch", { name: "pdf-forms enabled" })).not.toBeChecked();
});

test("the list shows each skill's version and source; Update from source only for git skills", async () => {
  const user = userEvent.setup();
  const calls = api({
    "GET /api/skills": () => [200, { skills: [notes, pdf] }],
    "POST /api/skills/s1/update/source": () => [
      200,
      {
        skill: { ...pdf, version: "fff000111222", revision: 3, files: [], versions: [] },
        changed: [
          { path: "ref.md", change: "added" },
          { path: "SKILL.md", change: "modified" },
        ],
      },
    ],
  });
  show();
  const row = (await screen.findByText("pdf-forms")).closest("tr")!;
  expect(within(row).getByText("abc123def456")).toBeInTheDocument();
  expect(within(row).getByText("Git: https://github.com/acme/skills @ main, pdf-forms")).toBeInTheDocument();
  const other = screen.getByText("notes").closest("tr")!;
  expect(within(other).getByText("Uploaded notes.zip")).toBeInTheDocument();
  // Each row has one actions menu instead of a row of buttons; the name opens the skill.
  expect(within(row).getAllByRole("button")).toHaveLength(1);
  expect(within(other).getByRole("link", { name: "notes" })).toHaveAttribute("href", "/skills/s2");
  await user.click(within(other).getByRole("button", { name: "Actions for notes" }));
  expect(screen.getByRole("menuitem", { name: "Edit" })).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: "Update from upload…" })).toBeInTheDocument();
  expect(screen.queryByRole("menuitem", { name: "Update from source" })).not.toBeInTheDocument();
  await user.keyboard("{Escape}");
  await user.click(within(row).getByRole("button", { name: "Actions for pdf-forms" }));
  await user.click(screen.getByRole("menuitem", { name: "Update from source" }));
  await screen.findByText("pdf-forms updated to version fff000111222. Changed: ref.md (added), SKILL.md (modified).");
  expect(calls.find((call) => call.key === "POST /api/skills/s1/update/source")!.body).toEqual({ revision: 2 });
});

test("a zip import previews the skills it holds, then imports them on confirm", async () => {
  const calls = api({
    "GET /api/skills": () => [200, { skills: [] }],
    "POST /api/skills/import/zip?file=all.zip&preview=true": () => [
      200,
      {
        skills: [
          { name: "docx", description: "Word files.", files: [{ path: "SKILL.md", size: 10 }] },
          { name: "pdf", description: "PDF files.", files: [{ path: "SKILL.md", size: 10 }] },
        ],
      },
    ],
    "POST /api/skills/import/zip?file=all.zip": () => [
      201,
      {
        skills: [
          { ...pdf, name: "docx", files: [], versions: [] },
          { ...pdf, name: "pdf", files: [], versions: [] },
        ],
      },
    ],
  });
  show();
  await screen.findByText("No skills yet");
  fireEvent.click(screen.getByRole("button", { name: "Import" }));
  const file = new File(["PK"], "all.zip", { type: "application/zip" });
  fireEvent.change(await screen.findByLabelText("Zip file"), { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: "Check archive" }));
  expect(await screen.findByText("docx")).toBeInTheDocument();
  expect(screen.getByText("PDF files.")).toBeInTheDocument();
  const preview = calls.find((call) => call.key.includes("preview=true"))!;
  expect(preview.body).toBe(file);
  expect(preview.headers).toEqual({ "Content-Type": "application/zip" });
  fireEvent.click(screen.getByRole("button", { name: "Import 2 skills" }));
  await screen.findByText("Imported docx, pdf.");
  expect(calls.map((call) => call.key)).toContain("POST /api/skills/import/zip?file=all.zip");
});

test("a git import sends the URL, ref and path, previews, then imports", async () => {
  const calls = api({
    "GET /api/skills": () => [200, { skills: [] }],
    "POST /api/skills/import/git": (body) => {
      const value = body as { preview?: boolean };
      return value.preview
        ? [200, { skills: [{ name: "pdf-forms", description: "Fill PDF forms.", files: [] }] }]
        : [201, { skills: [{ ...pdf, files: [], versions: [] }] }];
    },
  });
  show();
  await screen.findByText("No skills yet");
  fireEvent.click(screen.getByRole("button", { name: "Import" }));
  fireEvent.mouseDown(await screen.findByRole("tab", { name: "From git" }));
  fireEvent.change(screen.getByLabelText("Repository URL"), { target: { value: "https://github.com/acme/skills" } });
  fireEvent.change(screen.getByLabelText("Ref"), { target: { value: "main" } });
  fireEvent.change(screen.getByLabelText("Path"), { target: { value: "pdf-forms" } });
  fireEvent.click(screen.getByRole("button", { name: "Check repository" }));
  await screen.findByText("Fill PDF forms.");
  fireEvent.click(screen.getByRole("button", { name: "Import 1 skill" }));
  await screen.findByText("Imported pdf-forms.");
  expect(calls.filter((call) => call.key === "POST /api/skills/import/git").map((call) => call.body)).toEqual([
    { url: "https://github.com/acme/skills", ref: "main", path: "pdf-forms", preview: true },
    { url: "https://github.com/acme/skills", ref: "main", path: "pdf-forms" },
  ]);
});

test("New skill creates SKILL.md from the template and opens the editor", async () => {
  const calls = api({
    "GET /api/skills": () => [200, { skills: [] }],
    "POST /api/skills": () => [201, { ...pdf, id: "s9", files: [], versions: [] }],
  });
  const router = show();
  await screen.findByText("No skills yet");
  // the empty state repeats New skill as its first-item button
  expect(screen.getAllByRole("button", { name: "New skill" })).toHaveLength(2);
  fireEvent.click(screen.getAllByRole("button", { name: "New skill" })[1]);
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "pdf-forms" } });
  fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Fill PDF forms." } });
  fireEvent.click(screen.getByRole("button", { name: "Create and edit" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/skills/s9"));
  expect(calls.find((call) => call.key === "POST /api/skills")!.body).toEqual({
    name: "pdf-forms",
    description: "Fill PDF forms.",
  });
});

test("Delete asks first, naming the skill and its files", async () => {
  const user = userEvent.setup();
  const calls = api({
    "GET /api/skills": () => [200, { skills: [pdf] }],
    "DELETE /api/skills/s1": () => [204, null],
  });
  show();
  await user.click(await screen.findByRole("button", { name: "Actions for pdf-forms" }));
  await user.click(screen.getByRole("menuitem", { name: "Delete" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(within(dialog).getByText("Delete “pdf-forms”?")).toBeInTheDocument();
  expect(calls.some((call) => call.key.startsWith("DELETE"))).toBe(false);
  fireEvent.click(within(dialog).getByRole("button", { name: "Delete skill" }));
  await screen.findByText("Deleted pdf-forms.");
  expect(calls.map((call) => call.key)).toContain("DELETE /api/skills/s1");
});

test("unavailable skill storage shows the error and disables New skill and Import", async () => {
  api({ "GET /api/skills": () => [503, { error: { message: "The skills folder is unavailable." } }] });
  show();
  await screen.findByText("The skills folder is unavailable.");
  expect(screen.getByRole("button", { name: "New skill" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Import" })).toBeDisabled();
});

test("the skill list: Skeleton rows while loading, search, sort by a header and 20 rows a page", async () => {
  const user = userEvent.setup();
  const many = Array.from({ length: 22 }, (_, i) => ({
    ...notes,
    id: `n${i}`,
    name: `skill-${String(i).padStart(2, "0")}`,
    description: i === 7 ? "Reads invoices." : "Notes.",
  }));
  api({ "GET /api/skills": () => [200, { skills: many }] });
  show();
  expect(screen.getAllByTestId("skeleton-row").length).toBeGreaterThan(0);
  await screen.findByRole("link", { name: "skill-00" });
  const names = () => screen.getAllByRole("link").map((link) => link.textContent);
  expect(names()).toHaveLength(20);
  await user.click(screen.getByRole("button", { name: "Skill" }));
  expect(names()[0]).toBe("skill-21");
  await user.click(screen.getByRole("button", { name: "Next page" }));
  expect(names()).toEqual(["skill-01", "skill-00"]);
  await user.type(screen.getByRole("searchbox", { name: "Search skills" }), "invoices");
  expect(names()).toEqual(["skill-07"]);
  await user.clear(screen.getByRole("searchbox", { name: "Search skills" }));
  await user.type(screen.getByRole("searchbox", { name: "Search skills" }), "nothing");
  expect(screen.getByText("No skills match.")).toBeInTheDocument();
});

test("sourceLabel names where a skill came from", () => {
  expect(sourceLabel(pdf.source)).toBe("Git: https://github.com/acme/skills @ main, pdf-forms");
  expect(sourceLabel({ kind: "git", url: "https://x/y", ref: "", path: "", commit: "c" })).toBe("Git: https://x/y");
  expect(sourceLabel({ kind: "upload", file: "" })).toBe("Uploaded zip");
  expect(sourceLabel({ kind: "editor" })).toBe("Edited in the app");
  expect(sourceLabel(null)).toBe("Unknown");
});

test("checkSkillMd checks the frontmatter's name and description as the server does", () => {
  expect(checkSkillMd("---\nname: pdf-forms\ndescription: Fill PDF forms.\n---\n\n# Steps\n")).toBeNull();
  expect(checkSkillMd("---\nname: 'pdf-forms'\ndescription: \"Fill forms.\"\n---\n")).toBeNull();
  expect(checkSkillMd("---\nname: pdf\ndescription: >-\n  Fill forms\n  of all kinds.\n---\n")).toBeNull();
  expect(checkSkillMd("# no frontmatter")).toBe("SKILL.md must start with a frontmatter block between --- lines.");
  expect(checkSkillMd("---\nname: pdf\n")).toBe("SKILL.md must start with a frontmatter block between --- lines.");
  expect(checkSkillMd("---\nname: PDF Forms\ndescription: x\n---\n")).toBe(
    "SKILL.md's name must be 1-64 lowercase letters, digits and single hyphens, such as pdf-forms.",
  );
  expect(checkSkillMd("---\nname: pdf\n---\n")).toBe("SKILL.md's description must be text of 1-1024 characters.");
  expect(checkSkillMd(`---\nname: pdf\ndescription: ${"x".repeat(1025)}\n---\n`)).toBe(
    "SKILL.md's description must be text of 1-1024 characters.",
  );
});

test("treeRows lists folders before files, each under its parent", () => {
  expect(treeRows(["SKILL.md", "scripts/run.sh", "a.md", "scripts/lib/x.py"], ["assets"])).toEqual([
    { path: "assets", name: "assets", depth: 0, kind: "folder" },
    { path: "scripts", name: "scripts", depth: 0, kind: "folder" },
    { path: "scripts/lib", name: "lib", depth: 1, kind: "folder" },
    { path: "scripts/lib/x.py", name: "x.py", depth: 2, kind: "file" },
    { path: "scripts/run.sh", name: "run.sh", depth: 1, kind: "file" },
    { path: "a.md", name: "a.md", depth: 0, kind: "file" },
    { path: "SKILL.md", name: "SKILL.md", depth: 0, kind: "file" },
  ]);
});

test("fileChanges turns the working copy into the save's changes", () => {
  const saved = ["SKILL.md", "old.md", "a.md", "b.md", "logo.png"];
  expect(
    fileChanges(saved, {
      "SKILL.md": { origin: "SKILL.md", size: 5, text: "new", saved: "old" },
      // a.md and b.md swapped names; logo.png renamed without being opened; old.md deleted
      "b.md": { origin: "a.md", size: 1 },
      "a.md": { origin: "b.md", size: 1, text: "B2", saved: "B" },
      "img/logo.png": { origin: "logo.png", size: 9 },
      "new.md": { origin: null, size: 0, text: "" },
      "up.bin": { origin: null, size: 2, base64: "AAE=" },
    }),
  ).toEqual([
    { path: "old.md", delete: true },
    { path: "a.md", rename_to: ".renaming-0" },
    { path: "b.md", rename_to: ".renaming-1" },
    { path: "logo.png", rename_to: ".renaming-2" },
    { path: ".renaming-0", rename_to: "b.md" },
    { path: ".renaming-1", rename_to: "a.md" },
    { path: ".renaming-2", rename_to: "img/logo.png" },
    { path: "SKILL.md", text: "new" },
    { path: "a.md", text: "B2" },
    { path: "new.md", text: "" },
    { path: "up.bin", content_base64: "AAE=" },
  ]);
  expect(fileChanges(["SKILL.md"], { "SKILL.md": { origin: "SKILL.md", size: 3, text: "x", saved: "x" } })).toEqual([]);
});

test("Update from upload is offered for every skill: it sends the zip and shows the new version", async () => {
  const calls = api({
    "GET /api/skills": () => [200, { skills: [notes, pdf] }],
    "POST /api/skills/s2/update/upload?file=notes2.zip&revision=1": () => [
      200,
      {
        skill: { ...notes, version: "20261026T100000Z", revision: 2, files: [], versions: [] },
        changed: [{ path: "SKILL.md", change: "modified" }],
      },
    ],
  });
  show();
  const row = (await screen.findByText("notes")).closest("tr")!;
  expect(within(screen.getByText("pdf-forms").closest("tr")!).getByLabelText("File for pdf-forms")).toBeInTheDocument();
  const zip = new File(["zip"], "notes2.zip", { type: "application/zip" });
  fireEvent.change(within(row).getByLabelText("File for notes"), { target: { files: [zip] } });
  await screen.findByText("notes updated to version 20261026T100000Z. Changed: SKILL.md (modified).");
  const call = calls.find((item) => item.key.startsWith("POST /api/skills/s2/update/upload"))!;
  expect(call.body).toBe(zip);
});

test("githubTree splits a pasted GitHub tree URL into URL, ref and path", () => {
  expect(githubTree("https://github.com/acme/skills/tree/main/skills/pdf-forms")).toEqual({
    url: "https://github.com/acme/skills",
    ref: "main",
    path: "skills/pdf-forms",
  });
  expect(githubTree(" https://github.com/acme/skills/tree/v1/ ")).toEqual({
    url: "https://github.com/acme/skills",
    ref: "v1",
    path: "",
  });
  expect(githubTree("https://github.com/acme/skills")).toBeNull();
  expect(githubTree("https://gitlab.com/acme/skills/tree/main/x")).toBeNull();
});

test("pasting a GitHub tree URL fills the URL, Ref and Path fields", async () => {
  api({ "GET /api/skills": () => [200, { skills: [] }] });
  show();
  await screen.findByText("No skills yet");
  fireEvent.click(screen.getByRole("button", { name: "Import" }));
  fireEvent.mouseDown(await screen.findByRole("tab", { name: "From git" }));
  fireEvent.change(screen.getByLabelText("Repository URL"), {
    target: { value: "https://github.com/acme/skills/tree/main/skills/pdf-forms" },
  });
  expect(screen.getByLabelText("Repository URL")).toHaveValue("https://github.com/acme/skills");
  expect(screen.getByLabelText("Ref")).toHaveValue("main");
  expect(screen.getByLabelText("Path")).toHaveValue("skills/pdf-forms");
});

test.each([
  ["New skill", "New skill", "Name", "pdf-forms"],
  ["Import", "Import skills", "Repository URL", "https://example.com/skills.git"],
])(
  "%s: Escape with input asks Stay / Leave; Leave discards it, a clean sheet closes at once",
  async (open, title, label, text) => {
    api({ "GET /api/skills": () => [200, { skills: [] }] });
    show();
    await screen.findByText("No skills yet");
    const openSheet = async () => {
      fireEvent.click(screen.getAllByRole("button", { name: open })[0]);
      return within(await screen.findByRole("dialog", { name: title }));
    };
    let sheet = await openSheet();
    fireEvent.keyDown(sheet.getByRole("heading", { name: title }), { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: title })).toBeNull());
    expect(screen.queryByRole("alertdialog")).toBeNull();
    sheet = await openSheet();
    if (label === "Repository URL") fireEvent.mouseDown(sheet.getByRole("tab", { name: "From git" }));
    fireEvent.change(await sheet.findByLabelText(label), { target: { value: text } });
    fireEvent.keyDown(sheet.getByLabelText(label), { key: "Escape" });
    const ask = within(await screen.findByRole("alertdialog"));
    fireEvent.click(ask.getByRole("button", { name: "Stay" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(screen.getByLabelText(label)).toHaveValue(text);
    fireEvent.keyDown(screen.getByLabelText(label), { key: "Escape" });
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Leave" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: title })).toBeNull());
    // opened again, the sheet is empty
    sheet = await openSheet();
    if (label === "Repository URL") fireEvent.mouseDown(sheet.getByRole("tab", { name: "From git" }));
    expect(await sheet.findByLabelText(label)).toHaveValue("");
  },
);
