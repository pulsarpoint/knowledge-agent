import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import SkillEditor from "~/features/agents/SkillEditor";
import type { Skill } from "~/features/agents/skills";

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

const SKILL_MD = "---\nname: pdf-forms\ndescription: Fill PDF forms.\n---\n\n# Steps\n";
const skill: Skill = {
  id: "s1",
  name: "pdf-forms",
  description: "Fill PDF forms.",
  enabled: true,
  revision: 2,
  source: { kind: "upload", file: "pdf.zip" },
  folder: "skills/s1/20261025T100000Z",
  version: "20261025T100000Z",
  created_at: "2026-10-24T09:00:00Z",
  updated_at: "2026-10-25T10:00:00Z",
  files: [
    { path: "SKILL.md", size: SKILL_MD.length, executable: false },
    { path: "logo.png", size: 2048, executable: false },
    { path: "scripts/run.sh", size: 10, executable: true },
  ],
  versions: [
    {
      version: "20261025T100000Z",
      created_at: "2026-10-25T10:00:00Z",
      source: { kind: "upload", file: "pdf.zip" },
      folder: "skills/s1/20261025T100000Z",
      current: true,
    },
    {
      version: "20261024T090000Z",
      created_at: "2026-10-24T09:00:00Z",
      source: { kind: "editor" },
      folder: "skills/s1/20261024T090000Z",
      current: false,
    },
  ],
};

