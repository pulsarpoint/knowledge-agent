import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { ArrowLeft, Plus, Save, Trash2 } from 'lucide-react';
import { activateDocumentationPrompt, deleteDocumentationPrompt, getDocumentationPrompt, getDocumentationPrompts, getPromptTags, saveDocumentationPrompt, type DocumentationPrompt, type DocumentationPromptDetail, type DocumentationPromptInput, type DocumentationPromptVersion } from './api';
import TagsInput from './TagsInput';
import UnsavedDocumentChanges from './UnsavedDocumentChanges';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '~/components/ui/alert-dialog';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Empty, EmptyHeader, EmptyTitle, EmptyDescription } from '~/components/ui/empty';
import { Field, FieldGroup, FieldLabel, FieldDescription } from '~/components/ui/field';
import { Input } from '~/components/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/select';
import { Skeleton } from '~/components/ui/skeleton';
import { Spinner } from '~/components/ui/spinner';
import { Switch } from '~/components/ui/switch';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '~/components/ui/table';
import { Textarea } from '~/components/ui/textarea';

const ALL_TAGS = '__all__';

export default function DocumentationPromptsView() {
  const [rows, setRows] = useState<DocumentationPrompt[] | null>(null);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [tag, setTag] = useState('');
  const [tags, setTags] = useState<string[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    getPromptTags(controller.signal).then((data) => { if (!controller.signal.aborted) setTags(data.tags.map((item) => item.tag)); }).catch(() => {});
    return () => controller.abort();
  }, [refresh]);
  useEffect(() => {
    const controller = new AbortController(); setError('');
    getDocumentationPrompts(controller.signal, tag || undefined).then((data) => { if (!controller.signal.aborted) setRows(data.prompts); })
      .catch((e) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not load prompts'); });
    return () => controller.abort();
  }, [refresh, tag]);
  return <div className="flex flex-col gap-6">
    <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-sm text-muted-foreground">Reusable instructions for agents, organised by tags. New prompt versions remain drafts until activated.</p><Button asChild><Link to="/prompts/new"><Plus data-icon="inline-start" />Create prompt</Link></Button></div>
    {error && <Alert variant="destructive"><AlertDescription>{error} <Button variant="link" onClick={() => setRefresh((n) => n + 1)}>Retry</Button></AlertDescription></Alert>}
    <Select value={tag || ALL_TAGS} onValueChange={(value) => setTag(value === ALL_TAGS ? '' : value)}><SelectTrigger aria-label="Filter by tag" className="w-full sm:w-64"><SelectValue /></SelectTrigger><SelectContent><SelectGroup>
      <SelectItem value={ALL_TAGS}>All tags</SelectItem>{tags.map((item) => <SelectItem key={item} value={item}>{item}</SelectItem>)}
    </SelectGroup></SelectContent></Select>
    {rows === null ? <Skeleton className="h-48 w-full" /> : rows.length === 0 ? tag ? <Empty className="border"><EmptyHeader><EmptyTitle>No prompts have this tag.</EmptyTitle></EmptyHeader></Empty> : <Empty className="border"><EmptyHeader><EmptyTitle>No prompts yet</EmptyTitle><EmptyDescription>Create prompts for documentation, server discovery and other agent work, and tag them so features can find them.</EmptyDescription></EmptyHeader></Empty> :
      <div className="rounded-lg border"><Table><TableHeader><TableRow><TableHead>Prompt</TableHead><TableHead>Tags</TableHead><TableHead>Status</TableHead><TableHead>Active version</TableHead></TableRow></TableHeader><TableBody>{rows.map((prompt) => <TableRow key={prompt.id}>
        <TableCell className="max-w-md whitespace-normal"><Link to={`/prompts/${prompt.id}`} className="font-medium underline-offset-4 hover:underline">{prompt.name}</Link><p className="mt-1 text-sm text-muted-foreground">{prompt.description}</p></TableCell><TableCell><div className="flex flex-wrap gap-1">{prompt.tags.map((item) => <Badge key={item} variant="outline">{item}</Badge>)}</div></TableCell>
        <TableCell><Badge variant={prompt.enabled ? 'secondary' : 'outline'}>{prompt.enabled ? 'Enabled' : 'Disabled'}</Badge></TableCell><TableCell>{prompt.active_version ? `v${prompt.active_version}` : 'None — draft only'}<p className="text-xs text-muted-foreground">{prompt.version_count} saved versions</p></TableCell>
      </TableRow>)}</TableBody></Table></div>}
  </div>;
}

