import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { FileText, Plus, RefreshCw } from 'lucide-react';
import { getDocuments, getDocumentResources, type DocumentResources, type DocumentSummary } from '~/lib/documents';
import { discardDraft, getNewDrafts, type DraftSummary } from '~/lib/drafts';
import { DraftAuthor } from './DraftView';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle } from '~/components/ui/alert-dialog';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Checkbox } from '~/components/ui/checkbox';
import { Empty, EmptyHeader, EmptyMedia, EmptyTitle, EmptyDescription } from '~/components/ui/empty';
import { Field, FieldLabel } from '~/components/ui/field';
import { Input } from '~/components/ui/input';
import { Label } from '~/components/ui/label';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/select';
import { Skeleton } from '~/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '~/components/ui/table';

const draftsLabel = (count: number) => count === 1 ? '1 draft' : `${count} drafts`;

export default function DocumentsView() {
  const [params, setParams] = useSearchParams();
  const [rows, setRows] = useState<DocumentSummary[]>([]);
  const [resources, setResources] = useState<DocumentResources | null>(null);
  const [newDrafts, setNewDrafts] = useState<DraftSummary[]>([]);
  const [discarding, setDiscarding] = useState<DraftSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const offset = Number(params.get('offset') || 0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    Promise.all([getDocuments(params, controller.signal), getDocumentResources(controller.signal), getNewDrafts(controller.signal)]).then(([data, assets, drafts]) => {
      if (!controller.signal.aborted) { setRows(data.documents); setTotal(data.total); setResources(assets); setNewDrafts(drafts.drafts); }
    }).catch((e) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not load documents'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [params, refresh]);
  function filter(key: string, value: string) {
    setParams((old) => { const next = new URLSearchParams(old); value && value !== 'all' ? next.set(key, value) : next.delete(key); if (key !== 'offset') next.delete('offset'); return next; }, { replace: true });
  }
  async function discard() {
    if (!discarding) return;
    setBusy(true); setError('');
    try { await discardDraft(discarding.id, discarding.revision); setDiscarding(null); setRefresh((n) => n + 1); }
    catch (e) { setDiscarding(null); setError(e instanceof Error ? e.message : 'Could not discard the draft'); }
    finally { setBusy(false); }
  }
  return <div className="flex flex-col gap-6">
    <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-sm text-muted-foreground">{total} documents</p><div className="flex gap-2">
      <Button variant="outline" disabled={loading} onClick={() => setRefresh((n) => n + 1)}><RefreshCw data-icon="inline-start" />Refresh</Button>
      <Button asChild variant="outline"><Link to="/documentation/generations">Generate documents</Link></Button>
      <Button asChild><Link to="/documents/new"><Plus data-icon="inline-start" />Create document</Link></Button>
    </div></div>
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      <Field><FieldLabel htmlFor="document-query">Search documents</FieldLabel><Input id="document-query" placeholder="Title, slug or description" value={params.get('q') || ''} onChange={(e) => filter('q', e.target.value)} /></Field>
      {(['code_projects', 'services', 'servers', 'environments'] as const).map((kind) => {
        const key = { code_projects: 'code_project_id', services: 'service_id', servers: 'server_id', environments: 'environment_id' }[kind];
        const label = { code_projects: 'Code project', services: 'Service', servers: 'Server', environments: 'Environment' }[kind];
        return <Field key={kind}><FieldLabel htmlFor={`filter-${kind}`}>{label}</FieldLabel>
          <Select value={params.get(key) || 'all'} onValueChange={(v) => filter(key, v)}><SelectTrigger id={`filter-${kind}`}><SelectValue /></SelectTrigger><SelectContent><SelectGroup>
            <SelectItem value="all">All {label.toLowerCase()}s</SelectItem>{resources?.[kind]?.map((item) => <SelectItem key={item.id} value={item.id}>{item.name}</SelectItem>)}
          </SelectGroup></SelectContent></Select></Field>;
      })}
      <div className="flex items-center gap-2 self-end pb-2"><Checkbox id="filter-has-drafts" checked={params.get('has_drafts') === 'true'}
        onCheckedChange={(on) => filter('has_drafts', on === true ? 'true' : '')} /><Label htmlFor="filter-has-drafts">Has drafts</Label></div>
    </div>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {newDrafts.length > 0 && <section aria-label="New drafts" className="flex flex-col gap-2">
      <h2 className="text-sm font-semibold">New drafts</h2>
      <div className="rounded-lg border"><Table><TableHeader><TableRow><TableHead>Title</TableHead><TableHead>Folder</TableHead><TableHead>Written by</TableHead><TableHead>Updated</TableHead><TableHead /></TableRow></TableHeader><TableBody>
        {newDrafts.map((draft) => <TableRow key={draft.id}>
          <TableCell><Link className="font-medium underline-offset-4 hover:underline" to={`/documents/drafts/${draft.id}`}>{draft.title}</Link></TableCell>
          <TableCell>{draft.folder ?? '—'}</TableCell><TableCell><DraftAuthor draft={draft} /></TableCell>
          <TableCell>{new Date(draft.updated_at).toLocaleString()}</TableCell>
          <TableCell><div className="flex justify-end gap-2">
            <Button asChild size="sm" variant="outline"><Link to={`/documents/drafts/${draft.id}`} aria-label={`Open ${draft.title}`}>Open</Link></Button>
            <Button size="sm" variant="outline" aria-label={`Discard ${draft.title}`} onClick={() => setDiscarding(draft)}>Discard</Button></div></TableCell>
        </TableRow>)}
      </TableBody></Table></div>
    </section>}
    {loading ? <Skeleton className="h-48 w-full" /> : !error && (rows.length === 0 ? <Empty className="border"><EmptyHeader><EmptyMedia variant="icon"><FileText /></EmptyMedia>
      <EmptyTitle>{total === 0 && params.size === 0 ? 'No documents yet' : 'No matching documents'}</EmptyTitle><EmptyDescription>Create a Markdown document and connect it to the projects, services, and servers it describes.</EmptyDescription></EmptyHeader></Empty> : <>
      <div className="rounded-lg border"><Table><TableHeader><TableRow><TableHead>Document</TableHead><TableHead>Purpose / audience</TableHead><TableHead>Version</TableHead><TableHead>Drafts</TableHead><TableHead>Updated</TableHead></TableRow></TableHeader><TableBody>
        {rows.map((doc) => <TableRow key={doc.id}><TableCell className="max-w-md whitespace-normal"><Link className="font-medium underline-offset-4 hover:underline" to={`/documents/${doc.id}`}>{doc.title}</Link><p className="mt-1 text-sm text-muted-foreground">{doc.description}</p>
          <p className="mt-1 text-xs text-muted-foreground">{doc.code_project_ids.length} code projects · {doc.service_ids.length} services · {doc.server_ids.length} servers</p></TableCell>
          <TableCell><p>{doc.purpose}</p><span className="text-sm text-muted-foreground">{doc.audience === 'both' ? 'People and agents' : doc.audience === 'people' ? 'People' : 'Agents'}</span></TableCell>
          <TableCell><Badge variant="secondary">v{doc.version}</Badge></TableCell>
          <TableCell>{(doc.draft_count ?? 0) > 0 ? <Link className="underline-offset-4 hover:underline" to={`/documents/${doc.id}?tab=drafts`}>{draftsLabel(doc.draft_count!)}</Link> : <span className="text-muted-foreground">—</span>}</TableCell>
          <TableCell>{new Date(doc.updated_at).toLocaleString()}</TableCell></TableRow>)}
      </TableBody></Table></div>
    </>)}
    {total > 50 && <div className="flex items-center justify-between gap-3"><span className="text-sm text-muted-foreground">{offset + 1}–{Math.min(offset + 50, total)} of {total}</span><div className="flex gap-2">
      <Button variant="outline" disabled={loading || offset === 0} onClick={() => filter('offset', String(Math.max(0, offset - 50)))}>Previous</Button><Button variant="outline" disabled={loading || offset + 50 >= total} onClick={() => filter('offset', String(offset + 50))}>Next</Button>
    </div></div>}
    <AlertDialog open={discarding !== null} onOpenChange={(open) => { if (!open && !busy) setDiscarding(null); }}><AlertDialogContent>
      <AlertDialogHeader><AlertDialogTitle>{`Discard “${discarding?.title ?? ''}”?`}</AlertDialogTitle>
        <AlertDialogDescription>The draft is no longer listed (it stays in the audit history). No document is created.</AlertDialogDescription></AlertDialogHeader>
      <AlertDialogFooter><AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
        <AlertDialogAction disabled={busy} onClick={(e) => { e.preventDefault(); void discard(); }}>Discard draft</AlertDialogAction></AlertDialogFooter>
    </AlertDialogContent></AlertDialog>
  </div>;
}
