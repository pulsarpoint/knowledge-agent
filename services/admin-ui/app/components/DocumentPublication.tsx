import { useState } from 'react';
import { BookUp } from 'lucide-react';
import { getDocument, publishDocument, unpublishDocument, type DocumentSummary } from '~/lib/documents';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '~/components/ui/alert-dialog';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Field, FieldDescription, FieldLabel } from '~/components/ui/field';
import { Input } from '~/components/ui/input';
import { Spinner } from '~/components/ui/spinner';

type Publication = Pick<DocumentSummary, 'id' | 'version' | 'published_path' | 'published_version' | 'publish_error' | 'index_pending'>;
const folderOf = (path: string) => path.slice(0, path.lastIndexOf('/'));

/** Where a document is in the RAG knowledge base, with publish, retry and unpublish actions. */
export default function DocumentPublication({ document }: { document: Publication }) {
  const [state, setState] = useState<Publication>(document);
  const [choosing, setChoosing] = useState(false);
  const [folder, setFolder] = useState('documents');
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function run(action: () => Promise<unknown>) {
    setBusy(true); setError('');
    try {
      await action();
      const fresh = await getDocument(state.id);
      setState({ id: fresh.id, version: fresh.version, published_path: fresh.published_path, published_version: fresh.published_version,
        publish_error: fresh.publish_error, index_pending: fresh.index_pending });
      setChoosing(false); setConfirm(false);
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not update the knowledge base'); }
    finally { setBusy(false); }
  }
  const path = state.published_path;
  return <section aria-label="Knowledge base" className="flex flex-col gap-2 rounded-lg border p-3">
    {path ? <div className="flex flex-wrap items-center gap-2 text-sm">
      <span>In knowledge base:</span><code className="text-xs">{path}</code><Badge variant="secondary">v{state.published_version}</Badge>
      {state.index_pending && <Badge variant="outline">Indexing pending</Badge>}
      <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => setConfirm(true)}>Unpublish</Button>
    </div> : <div className="flex flex-wrap items-center gap-2 text-sm">
      <span className="text-muted-foreground">Not in the knowledge base.</span>
      {!choosing && <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => setChoosing(true)}><BookUp data-icon="inline-start" />Publish to knowledge base</Button>}
    </div>}
    {state.publish_error && <Alert variant="destructive"><AlertDescription>Publishing v{state.version} failed: {state.publish_error}
      {path && <Button type="button" size="sm" variant="outline" className="ml-2" disabled={busy} onClick={() => void run(() => publishDocument(state.id, folderOf(path)))}>Retry</Button>}</AlertDescription></Alert>}
    {choosing && !path && <div className="flex flex-wrap items-end gap-2">
      <Field className="max-w-xs"><FieldLabel htmlFor="publish-folder">RAG folder</FieldLabel>
        <Input id="publish-folder" value={folder} maxLength={500} onChange={e => setFolder(e.target.value)} />
        <FieldDescription>The latest version is kept in this folder and re-indexed on every new version.</FieldDescription></Field>
      <Button type="button" disabled={busy || !folder.trim()} onClick={() => void run(() => publishDocument(state.id, folder.trim()))}>{busy && <Spinner />}Publish</Button>
      <Button type="button" variant="ghost" disabled={busy} onClick={() => setChoosing(false)}>Cancel</Button>
    </div>}
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <AlertDialog open={confirm} onOpenChange={open => { if (!busy) setConfirm(open); }}><AlertDialogContent>
      <AlertDialogHeader><AlertDialogTitle>Remove from the knowledge base?</AlertDialogTitle>
        <AlertDialogDescription>{path} is deleted from its RAG folder and the folder is re-indexed. The document and its versions stay here.</AlertDialogDescription></AlertDialogHeader>
      <AlertDialogFooter><AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
        <AlertDialogAction disabled={busy} onClick={e => { e.preventDefault(); void run(() => unpublishDocument(state.id)); }}>{busy && <Spinner />}Remove from knowledge base</AlertDialogAction></AlertDialogFooter>
    </AlertDialogContent></AlertDialog>
  </section>;
}
