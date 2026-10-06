import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import { deleteCollection, getChunks, getCollection, getCollectionFiles, removeFolder, syncCollection, type CollectionFile, type CollectionPath, type RagCollection, type SkippedCounts } from "../lib/rag";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "~/components/ui/alert-dialog";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";
import RagCollectionForm from "./RagCollectionForm";
import RagFolderPicker from "./RagFolderPicker";
import { statusLabel, statusVariant, when } from "./RagCollectionsView";

type Chunk = { position: number; text: string; embedded: boolean };
interface Files { files: CollectionFile[]; truncated: boolean; skipped: SkippedCounts; missing_paths: string[] }

export default function RagCollectionDetail({ id }: { id: string }) {
  const navigate = useNavigate();
  const [collection, setCollection] = useState<RagCollection | null>(null);
  const [files, setFiles] = useState<Files | null>(null);
  const [selected, setSelected] = useState("");
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [editing, setEditing] = useState(false);
  const [picking, setPicking] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const [row, list] = await Promise.all([getCollection(id, signal), getCollectionFiles(id, signal)]);
      if (signal?.aborted) return;
      setCollection(row); setFiles(list); setError("");
    } catch (e) { if (!signal?.aborted) setError(e instanceof Error ? e.message : "Could not load the collection"); }
  }, [id]);
  useEffect(() => { const controller = new AbortController(); load(controller.signal); return () => controller.abort(); }, [load]);
  async function run(action: () => Promise<void>, reload = true) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); if (reload) await load(); } catch (e) { setError(e instanceof Error ? e.message : "Request failed"); } finally { setBusy(false); }
  }
  async function select(path: string) {
    setSelected(path); setChunks([]);
    await run(async () => { setChunks((await getChunks(id, path)).chunks); }, false);
  }
  const disconnect = (path: CollectionPath) => run(async () => {
    await removeFolder(id, path.id); setNotice("Folder removed. Its files left search; files on disk are kept.");
  });
  if (!collection) return <div className="flex flex-col gap-4">{error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : <Skeleton className="h-64" />}</div>;
  const c = collection;
  return <div className="flex flex-col gap-6">
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {notice && <Alert><AlertDescription role="status">{notice}</AlertDescription></Alert>}
    <section className="flex flex-col gap-3 rounded-lg border p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><h2 className="text-lg font-semibold">{c.name}</h2>{c.description && <p className="text-sm text-muted-foreground">{c.description}</p>}</div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => setEditing(true)}>Edit</Button>
          <Button variant="outline" size="sm" onClick={() => setPicking(true)}>Add folder</Button>
          <Button variant="outline" size="sm" disabled={busy} onClick={() => run(async () => { await syncCollection(id); setNotice("Refresh queued."); })}>Refresh</Button>
          <Button variant="outline" size="sm" disabled={busy} onClick={() => run(async () => { await syncCollection(id, true); setNotice("Rebuild queued."); })}>Rebuild</Button>
          <Button variant="destructive" size="sm" disabled={busy} onClick={() => setConfirmDelete(true)}>Remove</Button>
        </div>
      </div>
      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[10rem_minmax(0,1fr)]">
        <dt className="text-muted-foreground">Status</dt><dd><Badge variant={statusVariant(c.status)}>{statusLabel(c)}</Badge>
          {c.status === "folder_missing" && <span className="ml-2 text-destructive">Missing: {c.missing_paths.join(", ")}</span>}
          {c.status === "failed" && c.job?.error && <span className="ml-2 text-destructive">{c.job.error}</span>}</dd>
        <dt className="text-muted-foreground">Files indexed</dt><dd>{c.indexed_files} / {c.file_count}</dd>
        <dt className="text-muted-foreground">Embedded chunks</dt><dd>{c.embeddings_enabled ? `${c.embedded_chunks} / ${c.chunks}` : "Off"}</dd>
        <dt className="text-muted-foreground">Folders changed</dt><dd>{when(c.folder_changed_at)}</dd>
        <dt className="text-muted-foreground">Last synced</dt><dd>{when(c.last_synced_at)}</dd>
      </dl>
    </section>
    <section className="flex flex-col gap-3">
      <h3 className="text-base font-semibold">Folders</h3>
      <Table><TableHeader><TableRow><TableHead>Path</TableHead><TableHead>Indexed files</TableHead><TableHead>Folder</TableHead><TableHead /></TableRow></TableHeader><TableBody>
        {c.paths.map((p) => <TableRow key={p.id}>
          <TableCell><code className="break-all text-xs">{p.source_path}</code>{p.host_path && <p className="break-all text-xs text-muted-foreground">{p.host_path}</p>}</TableCell>
          <TableCell>{p.files}</TableCell>
          <TableCell>{p.present ? "Present" : <Badge variant="destructive">Missing</Badge>}</TableCell>
          <TableCell><Button variant="outline" size="sm" disabled={busy} aria-label={`Remove folder ${p.source_path}`} onClick={() => void disconnect(p)}>Remove</Button></TableCell>
        </TableRow>)}
        {c.paths.length === 0 && <TableRow><TableCell colSpan={4} className="py-8 text-center text-muted-foreground">No folders yet. Choose Add folder to connect one.</TableCell></TableRow>}
      </TableBody></Table>
    </section>
    <section className="flex flex-col gap-3">
      <h3 className="text-base font-semibold">Indexed files</h3>
      {files && <p className="text-xs text-muted-foreground">Skipped: {files.skipped.links} links, {files.skipped.special} special, {files.skipped.unreadable} unreadable, {files.skipped.invalid_names} invalid names.{files.truncated && " The list is truncated; only the first files are shown."}</p>}
      <Table><TableHeader><TableRow><TableHead>Path</TableHead><TableHead>Chunks</TableHead><TableHead>Embedded</TableHead><TableHead>Indexed</TableHead><TableHead /></TableRow></TableHeader><TableBody>
        {files?.files.map((f) => <TableRow key={f.path} data-state={f.path === selected ? "selected" : undefined}>
          <TableCell><Button variant="link" className="h-auto p-0 break-all" onClick={() => select(f.path)}>{f.path}</Button></TableCell>
          <TableCell>{f.chunks}</TableCell><TableCell>{f.embedded_chunks} embedded</TableCell><TableCell className="text-xs">{when(f.indexed_at)}</TableCell>
          <TableCell>{f.stale && <Badge variant="outline">Changed</Badge>}</TableCell>
        </TableRow>)}
        {files && files.files.length === 0 && <TableRow><TableCell colSpan={5} className="py-8 text-center text-muted-foreground">No files in these folders match the patterns.</TableCell></TableRow>}
      </TableBody></Table>
    </section>
    {selected && <section className="flex flex-col gap-3"><h3 className="text-base font-semibold">Chunks of {selected}</h3>
      {chunks.map((chunk) => <article key={chunk.position} className="flex flex-col gap-2 rounded-lg border p-3"><div className="flex items-center gap-2 text-xs text-muted-foreground">#{chunk.position}{!chunk.embedded && <Badge variant="outline">Not embedded</Badge>}</div><pre className="whitespace-pre-wrap text-sm">{chunk.text}</pre></article>)}
    </section>}
    <RagCollectionForm open={editing} collection={c} onOpenChange={setEditing} onSaved={() => { setEditing(false); load(); }} onFailed={() => load()} />
    <RagFolderPicker open={picking} collection={c} onOpenChange={setPicking}
      onAdded={() => { setPicking(false); setNotice("Folder added. Refresh the collection to index it."); load(); }} />
    <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Remove collection?</AlertDialogTitle>
      <AlertDialogDescription>Remove {c.name} from search? Its index, folders, file log and agent grants are removed; files on disk are kept.</AlertDialogDescription></AlertDialogHeader>
      <AlertDialogFooter><AlertDialogCancel>Cancel</AlertDialogCancel>
        <AlertDialogAction onClick={() => run(async () => { await deleteCollection(id, c.revision); navigate("/rag/collections"); }, false)}>Remove collection</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
    <Link className="text-sm underline" to="/rag/collections">Back to collections</Link>
  </div>;
}
