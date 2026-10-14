import { useCallback, useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { deleteCollection, getChunks, getCollection, removeFolder, syncCollection, syncLabel, type CollectionPath, type RagCollection } from "./api";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "~/components/ui/alert-dialog";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";
import RagCollectionForm from "./RagCollectionForm";
import RagFileLog from "./RagFileLog";
import RagFolderPicker from "./RagFolderPicker";
import { statusLabel, statusVariant, TOO_LARGE_HINT, tooLarge, when } from "./RagCollectionsView";

type Chunk = { position: number; text: string; embedded: boolean };

export default function RagCollectionDetail({ id }: { id: string }) {
  const navigate = useNavigate();
  const [collection, setCollection] = useState<RagCollection | null>(null);
  const [logVersion, setLogVersion] = useState(0);
  const [selected, setSelected] = useState("");
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [editing, setEditing] = useState(false);
  const [picking, setPicking] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [removing, setRemoving] = useState<CollectionPath | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const location = useLocation();
  const [folderError] = useState(() => (location.state as { folderError?: string } | null)?.folderError ?? "");
  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const row = await getCollection(id, signal);
      if (signal?.aborted) return;
      setCollection(row); setLogVersion((value) => value + 1); setError("");
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
  const clearSelection = useCallback(() => { setSelected(""); setChunks([]); }, []);
  const disconnect = (path: CollectionPath) => run(async () => {
    await removeFolder(id, path.id); setNotice("Folder removed. Its files left search; files on disk are kept.");
  });
  if (!collection) return <div className="flex flex-col gap-4">{error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : <Skeleton className="h-64" />}</div>;
  const c = collection;
  return <div className="flex flex-col gap-6">
    {folderError && <Alert variant="destructive"><AlertDescription>{folderError}</AlertDescription></Alert>}
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {notice && <Alert><AlertDescription role="status">{notice}</AlertDescription></Alert>}
    <section className="flex flex-col gap-3 rounded-lg border p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><h2 className="text-lg font-semibold">{c.name}</h2>{c.description && <p className="text-sm text-muted-foreground">{c.description}</p>}</div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => setEditing(true)}>Edit</Button>
          <Button variant="outline" size="sm" onClick={() => setPicking(true)}>Add folder</Button>
          <Button variant="outline" size="sm" disabled={busy || tooLarge(c)} title={tooLarge(c) ? TOO_LARGE_HINT : undefined} onClick={() => run(async () => { await syncCollection(id); setNotice("Refresh queued."); })}>Refresh</Button>
          <Button variant="outline" size="sm" disabled={busy} onClick={() => run(async () => { await syncCollection(id, true); setNotice("Rebuild queued."); })}>Rebuild</Button>
          <Button variant="destructive" size="sm" disabled={busy} onClick={() => setConfirmDelete(true)}>Remove</Button>
        </div>
      </div>
      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[10rem_minmax(0,1fr)]">
        <dt className="text-muted-foreground">Status</dt><dd><Badge variant={statusVariant(c.status)}>{statusLabel(c)}</Badge>
          {c.status === "folder_missing" && <span className="ml-2 text-destructive">Missing: {c.missing_paths.join(", ")}</span>}
          {c.status === "failed" && c.job?.error && <span className="ml-2 text-destructive">{c.job.error}</span>}</dd>
        <dt className="text-muted-foreground">Files</dt><dd>{c.log_counts.indexed} indexed · {c.log_counts.skipped} skipped · {c.log_counts.failed} failed</dd>
        <dt className="text-muted-foreground">Files found</dt><dd>{c.file_count}{c.truncated && " (or more)"}</dd>
        <dt className="text-muted-foreground">Embedded chunks</dt><dd>{c.embeddings_enabled ? `${c.embedded_chunks} / ${c.chunks}` : "Off"}</dd>
        <dt className="text-muted-foreground">Sync</dt><dd>{syncLabel(c)}</dd>
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
          <TableCell><Button variant="outline" size="sm" disabled={busy} aria-label={`Remove folder ${p.source_path}`} onClick={() => setRemoving(p)}>Remove</Button></TableCell>
        </TableRow>)}
        {c.paths.length === 0 && <TableRow><TableCell colSpan={4} className="py-8 text-center text-muted-foreground">No folders yet. Choose Add folder to connect one.</TableCell></TableRow>}
      </TableBody></Table>
    </section>
    <section className="flex flex-col gap-3">
      <h3 className="text-base font-semibold">File log</h3>
      <p className="text-xs text-muted-foreground">Not listed: {c.skipped.links} links, {c.skipped.special} special files, {c.skipped.unreadable} unreadable entries, {c.skipped.invalid_names} invalid names.</p>
      <RagFileLog collectionId={id} selected={selected} onSelect={(path) => void select(path)} onClear={clearSelection} reload={logVersion} />
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
        <AlertDialogAction onClick={() => run(async () => {
          // A refused delete (e.g. a stale revision) reloads first, so the retry uses the current revision.
          try { await deleteCollection(id, c.revision); } catch (e) { await load(); throw e; }
          navigate("/rag/collections");
        }, false)}>Remove collection</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
    <AlertDialog open={removing !== null} onOpenChange={(open) => { if (!open) setRemoving(null); }}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Remove folder?</AlertDialogTitle>
      <AlertDialogDescription>Remove folder {removing?.source_path} from {c.name}? Its indexed files and log entries are removed from search. Files on disk are kept.</AlertDialogDescription></AlertDialogHeader>
      <AlertDialogFooter><AlertDialogCancel>Cancel</AlertDialogCancel>
        <AlertDialogAction onClick={() => { if (removing) void disconnect(removing); setRemoving(null); }}>Remove folder</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
    <Link className="text-sm underline" to="/rag/collections">Back to collections</Link>
  </div>;
}
