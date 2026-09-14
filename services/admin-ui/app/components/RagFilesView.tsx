import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import { FileText, Plus, RefreshCw, Trash2, Upload } from "lucide-react";
import { ragRequest, type RagTree, type RagDocument, type RagFolder } from "../lib/rag";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "~/components/ui/alert-dialog";
import { Badge } from "~/components/ui/badge";
import { Button, buttonVariants } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import { Field, FieldGroup, FieldLabel, FieldDescription } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Textarea } from "~/components/ui/textarea";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "~/components/ui/sheet";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";
import { Skeleton } from "~/components/ui/skeleton";
import { Spinner } from "~/components/ui/spinner";
import RagSourceLocation from "./RagSourceLocation";
import FileTree, { type FileTreeEntry } from "./FileTree";

export default function RagFilesView() {
  const [tree, setTree] = useState<RagTree | null>(null);
  const [selected, setSelected] = useState("");
  const [description, setDescription] = useState("");
  const [newFolder, setNewFolder] = useState("");
  const [document, setDocument] = useState<RagDocument | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<{ folder: RagFolder; revision: number; subfolders: number; files: number } | null>(null);
  const [recursive, setRecursive] = useState(true);
  const [force, setForce] = useState(false);
  const [allowEmpty, setAllowEmpty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const upload = useRef<HTMLInputElement>(null);
  const folder = tree?.folders.find((row) => row.id === selected);
  const entries = useMemo<FileTreeEntry[]>(() => tree ? [
    ...tree.folders.map((row) => ({ path: row.path, kind: "folder" as const,
      detail: row.present ? `${row.files} ${row.files === 1 ? "file" : "files"} · ${row.indexed_files} indexed` : "Source folder missing" })),
    ...tree.files.map((file) => ({ path: file.path, kind: "file" as const,
      detail: !file.supported ? "Unsupported" : file.indexed_at ? "Indexed" : "Not processed" })),
  ] : [], [tree]);
  function selectFolder(row: RagFolder) {
    setSelected(row.id); setDescription(row.description); setAllowEmpty(false); setForce(false);
  }
  async function openFile(path: string) {
    const source = await ragRequest<RagDocument>(`file?path=${encodeURIComponent(path)}`);
    const parent = tree?.folders.find((row) => row.path === path.split("/").slice(0, -1).join("/"));
    if (parent && parent.id !== selected) selectFolder(parent);
    setDocument(source);
  }
  async function refresh() { setTree(await ragRequest<RagTree>("tree")); }
  useEffect(() => {
    const controller = new AbortController();
    ragRequest<RagTree>("tree", "GET", undefined, controller.signal).then(async (value) => {
      setTree(value);
      const path = new URLSearchParams(window.location.search).get('path');
      if (!path) return;
      const file = value.files.find((row) => row.path === path);
      const target = value.folders.find((row) => row.path === (file?.folder ?? path));
      if (target) { setSelected(target.id); setDescription(target.description); }
      if (file) setDocument(await ragRequest<RagDocument>(`file?path=${encodeURIComponent(path)}`, 'GET', undefined, controller.signal));
    })
      .catch((e) => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, []);
  async function run(action: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : "RAG request failed"); }
    finally { setBusy(false); }
  }
  return <div className="flex flex-col gap-5">
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {notice && <Alert><AlertDescription role="status">{notice}</AlertDescription></Alert>}
    {!!tree?.pending_deletions?.length && <Alert><AlertDescription>Indexed content has been removed. Source cleanup is still pending for {tree.pending_deletions.join(", ")}. The worker will retry automatically; check directory permissions if this persists.</AlertDescription></Alert>}
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p className="text-sm text-muted-foreground">Manage source folders and process them to update search.</p>
      <Button variant="outline" disabled={busy} onClick={() => run(refresh)}><RefreshCw />Refresh folders</Button>
    </div>
    {!tree && !error && <Skeleton className="h-64" />}
    {tree && <>
      <RagSourceLocation location={tree.source_location} />
      <form onSubmit={(event) => { event.preventDefault(); run(async () => {
        const result = await ragRequest<{ id: string }>("folders", "POST", { path: newFolder, description: "", revision: tree.revision });
        await refresh(); setSelected(result.id); setDescription(""); setNewFolder(""); setNotice("Folder created.");
      }); }}>
        <FieldGroup><Field>
          <FieldLabel htmlFor="new-rag-folder">New folder path</FieldLabel>
          <Field orientation="horizontal">
            <Input id="new-rag-folder" className="min-w-0 flex-1" aria-describedby="new-rag-folder-help" required value={newFolder} disabled={busy} placeholder="documentation/payments" onChange={(e) => setNewFolder(e.target.value)} />
            <Button type="submit" className="shrink-0" disabled={busy || !newFolder}><Plus data-icon="inline-start" />Create folder</Button>
          </Field>
          <FieldDescription id="new-rag-folder-help">Top-level folders are collections. Use / to create subfolders.</FieldDescription>
        </Field></FieldGroup>
      </form>
      <div className="grid items-start gap-6 lg:grid-cols-[300px_minmax(0,1fr)]">
        <nav aria-label="RAG folders" className="min-w-0 rounded-lg border">
          <div className="flex flex-col gap-1 border-b px-3 py-3"><h2 className="text-sm font-medium">Files &amp; folders</h2><p className="text-xs text-muted-foreground">Expand folders with the arrows. Select a file to open it.</p></div>
          {tree.folders.length === 0 && <p className="p-3 text-sm text-muted-foreground">Create your first collection folder or add folders to the mounted directory.</p>}
          <FileTree entries={entries} label="RAG files and folders" disabled={busy}
            selectedPath={document && tree.files.some((file) => file.path === document.path) ? document.path : folder?.path}
            onSelect={(entry) => {
              if (entry.kind === "file") { void run(() => openFile(entry.path)); return; }
              const row = tree.folders.find((candidate) => candidate.path === entry.path);
              if (row) selectFolder(row);
            }} />
        </nav>
        {folder ? <div className="flex min-w-0 flex-col gap-5">
          <div className="flex flex-wrap items-start justify-between gap-2"><div><h2 className="font-semibold">{folder.path}</h2><p className="text-sm text-muted-foreground">Search scope: <code>{folder.scope}</code> or <code>{folder.scope}.*</code></p></div><Badge variant="secondary">{folder.indexed_files} indexed files</Badge></div>
          {!folder.present && <Alert><AlertDescription>This folder is absent from the mounted source directory. Its last successful index is retained until you process this scope.</AlertDescription></Alert>}
          <FieldGroup><Field><FieldLabel htmlFor="rag-description">Folder description</FieldLabel><Textarea id="rag-description" maxLength={10000} value={description} disabled={busy} onChange={(e) => setDescription(e.target.value)} /><FieldDescription>The description becomes a searchable entry when this folder is processed.</FieldDescription></Field></FieldGroup>
          <div className="flex flex-wrap gap-2"><Button variant="outline" disabled={busy || description === folder.description} onClick={() => run(async () => {
            await ragRequest(`folders/${folder.id}`, "PUT", { path: folder.path, description, revision: tree.revision }); await refresh(); setNotice("Description saved. Process the folder to update search.");
          })}>Save description</Button>
            <Button variant="destructive" disabled={busy} onClick={() => {
              setError("");
              setDeleteTarget({ folder, revision: tree.revision,
                subfolders: tree.folders.filter((row) => row.path.startsWith(folder.path + "/")).length,
                files: tree.files.filter((file) => file.folder === folder.path || file.folder.startsWith(folder.path + "/")).length,
              });
            }}><Trash2 data-icon="inline-start" />Delete folder</Button>
          </div>
          <div className="flex flex-col gap-3 rounded-lg border p-4">
            <h3 className="font-medium">Process this folder</h3>
            <p className="text-sm text-muted-foreground">Unchanged files skip extraction, chunking, indexing, and embedding. Successful runs replace changed entries and remove deleted files.</p>
            <Field orientation="horizontal"><Checkbox id="rag-recursive" checked={recursive} onCheckedChange={(v) => setRecursive(v === true)} /><FieldLabel htmlFor="rag-recursive">Include subfolders</FieldLabel></Field>
            <Field orientation="horizontal"><Checkbox id="rag-force" checked={force} onCheckedChange={(v) => setForce(v === true)} /><FieldLabel htmlFor="rag-force">Force rebuild, including embeddings</FieldLabel></Field>
            <Field orientation="horizontal"><Checkbox id="rag-empty" checked={allowEmpty} onCheckedChange={(v) => setAllowEmpty(v === true)} /><FieldLabel htmlFor="rag-empty">Allow removing every indexed file in this scope</FieldLabel></Field>
            <div className="flex flex-wrap gap-2"><Button disabled={busy || description !== folder.description} onClick={() => run(async () => {
              await ragRequest("jobs", "POST", { folder_id: folder.id, recursive, force, allow_empty: allowEmpty }); setNotice("Processing queued. Follow its progress on Processing jobs.");
            })}>Process folder</Button><Button asChild variant="ghost"><Link to={`/rag/jobs`}>View processing jobs</Link></Button></div>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="font-medium">Files in this folder</h3><div className="flex gap-2">
            <input ref={upload} type="file" multiple className="hidden" aria-label="Upload source files" accept=".md,.markdown,.txt,.json" onChange={(e) => { const files = Array.from(e.target.files ?? []); e.target.value = ""; run(async () => {
              let count = 0;
              try { for (const file of files) { if (file.size > 10_000_000) throw new Error("File exceeds the maximum size");
                const content = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
                await ragRequest("file", "PUT", { path: `${folder.path}/${file.name}`, content }); count++;
              } } finally { await refresh(); if (count) setNotice(`${count} file(s) uploaded. Process this folder to update search.`); }
            }); }} />
            <Button variant="outline" disabled={busy || !folder.present} onClick={() => upload.current?.click()}><Upload />Upload</Button>
            <Button variant="outline" disabled={busy || !folder.present} onClick={() => setDocument({ path: `${folder.path}/new-document.md`, content: "", hash: null })}><Plus />New file</Button>
          </div></div>
          <Table><TableHeader><TableRow><TableHead>File</TableHead><TableHead>Size</TableHead><TableHead>Last indexed</TableHead></TableRow></TableHeader><TableBody>
            {tree.files.filter((file) => file.folder === folder.path).map((file) => <TableRow key={file.path}><TableCell><Button variant="link" className="h-auto justify-start whitespace-normal px-0 text-left" disabled={busy} onClick={() => run(() => openFile(file.path))}><FileText />{file.path.split("/").at(-1)}</Button>{!file.supported && <Badge variant="outline">Unsupported</Badge>}</TableCell><TableCell>{(file.size / 1024).toFixed(1)} KB</TableCell><TableCell>{file.indexed_at ? new Date(file.indexed_at).toLocaleString() : "Not processed"}</TableCell></TableRow>)}
            {!tree.files.some((file) => file.folder === folder.path) && <TableRow><TableCell colSpan={3} className="py-8 text-center text-muted-foreground">No files here. Upload Markdown, text, or JSON documents.</TableCell></TableRow>}
          </TableBody></Table>
        </div> : <div className="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground">Select a folder to manage its description, files, and processing.</div>}
      </div>
    </>}
    <AlertDialog open={deleteTarget !== null} onOpenChange={(open) => { if (!open && !busy) setDeleteTarget(null); }}>
      <AlertDialogContent aria-busy={busy}>
        <AlertDialogHeader><AlertDialogTitle className="break-all">Delete “{deleteTarget?.folder.path}”?</AlertDialogTitle>
          <AlertDialogDescription>This permanently deletes the folder, its {deleteTarget?.subfolders} subfolders and {deleteTarget?.files} source files, together with their indexed text, folder descriptions, and embeddings. Processing jobs covering this folder will be cancelled. This cannot be undone.</AlertDialogDescription>
        </AlertDialogHeader>
        {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
        <AlertDialogFooter><AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
          <AlertDialogAction className={buttonVariants({ variant: "destructive" })} disabled={busy} onClick={(event) => {
            event.preventDefault();
            if (!deleteTarget) return;
            const target = deleteTarget;
            run(async () => {
              const result = await ragRequest<{ cleanup_pending: boolean }>(`folders/${target.folder.id}?revision=${target.revision}&confirmed=true`, "DELETE");
              setSelected(""); setDescription(""); setDocument(null); setDeleteTarget(null);
              // Remove the deleted scope immediately, even if the refresh fails.
              setTree((current) => current ? { ...current,
                folders: current.folders.filter((row) => row.path !== target.folder.path && !row.path.startsWith(target.folder.path + "/")),
                files: current.files.filter((file) => file.folder !== target.folder.path && !file.folder.startsWith(target.folder.path + "/")),
              } : null);
              setNotice(result.cleanup_pending ? "Folder index deleted. Source cleanup is queued and will retry automatically." : "Folder, source files, indexed text, and embeddings deleted.");
              await refresh();
            });
          }}>{busy && <Spinner data-icon="inline-start" />}Delete folder and data</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
    <Sheet open={document !== null} onOpenChange={(open) => { if (!open && !busy) setDocument(null); }}><SheetContent className="flex w-full flex-col overflow-y-auto sm:max-w-3xl"><SheetHeader><SheetTitle>{document?.hash ? "Edit source file" : "New source file"}</SheetTitle><SheetDescription>Save source changes, then process the folder to publish them to search.</SheetDescription></SheetHeader>
      {document && <div className="flex flex-col gap-4 p-4"><Field><FieldLabel htmlFor="rag-file-path">File path</FieldLabel><Input id="rag-file-path" value={document.path} disabled={busy || document.hash !== null} onChange={(e) => setDocument({ ...document, path: e.target.value })} /></Field>
        <Field><FieldLabel htmlFor="rag-file-content">Content</FieldLabel><Textarea id="rag-file-content" className="min-h-96 font-mono text-sm" value={document.content} disabled={busy} onChange={(e) => setDocument({ ...document, content: e.target.value })} /></Field>
        {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
        <div className="flex gap-2"><Button disabled={busy} onClick={() => run(async () => { await ragRequest("file", "PUT", { path: document.path, content: document.content, expected_hash: document.hash }); await refresh(); setDocument(null); setNotice("File saved. Process its folder to update search."); })}>Save file</Button>
          {document.hash && <Button variant="destructive" disabled={busy} onClick={() => run(async () => { await ragRequest(`file?path=${encodeURIComponent(document.path)}&expected_hash=${document.hash}`, "DELETE"); await refresh(); setDocument(null); setNotice("Source file deleted. Process the folder to remove it from search."); })}>Delete source file</Button>}
        </div>
      </div>}
    </SheetContent></Sheet>
  </div>;
}
