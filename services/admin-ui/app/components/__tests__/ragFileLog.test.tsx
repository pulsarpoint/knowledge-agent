import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RagFileLog from "~/features/rag/RagFileLog";
import type { LogFile } from "~/features/rag/api";

const indexed: LogFile = {
  path: "jira/DEMO-1.md",
  status: "indexed",
  size: 2048,
  mtime: 1,
  error: null,
  indexed_at: "2026-10-05T09:00:00Z",
  last_seen_at: "2026-10-05T09:00:00Z",
  removed_at: null,
  chunks: 13,
};
const failed: LogFile = {
  ...indexed,
  path: "jira/DEMO-2.md",
  status: "failed",
  indexed_at: null,
  chunks: 0,
  error: "Could not parse this file: unbalanced table",
};

function api(files: LogFile[] = [indexed, failed], total = files.length) {
  const fetchMock = vi.fn(async (_url: string) => Response.json({ files, total }));
  vi.stubGlobal("fetch", fetchMock);
  return () => fetchMock.mock.calls.map(([url]) => String(url));
}

test("shows each file's status, size, chunks and error; only indexed files open chunks", async () => {
  api();
  const onSelect = vi.fn();
  const user = userEvent.setup();
  render(<RagFileLog collectionId="c1" selected="" onSelect={onSelect} />);
  const bad = (await screen.findByText("jira/DEMO-2.md")).closest("tr")!;
  expect(within(bad).getByText("Failed")).toBeInTheDocument();
  expect(within(bad).getByText("Could not parse this file: unbalanced table")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "jira/DEMO-2.md" })).toBeNull();
  const ok = screen.getByRole("button", { name: "jira/DEMO-1.md" });
  const row = ok.closest("tr")!;
  expect(within(row).getByText("Indexed")).toBeInTheDocument();
  expect(within(row).getByText("2.0 KB")).toBeInTheDocument();
  expect(within(row).getByText("13")).toBeInTheDocument();
  await user.click(ok);
  expect(onSelect).toHaveBeenCalledWith("jira/DEMO-1.md");
});

test("the first request asks for every file except removed ones, page one", async () => {
  const urls = api();
  render(<RagFileLog collectionId="c1" selected="" onSelect={() => {}} />);
  await screen.findByText("Showing 1–2 of 2");
  expect(urls()[0]).toBe("/api/rag/collections/c1/log?limit=25&offset=0");
});

test("paging, the status filter and the path search are sent to the API", async () => {
  const urls = api([indexed], 60);
  const user = userEvent.setup();
  render(<RagFileLog collectionId="c1" selected="" onSelect={() => {}} />);
  await screen.findByText("Showing 1–25 of 60");
  await user.click(screen.getByRole("button", { name: "Next page" }));
  await waitFor(() => expect(urls().at(-1)).toBe("/api/rag/collections/c1/log?limit=25&offset=25"));
  await user.click(screen.getByRole("combobox", { name: "File status" }));
  await user.click(screen.getByRole("option", { name: "Failed" }));
  await waitFor(() => expect(urls().at(-1)).toBe("/api/rag/collections/c1/log?status=failed&limit=25&offset=0"));
  await user.type(screen.getByRole("searchbox", { name: "Search files" }), "DEMO");
  await waitFor(() => expect(urls().at(-1)).toBe("/api/rag/collections/c1/log?status=failed&q=DEMO&limit=25&offset=0"));
});

test("an empty log explains how to fill it", async () => {
  api([], 0);
  render(<RagFileLog collectionId="c1" selected="" onSelect={() => {}} />);
  expect(await screen.findByText("No files yet. Refresh the collection to index its folders.")).toBeInTheDocument();
});
