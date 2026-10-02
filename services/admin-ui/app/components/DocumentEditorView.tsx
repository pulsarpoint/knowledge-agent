import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { ArrowLeft, Save, Trash2 } from 'lucide-react';
import { deleteDocument, exportDocument, getDocument, getDocumentResources, getDocumentVersion, saveDocument, type DocumentDetail, type DocumentInput, type DocumentResources, type DocumentVersion } from '~/lib/documents';
import Markdown from './Markdown';
import DocumentResourceSelection from './DocumentResourceSelection';
import UnsavedDocumentChanges from './UnsavedDocumentChanges';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '~/components/ui/alert-dialog';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Field, FieldGroup, FieldLabel, FieldDescription } from '~/components/ui/field';
import { Input } from '~/components/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/select';
import { Skeleton } from '~/components/ui/skeleton';
import { Spinner } from '~/components/ui/spinner';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '~/components/ui/tabs';
import { Textarea } from '~/components/ui/textarea';

function Editor({ document, resources, onSaved }: { document: DocumentDetail | null; resources: DocumentResources; onSaved: (id: string) => void }) {
  const navigate = useNavigate();
  const [initial] = useState<DocumentInput>(() => document ? {
    slug: document.slug, title: document.title, description: document.description, purpose: document.purpose, audience: document.audience,
    tags: document.tags, markdown: document.current_version.markdown, code_project_ids: document.code_project_ids,
    service_ids: document.service_ids, server_ids: document.server_ids, deployment_ids: document.deployment_ids, revision: document.revision, environment_ids: document.environment_ids ?? [],
    source_commits: Object.fromEntries(document.current_version.source_snapshot.code_projects.filter((r) => r.git_commit).map((r) => [r.id, r.git_commit!])),
  } : { slug: '', title: '', description: '', purpose: 'explanation', audience: 'both', tags: [], markdown: '', code_project_ids: [], service_ids: [], server_ids: [], deployment_ids: [], environment_ids: [] });
  const [value, setValue] = useState(initial);
  const [tags, setTags] = useState(initial.tags.join(', '));
  const [tab, setTab] = useState('write');
  const [historical, setHistorical] = useState<DocumentVersion | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [confirmDelete, setConfirmDelete] = useState(false);
  const leaving = useRef(false);
  const dirty = JSON.stringify(value) !== JSON.stringify(initial) || tags !== initial.tags.join(', ');
  const patch = <K extends keyof DocumentInput>(key: K, next: DocumentInput[K]) => setValue((old) => ({ ...old, [key]: next }));

  async function save(event: React.FormEvent) {
    event.preventDefault(); setPending(true); setError('');
    try {
      const saved = await saveDocument({ ...value, tags: tags.split(',').map((tag) => tag.trim()).filter(Boolean) }, document?.id);
      leaving.current = true; onSaved(saved.id);
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not save document'); }
    finally { setPending(false); }
  }
  async function openVersion(id: string) {
    if (!document) return;
    setPending(true); setError('');
    try { setHistorical(await getDocumentVersion(document.id, id)); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not load version'); }
    finally { setPending(false); }
  }
  async function remove() {
    if (!document) return;
    setPending(true); setError('');
    try { await deleteDocument(document.id, document.revision); leaving.current = true; navigate('/documents'); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not delete document'); }
    finally { setPending(false); }
  }
  return <form onSubmit={save} className="flex flex-col gap-6">
    <UnsavedDocumentChanges dirty={dirty} leaving={leaving} />
    <div className="flex flex-wrap items-center justify-between gap-3"><Button asChild variant="ghost"><Link to="/documents"><ArrowLeft data-icon="inline-start" />Documents</Link></Button><div className="flex items-center gap-2">
      {document && <Button type="button" variant="outline" disabled={pending || dirty} onClick={async () => {
        setPending(true); setError('');
        try { const result = await exportDocument(document.id); setNotice(`Exported to ${result.folder}`); }
        catch (e) { setError(e instanceof Error ? e.message : 'Could not export document'); }
        finally { setPending(false); }
      }}>Export to folder</Button>}
      {document && <Button asChild type="button" variant="outline"><a href={`/api/documentation/documents/${document.id}/bundle`}>Download document folder</a></Button>}
      {document && <Badge variant="secondary">Version {document.version}</Badge>}
      {document && <Button type="button" variant="outline" disabled={pending} onClick={() => { setError(''); setConfirmDelete(true); }}><Trash2 data-icon="inline-start" />Delete</Button>}
      <Button type="submit" disabled={pending || !value.markdown.trim() || (!dirty && document !== null)}>{pending ? <Spinner /> : <Save data-icon="inline-start" />}{document ? 'Save new version' : 'Create document'}</Button>
    </div></div>
    {document?.workflows?.map(workflow => <Button key={workflow.id} asChild variant="outline" className="self-start"><Link to={`/documentation/generations/${workflow.id}`}>View phases / regenerate · {workflow.name}</Link></Button>)}
    {error && !confirmDelete && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {notice && <Alert role="status"><AlertDescription>{notice}</AlertDescription></Alert>}
    <fieldset disabled={pending} className="flex min-w-0 flex-col gap-6">
      <FieldGroup className="grid gap-4 sm:grid-cols-2"><Field><FieldLabel htmlFor="doc-title">Title</FieldLabel><Input id="doc-title" required maxLength={240} value={value.title} onChange={(e) => {
        const title = e.target.value;
        setValue((old) => ({ ...old, title, ...(!document && (old.slug === '' || old.slug === old.title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 120)) ? { slug: title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 120) } : {}) }));
      }} /></Field><Field><FieldLabel htmlFor="doc-slug">Slug</FieldLabel><Input id="doc-slug" required maxLength={120} pattern="[a-z0-9]+(-[a-z0-9]+)*" value={value.slug} onChange={(e) => patch('slug', e.target.value)} /><FieldDescription>Unique name using lowercase letters, numbers, and hyphens.</FieldDescription></Field></FieldGroup>
      <Field><FieldLabel htmlFor="doc-description">Short description</FieldLabel><Textarea id="doc-description" rows={2} maxLength={4000} value={value.description} onChange={(e) => patch('description', e.target.value)} /></Field>
      <FieldGroup className="grid gap-4 sm:grid-cols-3">
        <Field><FieldLabel htmlFor="doc-purpose">Purpose</FieldLabel><Input id="doc-purpose" list="document-purposes" required maxLength={80} value={value.purpose} onChange={(e) => patch('purpose', e.target.value)} />
          <datalist id="document-purposes">{['explanation', 'onboarding', 'architecture', 'how-to', 'reference', 'runbook', 'troubleshooting'].map((p) => <option key={p} value={p} />)}</datalist></Field>
        <Field><FieldLabel htmlFor="doc-audience">Audience</FieldLabel><Select value={value.audience} onValueChange={(v) => patch('audience', v as DocumentInput['audience'])}><SelectTrigger id="doc-audience"><SelectValue /></SelectTrigger><SelectContent><SelectGroup>
          <SelectItem value="both">People and agents</SelectItem><SelectItem value="people">People</SelectItem><SelectItem value="agents">Agents</SelectItem>
        </SelectGroup></SelectContent></Select></Field>
        <Field><FieldLabel htmlFor="doc-tags">Tags</FieldLabel><Input id="doc-tags" placeholder="Comma-separated tags" value={tags} onChange={(e) => setTags(e.target.value)} /></Field>
      </FieldGroup>
      <Tabs value={tab} onValueChange={setTab}><TabsList><TabsTrigger value="write">Write</TabsTrigger><TabsTrigger value="preview">Preview</TabsTrigger><TabsTrigger value="resources">Linked resources</TabsTrigger><TabsTrigger value="history" disabled={!document}>Version history</TabsTrigger></TabsList>
        <TabsContent value="write" forceMount className="data-[state=inactive]:hidden"><Field><FieldLabel htmlFor="doc-markdown">Markdown</FieldLabel><Textarea id="doc-markdown" className="min-h-96 font-mono" required maxLength={1000000} value={value.markdown} onChange={(e) => patch('markdown', e.target.value)} />
          <FieldDescription>Every save preserves a complete version. Documents are stored here; RAG publishing will be added separately.</FieldDescription></Field></TabsContent>
        <TabsContent value="preview"><div className="min-h-96 rounded-lg border p-4">{value.markdown ? <Markdown content={value.markdown} /> : <p className="text-sm text-muted-foreground">Write Markdown to preview the document.</p>}</div></TabsContent>
        <TabsContent value="resources"><DocumentResourceSelection resources={resources} value={value} onChange={(next) => setValue({ ...next,
          source_commits: Object.fromEntries(Object.entries(next.source_commits ?? {}).filter(([id]) => next.code_project_ids.includes(id))),
        })} />
          {Object.keys(value.source_commits ?? {}).length > 0 && <div className="mt-4 flex flex-col gap-2"><h3 className="font-medium">Documented source commits</h3>
            {Object.entries(value.source_commits ?? {}).map(([id, commit]) => <p key={id} className="text-sm">{resources.code_projects.find((p) => p.id === id)?.name ?? id}: <code className="break-all">{commit}</code></p>)}
          </div>}
        </TabsContent>
        <TabsContent value="history"><div className="grid gap-6 md:grid-cols-[240px_minmax(0,1fr)]"><div className="flex flex-col gap-2">{document?.versions.map((version) => <Button key={version.id} type="button" variant={historical?.id === version.id ? 'secondary' : 'outline'} className="h-auto justify-start whitespace-normal py-3 text-left" onClick={() => void openVersion(version.id)}>
          <span>Version {version.version}{version.id === document.current_version_id ? ' · Current' : ''}<span className="block text-xs font-normal text-muted-foreground">{new Date(version.created_at).toLocaleString()}</span></span>
        </Button>)}</div><div className="min-w-0 rounded-lg border p-4">{historical ? <>
          <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="font-semibold">{historical.title} · v{historical.version}</h2><Button type="button" variant="outline" onClick={() => {
            setValue({ ...value, title: historical.title, description: historical.description, purpose: historical.purpose, audience: historical.audience, tags: historical.tags, markdown: historical.markdown,
              environment_ids: (historical.source_snapshot.environments ?? []).map((r) => r.id),
              source_commits: Object.fromEntries(historical.source_snapshot.code_projects.filter((r) => r.git_commit).map((r) => [r.id, r.git_commit!])),
              code_project_ids: historical.source_snapshot.code_projects.map((r) => r.id), service_ids: historical.source_snapshot.services.map((r) => r.id), server_ids: historical.source_snapshot.servers.map((r) => r.id), deployment_ids: historical.source_snapshot.deployments.map((r) => r.id) });
            setTags(historical.tags.join(', ')); setTab('write'); setNotice(`Version ${historical.version} loaded into the editor. Save to create a new version.`);
          }}>Use version as draft</Button></div>
          <p className="mt-2 text-sm text-muted-foreground">{historical.purpose} · {historical.audience} · {historical.origin}</p>
          <p className="mt-2 text-sm text-muted-foreground">Resources at save time: {Object.values(historical.source_snapshot).flat().map((r) => r.name).join(', ') || 'None'}</p>
          {historical.source_snapshot.code_projects.filter((r) => r.git_commit).map((r) => <p key={r.id} className="mt-2 text-sm">{r.name}: <code className="break-all">{r.git_commit}</code></p>)}
          <Markdown content={historical.markdown} />
        </> : <p className="text-sm text-muted-foreground">Select a version to read it or use it as the starting point for an edit.</p>}</div></div></TabsContent>
      </Tabs>
    </fieldset>
    <AlertDialog open={confirmDelete} onOpenChange={(open) => { if (!pending) setConfirmDelete(open); }}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Delete “{document?.title}”?</AlertDialogTitle>
      <AlertDialogDescription>This removes the document, all saved versions, and its resource links. The linked code projects, services, and servers remain. This cannot be undone.</AlertDialogDescription></AlertDialogHeader>
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      <AlertDialogFooter><AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel><AlertDialogAction className="bg-destructive text-destructive-foreground hover:bg-destructive/90" disabled={pending} onClick={(e) => { e.preventDefault(); void remove(); }}>{pending && <Spinner />}Delete document</AlertDialogAction></AlertDialogFooter>
    </AlertDialogContent></AlertDialog>
  </form>;
}

export default function DocumentEditorView() {
  const { documentId } = useParams();
  const navigate = useNavigate();
  const [loaded, setLoaded] = useState<{ document: DocumentDetail | null; resources: DocumentResources } | null>(null);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [notice, setNotice] = useState('');
  useEffect(() => {
    const controller = new AbortController(); setLoaded(null); setError('');
    Promise.all([documentId ? getDocument(documentId, controller.signal) : Promise.resolve(null), getDocumentResources(controller.signal)]).then(([document, resources]) => {
      if (!controller.signal.aborted) setLoaded({ document, resources });
    }).catch((e) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not open document'); });
    return () => controller.abort();
  }, [documentId, refresh]);
  return <>{notice && <Alert role="status"><AlertDescription>{notice}</AlertDescription></Alert>}{error ? <Alert variant="destructive"><AlertDescription>{error} <Button variant="link" onClick={() => setRefresh((n) => n + 1)}>Retry</Button></AlertDescription></Alert> : loaded ?
    <Editor key={`${documentId}-${refresh}`} {...loaded} onSaved={(id) => { setNotice('Document version saved.'); if (documentId === id) setRefresh((n) => n + 1); else navigate(`/documents/${id}`); }} /> : <Skeleton className="h-96 w-full" />}</>;
}
