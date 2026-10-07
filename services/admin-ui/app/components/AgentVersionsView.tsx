import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { Copy, ExternalLink, Plus } from 'lucide-react';
import { activateAgentVersion, createAgentVersion, getAgentVersions, getVersionSource, type AgentVersion, type AgentVersions, type DraftSource, type InstructionChanges } from '../lib/agentVersions';
import Markdown from './Markdown';
import { Alert, AlertDescription } from './ui/alert';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from './ui/alert-dialog';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Field, FieldDescription, FieldGroup, FieldLabel } from './ui/field';
import { Input } from './ui/input';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from './ui/sheet';
import { Skeleton } from './ui/skeleton';
import { Textarea } from './ui/textarea';

export default function AgentVersionsView({ agentId, dirty, onChanged, testable = true }: { agentId: string; dirty: boolean; onChanged: () => void; testable?: boolean }) {
  const [params, setParams] = useSearchParams();
  const reviewId = params.get('review_id');
  const [data, setData] = useState<AgentVersions>();
  const [source, setSource] = useState<{ version_id?: string; review_id?: string } | null>(reviewId ? { review_id: reviewId } : null);
  const [prepared, setPrepared] = useState<DraftSource>();
  const [changes, setChanges] = useState<InstructionChanges>({ system_prompt: null, instructions: null, server_instructions: [] });
  const [label, setLabel] = useState('');
  const [notes, setNotes] = useState('');
  const [error, setError] = useState('');
  const [draftError, setDraftError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [activation, setActivation] = useState<AgentVersion | null>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => { let live = true; getAgentVersions(agentId).then(value => { if (live) setData(value); }).catch(reason => { if (live) setError(reason.message); }); return () => { live = false; }; }, [agentId, reload]);
  useEffect(() => {
    if (source === null) return;
    let live = true; setPrepared(undefined); setDraftError(''); setLabel(''); setNotes('');
    getVersionSource(agentId,source).then(value => {
      if (!live) return;
      setPrepared(value);
      setChanges({ system_prompt: value.changes.system_prompt ?? value.config.system_prompt.content,
        instructions: value.changes.instructions ?? value.config.instructions,
        server_instructions: value.config.ssh.servers.map(server => ({ server_id: server.id,
          instructions: value.changes.server_instructions.find(change => change.server_id === server.id)?.instructions ?? server.instructions ?? '' })) });
      setLabel(source.review_id ? 'From conversation feedback' : 'New version');
    }).catch(reason => { if (live) setDraftError(reason.message); });
    return () => { live = false; };
  }, [agentId, source]);
  function closeDraft() {
    setSource(null);
    if (reviewId) setParams(current => { current.delete('review_id'); return current; }, { replace: true });
  }
  async function save() {
    if (!prepared || source === null) return;
    setBusy(true); setDraftError('');
    try {
      const editedInstructions = { ...changes, server_instructions: changes.server_instructions.filter(change => {
        const server = prepared.config.ssh.servers.find(item => item.id === change.server_id)!;
        return change.instructions !== (server.instructions_source === 'agent' ? changes.instructions : server.instructions ?? '');
      }) };
      const saved = await createAgentVersion(agentId,{ label, notes, changes: editedInstructions,
        source_hash: prepared.source_hash, source_version_id: source.version_id, source_review_id: source.review_id });
      setNotice(`Version ${saved.version} saved as a draft. Test it before activating.`); closeDraft(); setReload(value => value + 1); onChanged();
    } catch (reason) { setDraftError(reason instanceof Error ? reason.message : 'Could not save draft.'); }
    finally { setBusy(false); }
  }
  return <div className="flex flex-col gap-5">
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div className="max-w-2xl space-y-2"><h3 className="font-semibold">Configuration versions</h3>
        <p className="text-sm text-muted-foreground">Save the complete agent setup, test alternatives in separate conversations, then activate the version you prefer. Existing sessions keep their original configuration.</p>
        <p className="text-sm text-muted-foreground">Saved versions stay unchanged. Use “Copy as draft” to revise one, or save the working configuration from the other tabs.</p>
      </div>
      <Button disabled={dirty || busy} onClick={() => setSource({})}><Plus data-icon="inline-start" />Create draft from settings</Button>
    </div>
    {dirty && <p className="text-sm text-muted-foreground">Save or discard your edits in the configuration tabs first.</p>}
    {error && <Alert variant="destructive"><AlertDescription>{error}<Button variant="outline" onClick={() => { setError(''); setReload(value => value + 1); }}>Reload versions</Button></AlertDescription></Alert>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
    {!data ? <Skeleton className="h-32 w-full" /> : data.versions.length === 0 ? <p className="py-8 text-sm text-muted-foreground">No saved versions yet. Creating your first draft also preserves the current setup as version 1.</p> :
      <div className="divide-y rounded-lg border">{data.versions.map(version => <div key={version.id} className="flex flex-wrap items-center justify-between gap-4 p-4">
        <div className="min-w-0 space-y-1"><div className="flex flex-wrap items-center gap-2"><span className="font-medium">v{version.version} · {version.label}</span><Badge variant={version.status === 'active' ? 'default' : 'outline'}>{version.status}</Badge></div>
          <p className="text-xs text-muted-foreground">{new Date(version.created_at).toLocaleString()}</p>{version.notes && <p className="max-w-2xl whitespace-pre-wrap text-sm text-muted-foreground">{version.notes}</p>}</div>
        <div className="flex flex-wrap gap-2">
          {testable && <Button variant="outline" size="sm" asChild><Link to={`?view=test&version_id=${version.id}`} target="_blank" rel="noopener noreferrer">Test v{version.version}<ExternalLink data-icon="inline-end" /></Link></Button>}
          <Button variant="ghost" size="sm" disabled={dirty || busy} onClick={() => setSource({ version_id: version.id })}><Copy data-icon="inline-start" />Copy as draft</Button>
          {version.status !== 'active' && <Button variant="outline" size="sm" disabled={dirty || busy} onClick={() => setActivation(version)}>Activate v{version.version}</Button>}
        </div>
      </div>)}</div>}
    <Sheet open={source !== null} onOpenChange={open => { if (!open && !busy) closeDraft(); }}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-3xl">
        <SheetHeader><SheetTitle>Prepare a draft version</SheetTitle><SheetDescription>Review the proposed instructions before saving. Model, permissions, skills and resource selections are copied from the source configuration.</SheetDescription></SheetHeader>
        <div className="mt-6 flex flex-col gap-5">
          {draftError && <Alert variant="destructive"><AlertDescription>{draftError}</AlertDescription></Alert>}
          {!prepared ? <Skeleton className="h-48 w-full" /> : <form onSubmit={event => { event.preventDefault(); void save(); }}>
            <FieldGroup>
              {prepared.suggestion && <details><summary className="cursor-pointer text-sm font-medium">Reviewer’s suggestions</summary><div className="mt-3"><Markdown content={prepared.suggestion} /></div></details>}
              <Field><FieldLabel htmlFor="version-label">Version label</FieldLabel><Input id="version-label" required maxLength={160} value={label} onChange={event => setLabel(event.target.value)} disabled={busy} /></Field>
              <Field><FieldLabel htmlFor="version-notes">Version notes</FieldLabel><Textarea id="version-notes" rows={2} maxLength={20000} value={notes} onChange={event => setNotes(event.target.value)} disabled={busy} /><FieldDescription>Describe what you want to test. These notes are not sent to the agent.</FieldDescription></Field>
              <Field><FieldLabel htmlFor="version-system-prompt">System prompt</FieldLabel><Textarea id="version-system-prompt" required rows={10} maxLength={100000} value={changes.system_prompt ?? ''} onChange={event => setChanges(current => ({ ...current, system_prompt: event.target.value }))} disabled={busy} /></Field>
              <Field><FieldLabel htmlFor="version-instructions">Additional instructions</FieldLabel><Textarea id="version-instructions" rows={7} maxLength={40000} value={changes.instructions ?? ''} onChange={event => setChanges(current => ({ ...current, instructions: event.target.value,
                server_instructions: current.server_instructions.map(change => prepared.config.ssh.servers.find(server => server.id === change.server_id)?.instructions_source === 'agent' && change.instructions === current.instructions ? { ...change, instructions: event.target.value } : change) }))} disabled={busy} /></Field>
              {prepared.config.ssh.servers.map(server => <Field key={server.id}><FieldLabel htmlFor={`version-server-${server.id}`}>{server.name} instructions</FieldLabel><Textarea id={`version-server-${server.id}`} rows={4} maxLength={40000} value={changes.server_instructions.find(change => change.server_id === server.id)?.instructions ?? ''} disabled={busy}
                onChange={event => setChanges(current => ({ ...current, server_instructions: current.server_instructions.map(change => change.server_id === server.id ? { ...change, instructions: event.target.value } : change) }))} /></Field>)}
              <details><summary className="cursor-pointer text-sm font-medium">Full configuration to save</summary><pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify({ ...prepared.config, system_prompt: { ...prepared.config.system_prompt, content: changes.system_prompt }, instructions: changes.instructions,
                ssh: { ...prepared.config.ssh, servers: prepared.config.ssh.servers.map(server => ({ ...server, instructions: changes.server_instructions.find(change => change.server_id === server.id)?.instructions ?? server.instructions })) } },null,2)}</pre></details>
              <div className="flex gap-2"><Button disabled={busy || !label.trim() || !changes.system_prompt?.trim()}>{busy ? 'Saving…' : 'Save draft version'}</Button><Button type="button" variant="outline" disabled={busy} onClick={closeDraft}>Cancel</Button></div>
            </FieldGroup>
          </form>}
        </div>
      </SheetContent>
    </Sheet>
    <AlertDialog open={activation !== null} onOpenChange={open => { if (!open && !busy) setActivation(null); }}><AlertDialogContent>
      <AlertDialogHeader><AlertDialogTitle>Activate version {activation?.version}?</AlertDialogTitle><AlertDialogDescription>New conversations and delegated tasks will use “{activation?.label}”. Existing sessions keep their current version. You can reactivate an earlier version later.</AlertDialogDescription></AlertDialogHeader>
      <AlertDialogFooter><AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel><AlertDialogAction disabled={busy} onClick={event => { event.preventDefault(); if (!activation || !data) return; setBusy(true); setError('');
        void activateAgentVersion(agentId,activation.id,data.active_id).then(() => { setNotice(`Version ${activation.version} is now active.`); setActivation(null); setReload(value => value + 1); onChanged(); }).catch(reason => { setActivation(null); setError(reason.message); }).finally(() => setBusy(false));
      }}>Activate version</AlertDialogAction></AlertDialogFooter>
    </AlertDialogContent></AlertDialog>
  </div>;
}
