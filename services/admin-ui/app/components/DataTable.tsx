import { useState, type ReactNode } from "react";
import { flexRender, getCoreRowModel, useReactTable, type ColumnDef } from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronLeft, ChevronRight, type LucideIcon } from "lucide-react";
import { Button } from "~/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "~/components/ui/empty";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Skeleton } from "~/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

export const PAGE_SIZES = [10, 20, 50, 100];
export const DEFAULT_PAGE_SIZE = 20;
const SKELETON_ROWS = 3;

/** The column a list is sorted by, and which way. */
export type Sort<K extends string = string> = { key: K; descending: boolean };

/** Clicking a header: the same column turns the order round; another column sorts by it, ascending. */
export const nextSort = <K extends string>(sort: Sort<K>, key: K): Sort<K> => ({
  key,
  descending: sort.key === key ? !sort.descending : false,
});

/** A client-side list in the chosen order: `compare[key]` ascending, reversed when descending. */
export function sortRows<T, K extends string>(
  rows: T[],
  sort: Sort<K>,
  compare: Record<K, (a: T, b: T) => number>,
): T[] {
  return [...rows].sort((a, b) => (sort.descending ? -1 : 1) * compare[sort.key](a, b));
}

/** A sortable column's header: its label and an arrow (both ways while unsorted, up or down when sorted by it); a click
 * calls `onSort` with the next order. */
export function sortHeader<K extends string>(
  label: string,
  key: K,
  sort: Sort<K>,
  onSort: (next: Sort<K>) => void,
): ReactNode {
  const Icon = sort.key !== key ? ArrowUpDown : sort.descending ? ArrowDown : ArrowUp;
  return (
    <Button variant="ghost" size="sm" className="-ml-3" onClick={() => onSort(nextSort(sort, key))}>
      {label}
      <Icon data-icon="inline-end" aria-hidden />
    </Button>
  );
}

/** What an empty list shows: an icon, an action title ("No schedules yet"), a line and the first-item button. */
export function TableEmpty({
  icon: Icon,
  title,
  description,
  children,
}: {
  icon: LucideIcon;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <Empty className="p-4 md:p-6">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <Icon />
        </EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        {description && <EmptyDescription>{description}</EmptyDescription>}
      </EmptyHeader>
      {children && <EmptyContent>{children}</EmptyContent>}
    </Empty>
  );
}

/** The toolbar's search: one `type="search"` field, unlabelled, named and hinted "Search <plural>". */
export type TableSearch = { plural: string; value: string; onChange: (value: string) => void };

type ServerPaging = {
  total: number;
  page: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
};
type ClientPaging = {
  total?: undefined;
  page?: undefined;
  pageSize?: undefined;
  onPageChange?: undefined;
  onPageSizeChange?: undefined;
};

/** The one record list: a toolbar (search, then the page's filters and actions), the table, and a pager.
 *
 * Server-side paging: pass `total`, `page`, `pageSize` and their handlers; `data` is the current page. Client-side
 * paging: leave them out and pass every row; the table pages them itself, back to page 1 when the search or `resetKey`
 * (the filters and sort) change, and to the last page there is when rows go. While `loading` with no rows it shows
 * Skeleton rows; with no rows it shows `empty` (a `TableEmpty`, or a line such as "No agents match."). `sort` marks the
 * sorted header; `onRowClick` makes rows open something (a detail sheet). */
