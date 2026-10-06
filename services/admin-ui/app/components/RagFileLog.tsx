import { useEffect, useRef, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { getFileLog, LOG_STATUS_LABEL, type LogFile, type LogFilter, type LogStatus } from "../lib/rag";
import DataTable from "./DataTable";
import { when } from "./RagCollectionsView";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";

const ALL = "all";
const FILTERS: { value: LogFilter | typeof ALL; label: string }[] = [
  { value: ALL, label: "All files" }, { value: "indexed", label: "Indexed" }, { value: "skipped", label: "Skipped" },
  { value: "failed", label: "Failed" }, { value: "removed", label: "Removed" },
];
const bytes = (n: number) => n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;
const variant = (status: LogStatus) => status === "failed" ? "destructive" as const : status === "indexed" ? "secondary" as const : "outline" as const;

/** The collection's file log: what the last sync decided for each file, paged and filtered on the server.
 * `onClear` closes the selected file's chunks when the filters change or a loaded page no longer lists it. */
export default function RagFileLog({ collectionId, selected, onSelect, onClear, reload = 0 }: {
  collectionId: string; selected: string; onSelect: (path: string) => void; onClear?: () => void; reload?: number;
}) {
  const current = useRef({ selected, onClear });
  current.current = { selected, onClear };
  const [status, setStatus] = useState<LogFilter | typeof ALL>(ALL);
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [files, setFiles] = useState<LogFile[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    getFileLog(collectionId, { status: status === ALL ? undefined : status, q: q.trim() || undefined, limit: size, offset: (page - 1) * size }, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        setFiles(result.files); setTotal(result.total); setError("");
        const { selected: open, onClear: clear } = current.current;
        if (open && !result.files.some((file) => file.path === open && file.status === "indexed")) clear?.();
      })
      .catch((e) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Could not load the file log"); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [collectionId, status, q, page, size, reload]);
  const columns: ColumnDef<LogFile, unknown>[] = [
    { id: "path", header: "Path", cell: ({ row }) => row.original.status === "indexed"
      ? <Button variant="link" className="h-auto p-0 break-all" aria-pressed={row.original.path === selected} onClick={() => onSelect(row.original.path)}>{row.original.path}</Button>
      : <span className="break-all">{row.original.path}</span> },
    { id: "status", header: "Status", cell: ({ row }) => <Badge variant={variant(row.original.status)}>{LOG_STATUS_LABEL[row.original.status]}</Badge> },
    { id: "size", header: "Size", cell: ({ row }) => row.original.size === null ? "—" : bytes(row.original.size) },
    { id: "chunks", header: "Chunks", cell: ({ row }) => row.original.chunks },
    { id: "indexed", header: "Indexed at", cell: ({ row }) => <span className="whitespace-nowrap text-xs">{when(row.original.indexed_at)}</span> },
    { id: "error", header: "Error", cell: ({ row }) => row.original.error && <span className="text-xs text-destructive">{row.original.error}</span> },
  ];
  const toolbar = <div className="flex flex-wrap items-center gap-2">
    <Input type="search" aria-label="Search files" placeholder="Search by path…" className="h-9 w-64" value={q}
      onChange={(e) => { setQ(e.target.value); setPage(1); onClear?.(); }} />
    <Select value={status} onValueChange={(value) => { setStatus(value as LogFilter | typeof ALL); setPage(1); onClear?.(); }}>
      <SelectTrigger className="h-9 w-40" aria-label="File status"><SelectValue /></SelectTrigger>
      <SelectContent><SelectGroup>{FILTERS.map((filter) => <SelectItem key={filter.value} value={filter.value}>{filter.label}</SelectItem>)}</SelectGroup></SelectContent>
    </Select>
  </div>;
  return <div className="flex flex-col gap-2">
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <DataTable columns={columns} data={files} total={total} page={page} pageSize={size} rowId={(file) => file.path} loading={loading}
      onPageChange={setPage} onPageSizeChange={(value) => { setSize(value); setPage(1); }} toolbar={toolbar}
      empty={status === ALL && !q.trim() ? "No files yet. Refresh the collection to index its folders." : "No files match these filters."} />
  </div>;
}