type Route = (body: unknown) => [number, unknown];
function api(routes: Record<string, Route>) {
  const calls: { key: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${url}`;
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : null;
      calls.push({ key, body });
      if (!(key in routes)) throw new Error(`unexpected ${key}`);
      const [status, value] = routes[key](body);
      return new Response(JSON.stringify(value), { status });
    }),
  );
  return calls;
}
const files = {
  "GET /api/skills/s1": (): [number, unknown] => [200, skill],
  "GET /api/skills/s1/files/SKILL.md": (): [number, unknown] => [
    200,
    { path: "SKILL.md", size: SKILL_MD.length, executable: false, text: SKILL_MD },
  ],
  "GET /api/skills/s1/files/logo.png": (): [number, unknown] => [
    200,
    { path: "logo.png", size: 2048, executable: false, text: null },
  ],
  "GET /api/skills/s1/files/scripts/run.sh": (): [number, unknown] => [
    200,
    { path: "scripts/run.sh", size: 10, executable: true, text: "echo run\n" },
  ],
};
function show() {
  const router = createMemoryRouter(
    [
      { path: "/skills", element: <p>All skills page</p> },
      { path: "/skills/:skillId", element: <SkillEditor /> },
    ],
    { initialEntries: ["/skills/s1"] },
  );
  render(<RouterProvider router={router} />);
  return router;
}
const editor = () => screen.getByLabelText(/^Contents of /);
async function name(label: string, value: string) {
  const input = await screen.findByLabelText(label);
  fireEvent.change(input, { target: { value } });
  fireEvent.submit(input.closest("form")!);
}

test("edit, add, upload, rename and delete in the tree; Save sends the changes and shows the new version", async () => {
  const saved = {
    ...skill,
    revision: 3,
    version: "20261025T110000Z",
    source: { kind: "editor" },
    files: [{ path: "SKILL.md", size: 10, executable: false }],
  };
  const calls = api({
    ...files,
    "PUT /api/skills/s1/files": () => [200, { skill: saved, changed: [{ path: "SKILL.md", change: "modified" }] }],
  });
  show();
  await waitFor(() => expect(editor()).toHaveValue(SKILL_MD));
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

  fireEvent.change(editor(), { target: { value: SKILL_MD.replace("pdf-forms", "PDF") } });
  expect(screen.getByText(/name must be 1-64 lowercase letters/)).toBeInTheDocument();
  fireEvent.change(editor(), { target: { value: SKILL_MD.replace("# Steps", "# Fill") } });
  expect(screen.getByText("Frontmatter OK: pdf-forms")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Add file" }));
  await name("New file path", "references/guide.md");
  await waitFor(() => expect(editor()).toHaveAccessibleName("Contents of references/guide.md"));
  fireEvent.change(editor(), { target: { value: "Guide" } });

  fireEvent.click(screen.getByRole("button", { name: "Add folder" }));
  await name("New folder path", "assets");
  fireEvent.click(await screen.findByRole("button", { name: "assets" }));
  fireEvent.change(screen.getByLabelText("Upload files"), {
    target: { files: [new File(["PNG"], "icon.png", { type: "image/png" })] },
  });
  expect(await screen.findByRole("button", { name: "assets/icon.png" })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "scripts/run.sh" }));
  fireEvent.click(screen.getByRole("button", { name: "Rename" }));
  await name("New path", "scripts/fill.sh");
  expect(await screen.findByRole("button", { name: "scripts/fill.sh" })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "logo.png" }));
  await screen.findByText("2.0 KB");
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(within(dialog).getByText("Delete “logo.png”?")).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "Delete file" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "logo.png" })).not.toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("Saved version 20261025T110000Z (Edited in the app). Changed: SKILL.md (modified).");
  expect(calls.find((call) => call.key === "PUT /api/skills/s1/files")!.body).toEqual({
    revision: 2,
    changes: [
      { path: "logo.png", delete: true },
      { path: "scripts/run.sh", rename_to: ".renaming-0" },
      { path: ".renaming-0", rename_to: "scripts/fill.sh" },
      { path: "SKILL.md", text: SKILL_MD.replace("# Steps", "# Fill") },
      { path: "references/guide.md", text: "Guide" },
      { path: "assets/icon.png", content_base64: "UE5H" },
    ],
  });
  expect(screen.getByText("20261025T110000Z")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
});

test("deleting a folder asks first, naming the folder and how many files go", async () => {
  api(files);
  show();
  fireEvent.click(await screen.findByRole("button", { name: "scripts" }));
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(within(dialog).getByText("Delete folder “scripts” and its 1 file?")).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(screen.getByRole("button", { name: "scripts/run.sh" })).toBeInTheDocument();
});

test("a stale save keeps the edits and shows why", async () => {
  api({
    ...files,
    "PUT /api/skills/s1/files": () => [
      409,
      { error: { message: "This skill changed since you opened it. Reopen it before saving." } },
    ],
  });
  show();
  await waitFor(() => expect(editor()).toHaveValue(SKILL_MD));
  fireEvent.change(editor(), { target: { value: SKILL_MD + "more\n" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("This skill changed since you opened it. Reopen it before saving.");
  expect(editor()).toHaveValue(SKILL_MD + "more\n");
});

test("Previous versions lists the kept folders in a sheet; Restore asks first", async () => {
  const calls = api({
    ...files,
    "POST /api/skills/s1/restore": () => [
      200,
      { ...skill, revision: 3, version: "20261024T090000Z", source: { kind: "editor" } },
    ],
  });
  show();
  await waitFor(() => expect(editor()).toHaveValue(SKILL_MD));
  fireEvent.click(screen.getByRole("button", { name: "Previous versions" }));
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByText("Current")).toBeInTheDocument();
  expect(within(sheet).getByText("Uploaded pdf.zip")).toBeInTheDocument();
  expect(within(sheet).getAllByRole("button", { name: /^Restore/ })).toHaveLength(1);
  fireEvent.click(within(sheet).getByRole("button", { name: "Restore 20261024T090000Z" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(within(dialog).getByText("Restore version 20261024T090000Z?")).toBeInTheDocument();
  expect(within(dialog).getByText(/replaces the current files/)).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "Restore" }));
  await screen.findByText("Restored version 20261024T090000Z.");
  expect(calls.find((call) => call.key === "POST /api/skills/s1/restore")!.body).toEqual({
    revision: 2,
    version: "20261024T090000Z",
  });
});

test("leaving with unsaved changes asks first", async () => {
  api(files);
  const router = show();
  await waitFor(() => expect(editor()).toHaveValue(SKILL_MD));
  fireEvent.change(editor(), { target: { value: SKILL_MD + "x" } });
  fireEvent.click(screen.getByRole("link", { name: "All skills" }));
  const dialog = await screen.findByRole("alertdialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Stay" }));
  expect(router.state.location.pathname).toBe("/skills/s1");
  expect(editor()).toHaveValue(SKILL_MD + "x");
});