function PromptEditor({ prompt, tagsInUse, onSaved }: { prompt: DocumentationPromptDetail | null; tagsInUse: string[]; onSaved: (id: string) => void }) {
  const navigate = useNavigate();
  const [initial] = useState<DocumentationPromptInput>(() => ({ name: prompt?.name ?? '', description: prompt?.description ?? '', tags: prompt?.tags ?? [], enabled: prompt?.enabled ?? true, content: prompt?.versions[0]?.content ?? '', ...(prompt ? { revision: prompt.revision } : {}) }));
  const [value, setValue] = useState(initial);
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const [viewVersion, setViewVersion] = useState<DocumentationPromptVersion | null>(null);
  const [deleting, setDeleting] = useState<{ version?: DocumentationPromptVersion } | null>(null);
  const leaving = useRef(false);
  const dirty = JSON.stringify(value) !== JSON.stringify(initial);
  async function save(event: React.FormEvent) {
    event.preventDefault(); setPending(true); setError('');
    try { const saved = await saveDocumentationPrompt(value, prompt?.id); leaving.current = true; onSaved(saved.id); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not save prompt'); }
    finally { setPending(false); }
  }
  async function activate(versionId: string) {
    if (!prompt) return;
    setPending(true); setError('');
    try { await activateDocumentationPrompt(prompt.id, versionId, prompt.revision); onSaved(prompt.id); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not activate version'); }
    finally { setPending(false); }
  }
  async function remove() {
    if (!prompt || !deleting) return;
    setPending(true); setError('');
    try {
      await deleteDocumentationPrompt(prompt.id, prompt.revision, deleting.version?.id);
      leaving.current = true;
      if (deleting.version) onSaved(prompt.id); else navigate('/prompts');
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not delete prompt'); }
    finally { setPending(false); }
  }
  return <form onSubmit={save} className="flex flex-col gap-6">
    <UnsavedDocumentChanges dirty={dirty} leaving={leaving} />
    <div className="flex flex-wrap items-center justify-between gap-3"><Button asChild variant="ghost"><Link to="/prompts"><ArrowLeft data-icon="inline-start" />Prompts</Link></Button><div className="flex gap-2">
      {prompt && <Button type="button" variant="outline" disabled={pending} onClick={() => { setError(''); setDeleting({}); }}><Trash2 data-icon="inline-start" />Delete prompt</Button>}
      <Button type="submit" disabled={pending || (!dirty && prompt !== null)}>{pending ? <Spinner /> : <Save data-icon="inline-start" />}{prompt ? 'Save prompt' : 'Create prompt'}</Button>
    </div></div>
    {error && !deleting && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <fieldset disabled={pending} className="flex min-w-0 flex-col gap-6">
      <FieldGroup className="grid gap-4 sm:grid-cols-2"><Field><FieldLabel htmlFor="documentation-prompt-name">Name</FieldLabel><Input id="documentation-prompt-name" required maxLength={160} value={value.name} onChange={(e) => setValue({ ...value, name: e.target.value })} /></Field>
        <Field><FieldLabel htmlFor="documentation-prompt-tags">Tags</FieldLabel><TagsInput id="documentation-prompt-tags" value={value.tags} onChange={(tags) => setValue({ ...value, tags })} suggestions={tagsInUse} /></Field></FieldGroup>
      <Field><FieldLabel htmlFor="documentation-prompt-description">Description</FieldLabel><Textarea id="documentation-prompt-description" rows={2} maxLength={4000} value={value.description} onChange={(e) => setValue({ ...value, description: e.target.value })} /></Field>
      <Field orientation="horizontal"><Switch id="documentation-prompt-enabled" checked={value.enabled} onCheckedChange={(enabled) => setValue({ ...value, enabled })} /><FieldLabel htmlFor="documentation-prompt-enabled">Enabled</FieldLabel></Field>
      <Field><FieldLabel htmlFor="documentation-prompt-content">Prompt text</FieldLabel><Textarea id="documentation-prompt-content" required maxLength={100000} className="min-h-72 font-mono" value={value.content} onChange={(e) => setValue({ ...value, content: e.target.value })} />
        <FieldDescription>Saving changed text creates a new draft version. The active version stays unchanged until you activate another version.</FieldDescription></Field>
    </fieldset>
    {prompt && <section className="flex flex-col gap-4" aria-label="Prompt version history"><h2 className="text-lg font-semibold">Version history</h2>
      {dirty && <p className="text-sm text-muted-foreground">Save your changes before activating or deleting a version.</p>}
      <div className="rounded-lg border"><Table><TableHeader><TableRow><TableHead>Version</TableHead><TableHead>Created</TableHead><TableHead>Actions</TableHead></TableRow></TableHeader><TableBody>{prompt.versions.map((version) => <TableRow key={version.id}>
        <TableCell>v{version.version} {version.id === prompt.active_version_id ? <Badge>Active</Badge> : <Badge variant="outline">{version.activated_at ? 'Inactive' : 'Draft'}</Badge>}</TableCell><TableCell>{new Date(version.created_at).toLocaleString()}</TableCell>
        <TableCell><div className="flex flex-wrap gap-2"><Button type="button" variant="outline" size="sm" disabled={pending} onClick={() => setViewVersion(version)}>Read v{version.version}</Button>
          <Button type="button" variant="outline" size="sm" disabled={pending || dirty || version.id === prompt.active_version_id} onClick={() => void activate(version.id)}>Activate v{version.version}</Button>
          <Button type="button" variant="ghost" size="sm" disabled={pending || dirty || version.id === prompt.active_version_id} onClick={() => { setError(''); setDeleting({ version }); }}>Delete v{version.version}</Button>
        </div></TableCell>
      </TableRow>)}</TableBody></Table></div>
      {viewVersion && <div className="flex flex-col gap-3 rounded-lg border p-4"><div className="flex items-center justify-between gap-3"><h3 className="font-medium">Version {viewVersion.version}</h3><Button type="button" variant="outline" disabled={pending} onClick={() => setValue({ ...value, content: viewVersion.content })}>Use as draft</Button></div><pre className="whitespace-pre-wrap break-words text-sm">{viewVersion.content}</pre></div>}
    </section>}
    <AlertDialog open={deleting !== null} onOpenChange={(open) => { if (!open && !pending) setDeleting(null); }}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>{deleting?.version ? `Delete prompt version ${deleting.version.version}?` : `Delete “${prompt?.name}”?`}</AlertDialogTitle>
      <AlertDialogDescription>{deleting?.version ? 'This permanently removes the inactive version.' : 'This permanently removes the prompt and all of its versions.'} Existing generation snapshots are retained. This cannot be undone.</AlertDialogDescription></AlertDialogHeader>
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      <AlertDialogFooter><AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel><AlertDialogAction className="bg-destructive text-destructive-foreground hover:bg-destructive/90" disabled={pending} onClick={(e) => { e.preventDefault(); void remove(); }}>{pending && <Spinner />}Delete</AlertDialogAction></AlertDialogFooter>
    </AlertDialogContent></AlertDialog>
  </form>;
}

export function DocumentationPromptEditorView() {
  const { promptId } = useParams();
  const navigate = useNavigate();
  const [loaded, setLoaded] = useState<{ prompt: DocumentationPromptDetail | null } | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [tagsInUse, setTagsInUse] = useState<string[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    getPromptTags(controller.signal).then((data) => { if (!controller.signal.aborted) setTagsInUse(data.tags.map((item) => item.tag)); }).catch(() => setTagsInUse([]));
    return () => controller.abort();
  }, []);
  useEffect(() => {
    const controller = new AbortController(); setLoaded(null); setError('');
    (promptId ? getDocumentationPrompt(promptId, controller.signal) : Promise.resolve(null)).then((prompt) => { if (!controller.signal.aborted) setLoaded({ prompt }); })
      .catch((e) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not load prompt'); });
    return () => controller.abort();
  }, [promptId, refresh]);
  return <>{notice && <Alert role="status"><AlertDescription>{notice}</AlertDescription></Alert>}{error ? <Alert variant="destructive"><AlertDescription>{error} <Button variant="link" onClick={() => setRefresh((n) => n + 1)}>Retry</Button></AlertDescription></Alert> : loaded ?
    <PromptEditor key={`${promptId}-${refresh}`} prompt={loaded.prompt} tagsInUse={tagsInUse} onSaved={(id) => { setNotice('Prompt updated. Review the active version below.'); if (promptId === id) setRefresh((n) => n + 1); else navigate(`/prompts/${id}`); }} /> : <Skeleton className="h-96 w-full" />}</>;
}
