import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import FileTree, { type FileTreeEntry } from "../FileTree";

const entries: FileTreeEntry[] = [
  { path: "docs", kind: "folder" },
  { path: "docs/payments", kind: "folder", detail: "1 file · 1 indexed" },
  { path: "docs/payments/readme.md", kind: "file" },
  { path: "docs/index.md", kind: "file" },
  { path: "empty", kind: "folder" },
];

test("arrows expand nested folders without selecting them; selecting a folder leaves it expanded", () => {
  const onSelect = vi.fn();
  render(<FileTree entries={entries} label="Documents" onSelect={onSelect} />);
  expect(screen.queryByRole("treeitem", { name: "payments" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Expand docs" }));
  expect(onSelect).not.toHaveBeenCalled();
  expect(screen.getAllByRole("treeitem").map((item) => item.getAttribute("aria-label")))
    .toEqual(["docs", "payments", "index.md", "empty"]);
  fireEvent.click(screen.getByRole("treeitem", { name: "docs" }));
  expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ path: "docs", kind: "folder" }));
  expect(screen.getByRole("treeitem", { name: "docs" })).toHaveAttribute("aria-expanded", "true");
  fireEvent.click(screen.getByRole("button", { name: "Expand docs/payments" }));
  fireEvent.click(screen.getByRole("treeitem", { name: "readme.md" }));
  expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ path: "docs/payments/readme.md", kind: "file" }));
  fireEvent.click(screen.getByRole("button", { name: "Collapse docs" }));
  expect(screen.queryByRole("treeitem", { name: "readme.md" })).toBeNull();
});

test("refreshing paths preserves expansion and removes deleted files and folders", () => {
  const onSelect = vi.fn();
  const { rerender } = render(<FileTree entries={entries} label="Documents" onSelect={onSelect} />);
  fireEvent.click(screen.getByRole("button", { name: "Expand docs" }));
  fireEvent.click(screen.getByRole("button", { name: "Expand docs/payments" }));
  const updated = entries.filter((entry) => entry.path !== "docs/payments/readme.md");
  updated.push({ path: "docs/payments/new.md", kind: "file" });
  rerender(<FileTree entries={updated} label="Documents" onSelect={onSelect} />);
  expect(screen.queryByRole("treeitem", { name: "readme.md" })).toBeNull();
  expect(screen.getByRole("treeitem", { name: "new.md" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("treeitem", { name: "new.md" }));
  rerender(<FileTree entries={[]} label="Documents" onSelect={onSelect} />);
  expect(screen.queryAllByRole("treeitem")).toHaveLength(0);
});

test("deep selection reveals ancestors once and still allows collapsing them across refreshes", () => {
  const onSelect = vi.fn();
  const props = { label: "Documents", onSelect, selectedPath: "docs/payments/readme.md" };
  const { rerender } = render(<FileTree {...props} entries={entries} />);
  expect(screen.getByRole("treeitem", { name: "readme.md" })).toHaveAttribute("aria-selected", "true");
  fireEvent.click(screen.getByRole("button", { name: "Collapse docs" }));
  rerender(<FileTree {...props} entries={[...entries]} />);
  expect(screen.queryByRole("treeitem", { name: "readme.md" })).toBeNull();
});

test("keyboard arrows navigate and expand folders, Enter opens the focused file", async () => {
  const user = userEvent.setup();
  const onSelect = vi.fn();
  render(<FileTree entries={entries} label="Documents" onSelect={onSelect} />);
  await user.tab();
  expect(screen.getByRole("treeitem", { name: "docs" })).toHaveFocus();
  await user.keyboard("{ArrowRight}{ArrowDown}");
  await waitFor(() => expect(screen.getByRole("treeitem", { name: "payments" })).toHaveFocus());
  await user.keyboard("{ArrowRight}{ArrowDown}");
  await waitFor(() => expect(screen.getByRole("treeitem", { name: "readme.md" })).toHaveFocus());
  await user.keyboard("{Enter}");
  expect(onSelect).toHaveBeenCalledTimes(1);
  expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ path: "docs/payments/readme.md" }));
  await user.keyboard("{ArrowLeft}");
  await waitFor(() => expect(screen.getByRole("treeitem", { name: "payments" })).toHaveFocus());
  await user.keyboard("{ArrowLeft}");
  expect(screen.queryByRole("treeitem", { name: "readme.md" })).toBeNull();
});

test("busy state blocks selection and keyboard expansion", async () => {
  const user = userEvent.setup();
  const onSelect = vi.fn();
  render(<FileTree entries={entries} label="Documents" onSelect={onSelect} disabled />);
  fireEvent.click(screen.getByRole("treeitem", { name: "docs" }));
  expect(screen.getByRole("button", { name: "Expand docs" })).toBeDisabled();
  await user.tab();
  await user.keyboard("{ArrowRight}{Enter}");
  expect(onSelect).not.toHaveBeenCalled();
  expect(screen.queryByRole("treeitem", { name: "payments" })).toBeNull();
});

test("missing intermediate folders are included and empty folders can expand", () => {
  render(<FileTree entries={[entries[2], entries[4]]} label="Documents" onSelect={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Expand docs" }));
  fireEvent.click(screen.getByRole("button", { name: "Expand docs/payments" }));
  expect(screen.getByRole("treeitem", { name: "readme.md" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Expand empty" }));
  expect(screen.getByRole("treeitem", { name: "empty" })).toHaveAttribute("aria-expanded", "true");
});
