import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { Plus, RefreshCw } from "lucide-react";
import { getCollections, syncCollection, syncStale, type CollectionState, type RagCollection } from "../lib/rag";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";
import RagCollectionForm from "./RagCollectionForm";

export const STATUS_LABEL: Record<CollectionState, string> = {
  up_to_date: "Up to date", needs_refresh: "Needs refresh", syncing: "Syncing", failed: "Failed", folder_missing: "Folder missing",
};
export const statusVariant = (status: CollectionState) => status === "failed" || status === "folder_missing" ? "destructive" as const : status === "up_to_date" ? "secondary" as const : "outline" as const;
export const when = (value: string | null) => value ? new Date(value).toLocaleString() : "Never";

export default function RagCollectionsView() {
  const [collections, setCollections] = useState<RagCollection[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const load = useCallback(async (signal?: AbortSignal) => {
    try { const result = await getCollections(signal); if (!signal?.aborted) { setCollections(result.collections); setError(""); } }
    catch (e) { if (!signal?.aborted) setError(e instanceof Error ? e.message : "Could not load collections"); }
  }, []);
  useEffect(() => { const controller = new AbortController(); load(controller.signal); return () => controller.abort(); }, [load]);
  const syncing = collections.some((c) => c.status === "syncing");
  useEffect(() => {
    if (!syncing) return;
    const controller = new AbortController();
    const timer = setInterval(() => load(controller.signal), 5000);
    return () => { clearInterval(timer); controller.abort(); };
  }, [syncing, load]);
  async function run(action: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); await load(); } catch (e) { setError(e instanceof Error ? e.message : "Request failed"); } finally { setBusy(false); }
  }
  return <div className="flex flex-col gap-4">
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {notice && <Alert><AlertDescription role="status">{notice}</AlertDescription></Alert>}
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p className="text-sm text-muted-foreground">Each collection indexes one folder. Refresh re-reads changed files; the folder is the source of truth.</p>
      <div className="flex gap-2">
        <Button variant="outline" disabled={busy} onClick={() => run(async () => { const { queued } = await syncStale(); setNotice(queued.length ? `Queued: ${queued.join(", ")}` : "Nothing to refresh"); })}><RefreshCw />Refresh all stale</Button>
        <Button onClick={() => setCreating(true)}><Plus />New collection</Button>
      </div>
    </div>
    <Table><TableHeader><TableRow><TableHead>Collection</TableHead><TableHead>Folder</TableHead><TableHead>Files</TableHead><TableHead>Embedded</TableHead><TableHead>Folder changed</TableHead><TableHead>Last synced</TableHead><TableHead>Status</TableHead><TableHead>Actions</TableHead></TableRow></TableHeader><TableBody>
      {collections.map((c) => <TableRow key={c.id}>
        <TableCell className="align-top"><Link className="font-medium underline-offset-4 hover:underline" to={`/rag/collections/${c.id}`}>{c.name}</Link>{c.description && <p className="text-xs text-muted-foreground">{c.description}</p>}</TableCell>
        <TableCell className="align-top"><code className="break-all text-xs">{c.host_path ?? c.source_path}</code></TableCell>
        <TableCell className="align-top">{c.indexed_files} / {c.file_count}</TableCell>
        <TableCell className="align-top">{c.embeddings_enabled ? `${c.embedded_chunks} / ${c.chunks}` : "Off"}</TableCell>
        <TableCell className="align-top text-xs">{when(c.folder_changed_at)}</TableCell>
        <TableCell className="align-top text-xs">{when(c.last_synced_at)}</TableCell>
        <TableCell className="align-top"><Badge variant={statusVariant(c.status)}>{STATUS_LABEL[c.status]}</Badge>{c.status === "failed" && c.job?.error && <p className="mt-1 max-w-xs text-xs text-destructive">{c.job.error}</p>}</TableCell>
        <TableCell className="align-top">{(c.status === "needs_refresh" || c.status === "failed") && <Button variant="outline" size="sm" disabled={busy} onClick={() => run(async () => { await syncCollection(c.id); })}>Refresh</Button>}</TableCell>
      </TableRow>)}
      {collections.length === 0 && <TableRow><TableCell colSpan={8} className="py-12 text-center text-muted-foreground">No collections yet. Create one to map a folder to search.</TableCell></TableRow>}
    </TableBody></Table>
    <RagCollectionForm open={creating} onOpenChange={setCreating} onSaved={() => { setCreating(false); load(); }} />
  </div>;
}
