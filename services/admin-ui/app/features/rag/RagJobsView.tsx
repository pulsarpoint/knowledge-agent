import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { RefreshCw, X } from "lucide-react";
import { ragRequest, type RagJob } from "./api";
import DataTable, { PAGE_SIZES } from "~/components/DataTable";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";

const STATUSES = ["queued", "running", "succeeded", "failed", "cancelled"];
const ACTIVE = ["queued", "running"];
const ALL = "all";
const DEFAULT_SIZE = 25;

function duration(job: RagJob, now: number): string {
  if (!job.started_at) return "—";
  const end = job.finished_at ? Date.parse(job.finished_at) : now;
  const seconds = Math.max(0, Math.round((end - Date.parse(job.started_at)) / 1000));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

export default function RagJobsView() {
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "";
  const collection = params.get("collection") ?? "";
  const q = params.get("q") ?? "";
  const page = Math.max(1, Number(params.get("page")) || 1);
  const size = PAGE_SIZES.includes(Number(params.get("size"))) ? Number(params.get("size")) : DEFAULT_SIZE;
  const [search, setSearch] = useState(q);
  const [jobs, setJobs] = useState<RagJob[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [names, setNames] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);

  /** Replace some query values; changing a filter returns to page one. Defaults are left out of the URL. */
  function update(values: Record<string, string | number | null>, keepPage = false) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(values)) {
      if (value === null || value === "" || (key === "page" && value === 1) || (key === "size" && value === DEFAULT_SIZE)) next.delete(key);
      else next.set(key, String(value));
    }
    if (!keepPage) next.delete("page");
    setParams(next);
  }

  useEffect(() => { setSearch(q); }, [q]);
  useEffect(() => {
    if (search.trim() === q) return;
    const timer = setTimeout(() => update({ q: search.trim() }), 300);
    return () => clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    const controller = new AbortController();
    ragRequest<{ collections: { name: string }[] }>("collections?status=false", "GET", undefined, controller.signal)
      .then((result) => setNames(result.collections.map((c) => c.name)))
      .catch(() => { /* the collection filter is optional; the table still works */ });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const query = new URLSearchParams();
    if (status) query.set("status", status);
    if (collection) query.set("collection", collection);
    if (q) query.set("q", q);
    query.set("limit", String(size));
    query.set("offset", String((page - 1) * size));
    async function load() {
      try {
        const result = await ragRequest<{ jobs: RagJob[]; total: number }>(`jobs?${query}`, "GET", undefined, controller.signal);
        if (controller.signal.aborted) return;
        setJobs(result.jobs); setTotal(result.total); setError("");
        if (result.jobs.some((job) => ACTIVE.includes(job.status))) timer = setTimeout(load, 2000);
      } catch (e) {
        if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Could not load jobs");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [status, collection, q, page, size, reload]);

  async function action(resource: string, body?: unknown) {
    setBusy(true); setError("");
    try { await ragRequest(resource, "POST", body); setReload((v) => v + 1); }
    catch (e) { setError(e instanceof Error ? e.message : "Job action failed"); }
    finally { setBusy(false); }
  }

  const now = Date.now();
  const columns: ColumnDef<RagJob, unknown>[] = [
    { id: "collection", header: "Collection", cell: ({ row }) => <span className="font-medium">{row.original.collection}</span> },
    { id: "status", header: "Status", cell: ({ row }) => <Badge variant={row.original.status === "failed" ? "destructive" : "secondary"}>{row.original.status}</Badge> },
    { id: "mode", header: "Mode", cell: ({ row }) => row.original.input.auto ? "Auto" : row.original.input.force ? "Force rebuild" : "Incremental" },
    { id: "started", header: "Started", cell: ({ row }) => <span className="whitespace-nowrap">{new Date(row.original.created_at).toLocaleString()}</span> },
    { id: "duration", header: "Duration", cell: ({ row }) => duration(row.original, now) },
    { id: "result", header: "Result", cell: ({ row }) => <div className="max-w-md">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">{Object.entries(row.original.result ?? {}).filter(([, count]) => count).map(([name, count]) =>
        <div key={name} className="flex gap-2"><dt className="text-muted-foreground">{name.replaceAll("_", " ")}</dt><dd>{count}</dd></div>)}</dl>
      {row.original.error && <p className="mt-2 text-sm text-destructive">{row.original.error}</p>}
    </div> },
    { id: "actions", header: () => <span className="sr-only">Actions</span>, cell: ({ row }) => {
      const job = row.original;
      if (!job.collection_id) return <span className="text-xs text-muted-foreground">Collection deleted</span>;
      if (ACTIVE.includes(job.status)) return <Button variant="outline" size="sm" disabled={busy} onClick={() => action(`jobs/${job.id}/cancel`)}>Cancel</Button>;
      return <Button variant="outline" size="sm" disabled={busy} onClick={() => action(`collections/${job.collection_id}/sync`, { force: job.input.force === true })}>Run again</Button>;
    } },
  ];

  const filtered = Boolean(status || collection || q);
  const toolbar = <div className="flex flex-wrap items-center gap-2">
    <Input type="search" aria-label="Search jobs" placeholder="Search collection or error…" className="h-9 w-64" value={search} onChange={(e) => setSearch(e.target.value)} />
    <Select value={status || ALL} onValueChange={(value) => update({ status: value === ALL ? null : value })}>
      <SelectTrigger className="h-9 w-40" aria-label="Status"><SelectValue /></SelectTrigger>
      <SelectContent><SelectGroup><SelectItem value={ALL}>All statuses</SelectItem>{STATUSES.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectGroup></SelectContent>
    </Select>
    <Select value={collection || ALL} onValueChange={(value) => update({ collection: value === ALL ? null : value })}>
      <SelectTrigger className="h-9 w-44" aria-label="Collection"><SelectValue /></SelectTrigger>
      <SelectContent><SelectGroup><SelectItem value={ALL}>All collections</SelectItem>
        {[...new Set([...names, ...(collection ? [collection] : [])])].map((name) => <SelectItem key={name} value={name}>{name}</SelectItem>)}</SelectGroup></SelectContent>
    </Select>
    {filtered && <Button variant="ghost" size="sm" onClick={() => { setSearch(""); setParams(new URLSearchParams()); }}><X data-icon="inline-start" />Clear filters</Button>}
    <Button variant="outline" size="sm" className="ml-auto" disabled={busy} onClick={() => setReload((v) => v + 1)}><RefreshCw data-icon="inline-start" />Refresh</Button>
  </div>;

  return <div className="flex flex-col gap-4">
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <p className="text-sm text-muted-foreground">Sync runs for RAG collections, newest first. Active jobs refresh automatically; a failed sync keeps the previous index.</p>
    <DataTable columns={columns} data={jobs} total={total} page={page} pageSize={size} rowId={(job) => job.id} loading={loading}
      onPageChange={(next) => update({ page: next }, true)} onPageSizeChange={(next) => update({ size: next })}
      toolbar={toolbar} empty={filtered ? "No jobs match these filters." : "No sync jobs yet. Refresh a collection on the Collections page to start."} />
  </div>;
}
