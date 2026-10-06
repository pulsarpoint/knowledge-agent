import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { Plus, RefreshCw } from "lucide-react";
import { getCollections, syncCollection, syncLabel, syncStale, type CollectionState, type RagCollection } from "../lib/rag";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";
import RagCollectionForm from "./RagCollectionForm";

export const STATUS_LABEL: Record<CollectionState, string> = {
  up_to_date: "Up to date", needs_refresh: "Needs refresh", syncing: "Syncing", failed: "Failed", folder_missing: "Folder missing",
};
/** Over the sync limits: never offer a sync that would fail, and say why instead of Needs refresh. */
export const tooLarge = (c: Pick<RagCollection, "too_large">) => c.too_large;
export const TOO_LARGE_HINT = "Over the sync limits — narrow the include patterns or folders";
export const statusLabel = (c: Pick<RagCollection, "status" | "too_large">) => tooLarge(c) && c.status === "needs_refresh" ? "Too large to sync" : STATUS_LABEL[c.status];
export const statusVariant = (status: CollectionState) => status === "failed" || status === "folder_missing" ? "destructive" as const : status === "up_to_date" ? "secondary" as const : "outline" as const;
export const when = (value: string | null) => value ? new Date(value).toLocaleString() : "Never";

export default function RagCollectionsView() {
  const [collections, setCollections] = useState<RagCollection[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const wanted = params.get("name");
  const load = useCallback(async (signal?: AbortSignal) => {
    try { const result = await getCollections(signal); if (!signal?.aborted) { setCollections(result.collections); setError(""); } }
    catch (e) { if (!signal?.aborted) setError(e instanceof Error ? e.message : "Could not load collections"); }
  }, []);
  useEffect(() => { const controller = new AbortController(); load(controller.signal); return () => controller.abort(); }, [load]);
  useEffect(() => {
    const match = wanted ? collections.find((c) => c.name === wanted) : undefined;
    if (match) navigate(`/rag/collections/${match.id}`, { replace: true });
  }, [wanted, collections, navigate]);
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
      <p className="text-sm text-muted-foreground">A collection indexes the folders you connect to it. Sources such as Docs → Documents, the Jira export and Git pulls only write files into their folders. Refresh re-reads changed files.</p>
      <div className="flex gap-2">
        <Button variant="outline" disabled={busy} onClick={() => run(async () => { const { queued } = await syncStale(); setNotice(queued.length ? `Queued: ${queued.join(", ")}` : "Nothing to refresh"); })}><RefreshCw />Refresh all stale</Button>
        <Button onClick={() => setCreating(true)}><Plus />New collection</Button>
      </div>
    </div>
    <Table><TableHeader><TableRow><TableHead>Collection</TableHead><TableHead>Folders</TableHead><TableHead>Files</TableHead><TableHead>Embedded</TableHead><TableHead>Sync</TableHead><TableHead>Last synced</TableHead><TableHead>Status</TableHead><TableHead>Actions</TableHead></TableRow></TableHeader><TableBody>
      {collections.map((c) => <TableRow key={c.id}>
        <TableCell className="align-top"><Link className="font-medium underline-offset-4 hover:underline" to={`/rag/collections/${c.id}`}>{c.name}</Link>{c.description && <p className="text-xs text-muted-foreground">{c.description}</p>}</TableCell>
        <TableCell className="align-top"><ul className="flex flex-col gap-1">
          {c.paths.map((p) => <li key={p.id} className="flex flex-wrap items-center gap-2 text-xs">
            <code className="break-all">{p.host_path ?? p.source_path}</code>
            <span className="text-muted-foreground">{p.files} files</span>
            {!p.present && <Badge variant="destructive">Missing</Badge>}
          </li>)}
          {c.paths.length === 0 && <li className="text-xs text-muted-foreground">No folders yet</li>}
        </ul></TableCell>
        <TableCell className="align-top">{c.indexed_files} / {c.file_count}</TableCell>
        <TableCell className="align-top">{c.embeddings_enabled ? `${c.embedded_chunks} / ${c.chunks}` : "Off"}</TableCell>
        <TableCell className="align-top text-xs">{syncLabel(c)}</TableCell>
        <TableCell className="align-top text-xs">{when(c.last_synced_at)}</TableCell>
        <TableCell className="align-top"><Badge variant={statusVariant(c.status)}>{statusLabel(c)}</Badge>{c.status === "failed" && c.job?.error && <p className="mt-1 max-w-xs text-xs text-destructive">{c.job.error}</p>}{c.status === "folder_missing" && <p className="mt-1 text-xs text-destructive">Missing: {c.missing_paths.join(", ")}</p>}</TableCell>
        <TableCell className="align-top">{(c.status === "needs_refresh" || c.status === "failed") && <Button variant="outline" size="sm" disabled={busy || tooLarge(c)} title={tooLarge(c) ? TOO_LARGE_HINT : undefined} onClick={() => run(async () => { await syncCollection(c.id); })}>Refresh</Button>}</TableCell>
      </TableRow>)}
      {collections.length === 0 && <TableRow><TableCell colSpan={8} className="py-12 text-center text-muted-foreground">No collections yet. Choose New collection, then add folders to it.</TableCell></TableRow>}
    </TableBody></Table>
    <RagCollectionForm open={creating} onOpenChange={setCreating} onSaved={(saved) => { setCreating(false); navigate(`/rag/collections/${saved.id}`); }} />
  </div>;
}
