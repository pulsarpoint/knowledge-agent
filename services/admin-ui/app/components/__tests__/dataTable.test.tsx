import { useState } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ColumnDef } from "@tanstack/react-table";
import { Bot } from "lucide-react";
import DataTable, { nextSort, sortHeader, sortRows, TableEmpty, type Sort } from "../DataTable";
import { Button } from "~/components/ui/button";

type Row = { id: string; name: string; size: number };
const rows = (count: number): Row[] =>
  Array.from({ length: count }, (_, i) => ({ id: `r${i + 1}`, name: `Row ${i + 1}`, size: count - i }));
const columns: ColumnDef<Row, unknown>[] = [
  { id: "name", header: "Name", cell: ({ row }) => row.original.name },
  { id: "size", header: "Size", cell: ({ row }) => row.original.size },
];
const shown = () =>
  screen
    .getAllByRole("row")
    .slice(1)
    .map((row) => within(row).getAllByRole("cell")[0].textContent);

test("nextSort: the same column turns round, another column sorts ascending", () => {
  expect(nextSort({ key: "name", descending: false }, "name")).toEqual({ key: "name", descending: true });
  expect(nextSort({ key: "name", descending: true }, "name")).toEqual({ key: "name", descending: false });
  expect(nextSort({ key: "name", descending: true }, "size")).toEqual({ key: "size", descending: false });
});

test("sortRows: by the chosen compare, reversed when descending, without changing the input", () => {
  const input = rows(3);
  const compare = { name: (a: Row, b: Row) => a.name.localeCompare(b.name), size: (a: Row, b: Row) => a.size - b.size };
  expect(sortRows(input, { key: "size", descending: false }, compare).map((r) => r.id)).toEqual(["r3", "r2", "r1"]);
  expect(sortRows(input, { key: "name", descending: true }, compare).map((r) => r.id)).toEqual(["r3", "r2", "r1"]);
  expect(input.map((r) => r.id)).toEqual(["r1", "r2", "r3"]);
});

function Sorting() {
  const [sort, setSort] = useState<Sort<"name" | "size">>({ key: "name", descending: false });
  return (
    <>
      {sortHeader("Name", "name", sort, setSort)}
      {sortHeader("Size", "size", sort, setSort)}
      <span>
        {sort.key} {sort.descending ? "down" : "up"}
      </span>
    </>
  );
}