export default function DataTable<T>({
  columns,
  data,
  search,
  toolbar,
  empty,
  loading = false,
  rowId,
  resetKey = "",
  sort,
  onRowClick,
  ...paging
}: {
  columns: ColumnDef<T, unknown>[];
  data: T[];
  search?: TableSearch;
  toolbar?: ReactNode;
  empty: ReactNode;
  loading?: boolean;
  rowId?: (row: T) => string;
  resetKey?: string;
  /** The order shown: the column whose id is `sort.key` gets aria-sort. */
  sort?: Sort;
  /** Opening a row by a click or Enter (the row is focusable). */
  onRowClick?: (row: T) => void;
} & (ServerPaging | ClientPaging)) {
  const [clientPage, setClientPage] = useState(1);
  const [clientSize, setClientSize] = useState(DEFAULT_PAGE_SIZE);
  const key = `${search?.value ?? ""}\n${resetKey}`;
  const [seenKey, setSeenKey] = useState(key);
  const client = paging.total === undefined;
  if (client && key !== seenKey) {
    setSeenKey(key);
    setClientPage(1);
  }
  const total = paging.total ?? data.length;
  const pageSize = paging.pageSize ?? clientSize;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  // A delete or a reload can empty the last page: show the last page there is.
  const clamped = Math.min(clientPage, pageCount);
  if (client && clamped !== clientPage) setClientPage(clamped);
  const page = paging.page ?? clamped;
  const onPageChange = paging.onPageChange ?? setClientPage;
  const onPageSizeChange =
    paging.onPageSizeChange ??
    ((size: number) => {
      setClientSize(size);
      setClientPage(1);
    });
  const rows = client ? data.slice((page - 1) * pageSize, page * pageSize) : data;

  const table = useReactTable({
    data: rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
    manualPagination: true,
    pageCount,
    state: { pagination: { pageIndex: page - 1, pageSize } },
    getRowId: rowId ? (row) => rowId(row) : undefined,
  });
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const last = Math.min(total, page * pageSize);
  const showSkeleton = loading && rows.length === 0;
  return (
    <div className="flex min-w-0 flex-col gap-3">
      {(search || toolbar) && (
        <div className="flex flex-wrap items-center gap-2">
          {search && (
            <Input
              type="search"
              aria-label={`Search ${search.plural}`}
              placeholder={`Search ${search.plural}`}
              className="max-w-xs"
              value={search.value}
              onChange={(event) => search.onChange(event.target.value)}
            />
          )}
          {toolbar}
        </div>
      )}
      <div className="overflow-hidden rounded-lg border">
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((group) => (
              <TableRow key={group.id}>
                {group.headers.map((header) => (
                  <TableHead
                    key={header.id}
                    aria-sort={
                      sort?.key === header.column.id ? (sort.descending ? "descending" : "ascending") : undefined
                    }
                  >
                    {header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext())}
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody aria-busy={loading || undefined}>
            {table.getRowModel().rows.map((row) => (
              <TableRow
                key={row.id}
                {...(onRowClick && {
                  tabIndex: 0,
                  className: "cursor-pointer",
                  onClick: () => onRowClick(row.original),
                  onKeyDown: (event: React.KeyboardEvent) => {
                    if (event.key === "Enter") onRowClick(row.original);
                  },
                })}
              >
                {row.getVisibleCells().map((cell) => (
                  <TableCell key={cell.id} className="align-top whitespace-normal">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </TableCell>
                ))}
              </TableRow>
            ))}
            {showSkeleton &&
              Array.from({ length: SKELETON_ROWS }, (_, index) => (
                <TableRow key={`skeleton-${index}`} data-testid="skeleton-row">
                  {columns.map((_, cell) => (
                    <TableCell key={cell}>
                      {index === 0 && cell === 0 && <span className="sr-only">Loading…</span>}
                      <Skeleton className="h-5 w-full" />
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            {!showSkeleton && rows.length === 0 && (
              <TableRow>
                <TableCell
                  colSpan={columns.length}
                  className={
                    typeof empty === "string" ? "h-24 text-center whitespace-normal text-muted-foreground" : "p-0"
                  }
                >
                  {empty}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
      {total > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
          <span className="text-muted-foreground">
            Showing {first}–{last} of {total}
          </span>
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground">Rows per page</span>
            <Select value={String(pageSize)} onValueChange={(value) => onPageSizeChange(Number(value))}>
              <SelectTrigger className="h-8 w-20" aria-label="Rows per page">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {PAGE_SIZES.map((size) => (
                    <SelectItem key={size} value={String(size)}>
                      {size}
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
            <span className="text-muted-foreground">
              Page {page} of {pageCount}
            </span>
            <Button
              variant="outline"
              size="icon"
              aria-label="Previous page"
              disabled={page <= 1}
              onClick={() => onPageChange(page - 1)}
            >
              <ChevronLeft />
            </Button>
            <Button
              variant="outline"
              size="icon"
              aria-label="Next page"
              disabled={page >= pageCount}
              onClick={() => onPageChange(page + 1)}
            >
              <ChevronRight />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
