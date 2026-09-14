import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useLocation } from 'react-router';
import { Plus, RefreshCw, Trash2 } from 'lucide-react';
import { ApiError } from '../lib/api';
import { activatePrompt, agentNames, deletePrompt, getPrompts, savePrompt, type AgentName, type Prompts, type PromptVersion } from '../lib/prompts';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '~/components/ui/alert-dialog';
import { Badge } from '~/components/ui/badge';
import { Button, buttonVariants } from '~/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '~/components/ui/card';
import { Field, FieldDescription, FieldGroup, FieldLabel } from '~/components/ui/field';
import { Input } from '~/components/ui/input';
import { Spinner } from '~/components/ui/spinner';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '~/components/ui/table';
import { Textarea } from '~/components/ui/textarea';

export default function PromptsView({ agent = "search", agentId = agent, agentLabel, active = true, onActivated }: { agent?: AgentName; agentLabel?: string; agentId?: string; active?: boolean; onActivated?: () => void }) {
  const section = useRef<HTMLElement>(null);
  const { hash } = useLocation();
  const [data, setData] = useState<Prompts | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ version?: PromptVersion; title: string; content: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [activation, setActivation] = useState<PromptVersion | null>(null);
  const [deletion, setDeletion] = useState<PromptVersion | null>(null);
  const prompt = data?.agents.find(row => row.agent === agentId);
  const versions = data?.versions.filter(row => row.agent === agentId) ?? [];
  const selected = versions.find(row => row.id === selectedId) ?? versions.find(row => row.id === prompt?.active_version_id);

  useEffect(() => {
    if (active && hash === `#system-prompts-${agentId}`) section.current?.scrollIntoView({ block: 'start' });
  }, [active, agentId, hash]);

  useEffect(() => {
    let cancelled = false;
    getPrompts().then(value => { if (!cancelled) setData(value); })
      .catch(reason => { if (!cancelled) setError(reason instanceof ApiError ? reason.message : 'Could not load prompts.'); });
    return () => { cancelled = true; };
  }, []);

  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError(null); setNotice(null);
    try { await action(); }
    catch (reason) { setError(reason instanceof ApiError ? reason.message : 'Could not update prompts. Try reloading.'); }
    finally { setBusy(false); }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!editing) return;
    await run(async () => {
      const saved = await savePrompt(agentId, editing.title, editing.content, editing.version);
      setData(await getPrompts()); setSelectedId(saved.id); setEditing(null);
      setNotice(`Version ${saved.version} saved as draft. The active prompt has not changed.`);
    });
  }

  return <section ref={section} id={`system-prompts-${agentId}`} aria-label={`${agentLabel ?? agentNames[agent]} system prompts`} className="flex flex-col gap-6 scroll-mt-6">
    <div className="flex flex-wrap items-end justify-between gap-4">
      <h2 className="text-lg font-semibold">System prompt</h2>
      <Button variant="outline" disabled={busy || editing !== null} onClick={() => void run(async () => { setData(await getPrompts()); onActivated?.(); })}><RefreshCw data-icon="inline-start" />Reload prompts</Button>
    </div>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {notice && <Alert><AlertDescription>{notice}</AlertDescription></Alert>}
    {!data && !error && <Spinner />}
    {prompt && <Alert><AlertDescription>
      <span>Active: {versions.find(row => row.id === prompt.active_version_id)?.title} · version {versions.find(row => row.id === prompt.active_version_id)?.version}.</span> New versions start as drafts. Activation applies to new requests for this agent; running requests keep their current prompt.
    </AlertDescription></Alert>}
    {data && <Card>
      <CardHeader><CardTitle>Prompt versions</CardTitle><CardDescription>Review a version, create a draft from it, or reactivate a previously published version.</CardDescription></CardHeader>
      <CardContent><Table><TableHeader><TableRow><TableHead>Version</TableHead><TableHead>Title</TableHead><TableHead>Status</TableHead><TableHead>Updated</TableHead><TableHead><span className="sr-only">Actions</span></TableHead></TableRow></TableHeader>
        <TableBody>{versions.map(version => <TableRow key={version.id} data-state={selected?.id === version.id ? 'selected' : undefined}>
          <TableCell>v{version.version}</TableCell><TableCell>{version.title}</TableCell><TableCell><Badge variant={version.status === 'active' ? 'default' : 'outline'}>{version.status}</Badge></TableCell><TableCell>{new Date(version.updated_at).toLocaleString()}</TableCell>
          <TableCell><div className="flex justify-end gap-2">
            <Button variant="outline" size="sm" disabled={busy || editing !== null} onClick={() => setSelectedId(version.id)}>View<span className="sr-only"> version {version.version}</span></Button>
            {version.status !== 'active' && <Button variant="outline" size="sm" disabled={busy || editing !== null} onClick={() => { setError(null); setDeletion(version); }}><Trash2 data-icon="inline-start" />Delete<span className="sr-only"> version {version.version}</span></Button>}
          </div></TableCell>
        </TableRow>)}</TableBody>
      </Table></CardContent>
      <CardFooter><p className="text-sm text-muted-foreground">Published content is read-only. You can delete drafts and archived versions; the active version is protected.</p></CardFooter>
    </Card>}
    {editing ? <Card>
      <CardHeader><CardTitle>{editing.version ? `Edit draft v${editing.version.version}` : 'New draft'}</CardTitle><CardDescription>Saving this draft does not activate it.</CardDescription></CardHeader>
      <form onSubmit={event => void save(event)} className="flex flex-col gap-6">
        <CardContent><FieldGroup>
          <Field><FieldLabel htmlFor={`prompt-title-${agent}`}>Version title</FieldLabel><Input id={`prompt-title-${agent}`} required maxLength={100} disabled={busy} value={editing.title} onChange={event => setEditing({ ...editing, title: event.target.value })} /></Field>
          <Field><FieldLabel htmlFor={`prompt-content-${agent}`}>System prompt</FieldLabel><Textarea id={`prompt-content-${agent}`} required rows={18} maxLength={40000} disabled={busy} value={editing.content} onChange={event => setEditing({ ...editing, content: event.target.value })} />
            <FieldDescription>Write plain instructions. The app adds available tools, access restrictions and the context required by each agent automatically.</FieldDescription></Field>
        </FieldGroup></CardContent>
        <CardFooter className="gap-2"><Button disabled={busy || !editing.title.trim() || !editing.content.trim()}>{busy && <Spinner />}Save draft</Button><Button type="button" variant="outline" disabled={busy} onClick={() => setEditing(null)}>Cancel</Button></CardFooter>
      </form>
    </Card> : selected && <Card>
      <CardHeader><CardTitle>{selected.title} · v{selected.version}</CardTitle><CardDescription>{selected.status === 'draft' ? 'Draft instructions. These are not used by the agent yet.' : 'Published instructions. Create a new draft to make changes.'}</CardDescription></CardHeader>
      <CardContent><pre className="whitespace-pre-wrap break-words text-sm">{selected.content}</pre></CardContent>
      <CardFooter className="flex-wrap gap-2">
        <Button variant="outline" disabled={busy} onClick={() => { setEditing({ title: `${selected.title}`.slice(0, 100), content: selected.content }); setNotice(null); }}><Plus data-icon="inline-start" />New draft from this version</Button>
        {selected.status === 'draft' && <Button variant="outline" disabled={busy} onClick={() => setEditing({ version: selected, title: selected.title, content: selected.content })}>Edit draft</Button>}
        {selected.status !== 'active' && <Button disabled={busy} onClick={() => { setError(null); setActivation(selected); }}>Set active</Button>}
      </CardFooter>
    </Card>}
    <AlertDialog open={active && activation !== null} onOpenChange={open => { if (!open && !busy) setActivation(null); }}>
      <AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Activate prompt version {activation?.version}?</AlertDialogTitle><AlertDialogDescription>
        This becomes the system prompt for the {(agentLabel ?? agentNames[agent]).toLowerCase()} on new requests. The current active version remains available in history.
      </AlertDialogDescription></AlertDialogHeader>
        {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
        <AlertDialogFooter><AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel><AlertDialogAction disabled={busy} onClick={event => {
          event.preventDefault();
          if (!activation || !prompt) return;
          void run(async () => { await activatePrompt(activation, prompt); setData(await getPrompts()); onActivated?.(); setActivation(null); setNotice(`Version ${activation.version} is now active.`); });
        }}>{busy && <Spinner />}Activate version</AlertDialogAction></AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
    <AlertDialog open={active && deletion !== null} onOpenChange={open => { if (!open && !busy) setDeletion(null); }}>
      <AlertDialogContent aria-busy={busy}><AlertDialogHeader><AlertDialogTitle>Delete prompt version {deletion?.version}?</AlertDialogTitle><AlertDialogDescription className="break-words">
        This permanently deletes “{deletion?.title}” for the {(agentLabel ?? agentNames[agent]).toLowerCase()}. Its saved instructions cannot be restored. The active prompt stays unchanged.
      </AlertDialogDescription></AlertDialogHeader>
        {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
        <AlertDialogFooter><AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel><AlertDialogAction className={buttonVariants({ variant: 'destructive' })} disabled={busy} onClick={event => {
          event.preventDefault();
          if (!deletion || !prompt) return;
          void run(async () => {
            await deletePrompt(deletion, prompt);
            setData(current => current && ({ ...current, versions: current.versions.filter(row => row.id !== deletion.id) }));
            setSelectedId(current => current === deletion.id ? null : current);
            setDeletion(null); setNotice(`Version ${deletion.version} deleted.`);
          });
        }}>{busy && <Spinner data-icon="inline-start" />}Delete version</AlertDialogAction></AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  </section>;
}
