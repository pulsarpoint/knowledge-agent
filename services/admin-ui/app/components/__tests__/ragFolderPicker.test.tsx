import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RagFolderPicker from "../RagFolderPicker";

function api(add: () => Response = () => Response.json({ id: "c1", paths: [] })) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") return add();
    if (url === "/api/rag/folders") return Response.json({ path: null, truncated: false, folders: [{ name: "code", path: "code" }, { name: "jira", path: "jira" }] });
    if (url === "/api/rag/folders?path=code") return Response.json({ path: "code", truncated: false, folders: [{ name: "payments", path: "code/payments" }] });
    return Response.json({ path: "code/payments", truncated: false, folders: [] });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
const posted = (fetchMock: ReturnType<typeof api>) => fetchMock.mock.calls
  .filter(([, init]) => (init as RequestInit | undefined)?.method === "POST")
  .map(([url, init]) => [url, JSON.parse((init as RequestInit).body as string)]);
const picker = (onAdded = vi.fn()) => render(<RagFolderPicker open collection={{ id: "c1", name: "payments" }} onOpenChange={() => {}} onAdded={onAdded} />);

test("browse into a folder and connect it", async () => {
  const fetchMock = api();
  const onAdded = vi.fn();
  const user = userEvent.setup();
  picker(onAdded);
  await user.click(await screen.findByRole("button", { name: "code" }));
  await user.click(await screen.findByRole("button", { name: "payments" }));
  expect(screen.getByLabelText("Folder path")).toHaveValue("code/payments");
  expect(await screen.findByText("No subfolders.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Connect folder" }));
  await waitFor(() => expect(onAdded).toHaveBeenCalled());
  expect(posted(fetchMock)).toEqual([["/api/rag/collections/c1/paths", { source_path: "code/payments" }]]);
});

test("Up returns to the parent folder", async () => {
  const fetchMock = api();
  const user = userEvent.setup();
  picker();
  await user.click(await screen.findByRole("button", { name: "code" }));
  await screen.findByRole("button", { name: "payments" });
  await user.click(screen.getByRole("button", { name: "Up" }));
  expect(await screen.findByRole("button", { name: "jira" })).toBeInTheDocument();
  expect(screen.getByLabelText("Folder path")).toHaveValue("");
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(["/api/rag/folders", "/api/rag/folders?path=code", "/api/rag/folders"]);
});

test("starts at the DATA_ROOT top level and only connects existing folders", async () => {
  const fetchMock = api();
  picker();
  expect(await screen.findByRole("button", { name: "jira" })).toBeInTheDocument();
  expect(screen.getByText("DATA_ROOT")).toBeInTheDocument();
  expect(fetchMock.mock.calls[0][0]).toBe("/api/rag/folders");
  expect(screen.getByText(/The folder must already exist/)).toBeInTheDocument();
  expect(screen.queryByText(/rag-data|is created/)).toBeNull();
});

test("a typed path is trimmed of slashes", async () => {
  const fetchMock = api();
  const user = userEvent.setup();
  picker();
  await user.type(await screen.findByLabelText("Folder path"), " /jira/notes/ ");
  await user.click(screen.getByRole("button", { name: "Connect folder" }));
  await waitFor(() => expect(posted(fetchMock)).toEqual([["/api/rag/collections/c1/paths", { source_path: "jira/notes" }]]));
});

test("a refused folder shows the RAG message and nothing is added", async () => {
  api(() => Response.json({ error: { message: "Folder overlaps code/payments in collection payments" } }, { status: 409 }));
  const onAdded = vi.fn();
  const user = userEvent.setup();
  picker(onAdded);
  await user.type(await screen.findByLabelText("Folder path"), "code/payments/src");
  await user.click(screen.getByRole("button", { name: "Connect folder" }));
  expect(await screen.findByText("Folder overlaps code/payments in collection payments")).toBeInTheDocument();
  expect(onAdded).not.toHaveBeenCalled();
});

test("shows Loading… while a folder listing is in flight", async () => {
  api();
  picker();
  expect(screen.getByText("Loading…")).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: "code" })).toBeInTheDocument();
  expect(screen.queryByText("Loading…")).toBeNull();
});