test("sortHeader: a button with the label and an arrow; a click sorts by it, again turns it round", async () => {
  render(<Sorting />);
  const name = screen.getByRole("button", { name: "Name" });
  expect(name.querySelector("svg")).not.toBeNull();
  await userEvent.click(name);
  expect(screen.getByText("name down")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Size" }));
  expect(screen.getByText("size up")).toBeInTheDocument();
});

test("the toolbar search: one unlabelled search field named and hinted Search <plural>, then the page's filters", async () => {
  const onChange = vi.fn();
  render(
    <DataTable
      columns={columns}
      data={rows(2)}
      empty="None"
      search={{ plural: "agents", value: "", onChange }}
      toolbar={<button type="button">A filter</button>}
    />,
  );
  const search = screen.getByRole("searchbox", { name: "Search agents" });
  expect(search).toHaveAttribute("type", "search");
  expect(search).toHaveAttribute("placeholder", "Search agents");
  expect(screen.getByRole("button", { name: "A filter" })).toBeInTheDocument();
  await userEvent.type(search, "x");
  expect(onChange).toHaveBeenCalledWith("x");
});

test("the actions slot: the page's actions at the right end of the one toolbar row, after search and filters", () => {
  render(
    <DataTable
      columns={columns}
      data={rows(2)}
      empty="None"
      search={{ plural: "agents", value: "", onChange: () => {} }}
      toolbar={<button type="button">A filter</button>}
      actions={<button type="button">New agent</button>}
    />,
  );
  const row = screen.getByRole("searchbox", { name: "Search agents" }).closest("[data-slot='table-toolbar']")!;
  expect(row).toHaveClass("flex-wrap", "items-center");
  const action = screen.getByRole("button", { name: "New agent" });
  expect(row).toContainElement(screen.getByRole("button", { name: "A filter" }));
  expect(row).toContainElement(action);
  expect(action.parentElement).toHaveClass("ml-auto", "items-center");
  // The filter comes before the actions.
  const filter = screen.getByRole("button", { name: "A filter" });
  expect(filter.compareDocumentPosition(action) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test("actions alone still make the toolbar row", () => {
  render(<DataTable columns={columns} data={[]} empty="None" actions={<button type="button">Refresh</button>} />);
  expect(screen.getByRole("button", { name: "Refresh" }).closest("[data-slot='table-toolbar']")).not.toBeNull();
});

test("loading with no rows shows Skeleton rows, not the empty state", () => {
  render(<DataTable columns={columns} data={[]} empty="No rows yet" loading />);
  expect(screen.getAllByTestId("skeleton-row")).toHaveLength(3);
  expect(screen.queryByText("No rows yet")).not.toBeInTheDocument();
  expect(screen.queryByText(/Showing/)).not.toBeInTheDocument();
});

test("an empty list shows the Empty element: icon, action title, line and the first-item button", () => {
  render(
    <DataTable
      columns={columns}
      data={[]}
      empty={
        <TableEmpty icon={Bot} title="No agents yet" description="Agents answer questions.">
          <Button>New agent</Button>
        </TableEmpty>
      }
    />,
  );
  expect(screen.queryAllByTestId("skeleton-row")).toHaveLength(0);
  expect(screen.getByText("No agents yet")).toBeInTheDocument();
  expect(screen.getByText("Agents answer questions.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "New agent" })).toBeInTheDocument();
  expect(document.querySelector("[data-slot=empty-icon] svg")).not.toBeNull();
});

function ClientList({ count }: { count: number }) {
  const [search, setSearch] = useState("");
  const [data, setData] = useState(rows(count));
  return (
    <>
      <button type="button" onClick={() => setData((d) => d.slice(0, 3))}>
        Keep three
      </button>
      <DataTable
        columns={columns}
        data={data.filter((r) => r.name.includes(search))}
        empty="No rows match."
        search={{ plural: "rows", value: search, onChange: setSearch }}
      />
    </>
  );
}

test("client-side paging: 20 rows a page, the pager moves, a search goes back to page 1", async () => {
  render(<ClientList count={25} />);
  expect(shown()).toHaveLength(20);
  expect(screen.getByText("Showing 1–20 of 25")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Next page" }));
  expect(shown()).toEqual(["Row 21", "Row 22", "Row 23", "Row 24", "Row 25"]);
  expect(screen.getByText("Page 2 of 2")).toBeInTheDocument();
  await userEvent.type(screen.getByRole("searchbox", { name: "Search rows" }), "Row 1");
  expect(screen.getByText("Page 1 of 1")).toBeInTheDocument();
  expect(shown()).toContain("Row 1");
  await userEvent.clear(screen.getByRole("searchbox", { name: "Search rows" }));
  await userEvent.type(screen.getByRole("searchbox", { name: "Search rows" }), "nothing");
  expect(screen.getByText("No rows match.")).toBeInTheDocument();
});

test("client-side paging: when rows go, the last page there is", async () => {
  render(<ClientList count={25} />);
  await userEvent.click(screen.getByRole("button", { name: "Next page" }));
  await userEvent.click(screen.getByRole("button", { name: "Keep three" }));
  expect(screen.getByText("Page 1 of 1")).toBeInTheDocument();
  expect(shown()).toEqual(["Row 1", "Row 2", "Row 3"]);
});

test("server-side paging: shows the given page and total, asks for the next page", async () => {
  const onPageChange = vi.fn();
  render(
    <DataTable
      columns={columns}
      data={rows(2)}
      empty="None"
      total={45}
      page={2}
      pageSize={20}
      onPageChange={onPageChange}
      onPageSizeChange={() => {}}
    />,
  );
  expect(screen.getByText("Showing 21–40 of 45")).toBeInTheDocument();
  expect(shown()).toEqual(["Row 1", "Row 2"]);
  await userEvent.click(screen.getByRole("button", { name: "Next page" }));
  expect(onPageChange).toHaveBeenCalledWith(3);
});

test("sort marks the sorted column's header with aria-sort", () => {
  render(<DataTable columns={columns} data={rows(1)} empty="None" sort={{ key: "size", descending: true }} />);
  expect(screen.getByRole("columnheader", { name: "Size" })).toHaveAttribute("aria-sort", "descending");
  expect(screen.getByRole("columnheader", { name: "Name" })).not.toHaveAttribute("aria-sort");
});

test("onRowClick opens a row by click or Enter", async () => {
  const open = vi.fn();
  render(<DataTable columns={columns} data={rows(2)} empty="None" onRowClick={open} />);
  const [, first, second] = screen.getAllByRole("row");
  await userEvent.click(first);
  expect(open).toHaveBeenCalledWith(expect.objectContaining({ id: "r1" }));
  second.focus();
  await userEvent.keyboard("{Enter}");
  expect(open).toHaveBeenLastCalledWith(expect.objectContaining({ id: "r2" }));
});
