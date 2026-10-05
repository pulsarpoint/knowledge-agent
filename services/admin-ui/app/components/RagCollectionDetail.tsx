import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import { deleteCollection, getChunks, getCollection, getCollectionFiles, getRagFile, putRagFile, syncCollection, type CollectionFile, type RagCollection, type SkippedCounts } from "../lib/rag";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "~/components/ui/alert-dialog";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Field, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";
import { Textarea } from "~/components/ui/textarea";
import RagCollectionForm from "./RagCollectionForm";
import { STATUS_LABEL, statusVariant, when } from "./RagCollectionsView";

type Chunk = { position: number; text: string; embedded: boolean };
interface Files { files: CollectionFile[]; truncated: boolean; skipped: SkippedCounts }

export default function RagCollectionDetail({ id }: { id: string }) {
  const navigate = useNavigate();
  const [collection, setCollection] = useState<RagCollection | null>(null);
  const [files, setFiles] = useState<Files | null>(null);
  const [selected, setSelected] = useState("");
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editPath, setEditPath] = useState("");
  const [editContent, setEditContent] = useState("");
  const [editHash, setEditHash] = useState<string | null>(null);
  const [editOpen, setEditOpen] = useState(false);
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
          <Button variant="outline" size="sm" disabled={busy} onClick={() => run(async () => { await syncCollection(id); setNotice("Refresh queued."); })}>Refresh</Button>
          <Button variant="outline" size="sm" disabled={busy} onClick={() => run(async () => { await syncCollection(id, true); setNotice("Rebuild queued."); })}>Rebuild</Button>
          <Button variant="destructive" size="sm" disabled={busy} onClick={() => setConfirmDelete(true)}>Delete</Button>
        </div>
      </div>
      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[10rem_minmax(0,1fr)]">
        <dt className="text-muted-foreground">Folder</dt><dd><code className="break-all">{c.host_path ?? c.source_path}</code></dd>
        <dt className="text-muted-foreground">Status</dt><dd><Badge variant={statusVariant(c.status)}>{STATUS_LABEL[c.status]}</Badge>{c.status === "failed" && c.job?.error && <span className="ml-2 text-destructive">{c.job.error}</span>}</dd>
        <dt className="text-muted-foreground">Files indexed</dt><dd>{c.indexed_files} / {c.file_count}</dd>
        <dt className="text-muted-foreground">Embedded chunks</dt><dd>{c.embeddings_enabled ? `${c.embedded_chunks} / ${c.chunks}` : "Off"}</dd>
        <dt className="text-muted-foreground">Folder changed</dt><dd>{when(c.folder_changed_at)}</dd>
        <dt className="text-muted-foreground">Last synced</dt><dd>{when(c.last_synced_at)}</dd>
      </dl>
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
        {files && files.files.length === 0 && <TableRow><TableCell colSpan={5} className="py-8 text-center text-muted-foreground">No files in this folder match the patterns.</TableCell></TableRow>}
      </TableBody></Table>
    </section>
    {selected && <section className="flex flex-col gap-3"><h3 className="text-base font-semibold">Chunks of {selected}</h3>
      {chunks.map((chunk) => <article key={chunk.position} className="flex flex-col gap-2 rounded-lg border p-3"><div className="flex items-center gap-2 text-xs text-muted-foreground">#{chunk.position}{!chunk.embedded && <Badge variant="outline">Not embedded</Badge>}</div><pre className="whitespace-pre-wrap text-sm">{chunk.text}</pre></article>)}
    </section>}
    {c.writable && <section className="flex flex-col gap-3 rounded-lg border p-5"><h3 className="text-base font-semibold">Edit file</h3>
      <div className="flex flex-wrap items-end gap-2"><Field className="max-w-md"><FieldLabel htmlFor="edit-file-path">File path</FieldLabel><Input id="edit-file-path" value={editPath} onChange={(e) => { setEditPath(e.target.value); setEditOpen(false); }} placeholder="notes/new.md" /></Field>
        <Button variant="outline" disabled={busy || !editPath.trim()} onClick={() => run(async () => { const file = await getRagFile(id, editPath.trim()); setEditContent(file.content); setEditHash(file.hash); setEditOpen(true); }, false)}>Open file</Button>
        <Button variant="outline" disabled={busy || !editPath.trim()} onClick={() => { setEditContent(""); setEditHash(null); setEditOpen(true); }}>New file</Button></div>
      {editOpen && <><Field><FieldLabel htmlFor="edit-file-content">Content</FieldLabel><Textarea id="edit-file-content" className="min-h-48 font-mono" value={editContent} onChange={(e) => setEditContent(e.target.value)} /></Field>
        <Button className="self-start" disabled={busy} onClick={() => run(async () => { const saved = await putRagFile(id, editPath.trim(), editContent, editHash); setEditHash(saved.hash); setNotice("File saved. Refresh the collection to update search."); })}>Save file</Button></>}
      <Field className="max-w-md"><FieldLabel htmlFor="upload-file">Upload file</FieldLabel><Input id="upload-file" type="file" disabled={busy} onChange={(e) => { const file = e.target.files?.[0]; e.target.value = ""; if (file) run(async () => { await putRagFile(id, file.name, await file.text(), null); setNotice(`${file.name} uploaded. Refresh the collection to update search.`); }); }} /></Field>
    </section>}
    <RagCollectionForm open={editing} collection={c} onOpenChange={setEditing} onSaved={() => { setEditing(false); load(); }} />
    <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Delete collection?</AlertDialogTitle>
      <AlertDialogDescription>Remove {c.name} from search? Files in the folder are kept.</AlertDialogDescription></AlertDialogHeader>
      <AlertDialogFooter><AlertDialogCancel>Cancel</AlertDialogCancel>
        <AlertDialogAction onClick={() => run(async () => { await deleteCollection(id, c.revision); navigate("/rag/collections"); }, false)}>Delete collection</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
    <Link className="text-sm underline" to="/rag/collections">Back to collections</Link>
  </div>;
}
