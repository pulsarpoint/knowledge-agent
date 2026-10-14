import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { Copy, MoreHorizontal } from 'lucide-react';
import { toast } from 'sonner';
import { ApiError } from '~/lib/api';
import { applyToOriginal, cloneAgent, getApplyPreview, type ApplyPreview } from './clones';
import { deleteAgent, describeHistory, getAgentHistory, type AgentProfile } from './api';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '~/components/ui/alert-dialog';
import { Button, buttonVariants } from '~/components/ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '~/components/ui/dropdown-menu';

const message = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;
const status = (reason: unknown) => reason instanceof ApiError ? reason.status : undefined;

/** The agent page header: Clone agent; on a clone, Apply to <original> and, once applied, Delete this clone. */
export default function AgentActions({ profile, onChanged }: { profile: AgentProfile; onChanged: () => void }) {
  const navigate = useNavigate();
  const [notClone, setNotClone] = useState(false);  // the API said 404 NOT_A_CLONE (e.g. the original was deleted)
  const original = notClone ? null : profile.original ?? null;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [applying, setApplying] = useState(false);
  const [preview, setPreview] = useState<ApplyPreview | null>(null);
  const [applied, setApplied] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [compareFailed, setCompareFailed] = useState(false);
  // What would go with the clone: undefined while checking, '' without history (or when it could not be checked).
  const [history, setHistory] = useState<string>();
  useEffect(() => {
    if (!deleting) return;
    let live = true; setHistory(undefined);
    getAgentHistory(profile.id).then(value => { if (live) setHistory(describeHistory(value)); }).catch(() => { if (live) setHistory(''); });
    return () => { live = false; };
  }, [deleting, profile.id]);
  function refused(reason: unknown, fallback: string) {
    setError(message(reason, fallback));
    if (status(reason) === 404) { setNotClone(true); setApplying(false); }
  }
  async function clone() {
    setBusy(true); setError('');
    try { const created = await cloneAgent(profile.id); toast.success(`Clone created: ${created.name}`); navigate(`/agents/${encodeURIComponent(created.id)}`); }
    catch (reason) { setError(message(reason, 'Could not clone the agent.')); }
    finally { setBusy(false); }
  }
  async function compare() {
    setPreview(null); setCompareFailed(false);
    try { setPreview(await getApplyPreview(profile.id)); }
    catch (reason) { refused(reason, 'Could not compare the configurations.'); setCompareFailed(status(reason) !== 404); }
  }
  function compareAgain() { setError(''); void compare(); }
  function openApply() { setApplying(true); setError(''); void compare(); }
  async function apply() {
    if (!preview) return;
    setBusy(true); setError('');
    try { await applyToOriginal(profile.id, preview); setApplying(false); setApplied(true); onChanged(); }
    catch (reason) {
      refused(reason, 'Could not apply the configuration.');
      // The original changed since the preview: keep the reason and compare again.
      if (status(reason) === 409) await compare();
    }
    finally { setBusy(false); }
  }
  async function removeClone() {
    if (!original) return;
    setBusy(true); setError('');
    const withHistory = Boolean(history);
    try {
      await deleteAgent(profile.id, profile.revision, withHistory);
      toast.success(`Clone ${profile.name} deleted`); navigate(`/agents/${encodeURIComponent(original.id)}`);
    } catch (reason) {
      setError(message(reason, 'Could not delete the clone.'));
      // History may have appeared since the dialog opened; show it (next to the reason) so it can be deleted too.
      if (!withHistory) await getAgentHistory(profile.id).then(value => { const found = describeHistory(value); if (found) setHistory(found); }).catch(() => undefined);
    }
    finally { setBusy(false); }
  }
  return <div className="flex flex-wrap items-center gap-2">
    {original && <Button variant="outline" size="sm" disabled={busy} onClick={openApply}>Apply to {original.name}</Button>}
    {applied && original && <Button variant="destructive" size="sm" disabled={busy} onClick={() => setDeleting(true)}>Delete this clone</Button>}
    <DropdownMenu modal={false}><DropdownMenuTrigger asChild><Button variant="ghost" size="icon" aria-label={`Actions for ${profile.name}`} disabled={busy}><MoreHorizontal /></Button></DropdownMenuTrigger>
      <DropdownMenuContent align="end"><DropdownMenuItem onSelect={() => void clone()}><Copy />Clone agent</DropdownMenuItem></DropdownMenuContent></DropdownMenu>
    {applied && original && <p role="status" className="text-sm text-muted-foreground">Applied to {original.name}.</p>}
    {error && !applying && !deleting && <p role="alert" className="text-sm text-destructive">{error}</p>}
    <AlertDialog open={applying} onOpenChange={open => { if (!open && !busy) setApplying(false); }}><AlertDialogContent>
      <AlertDialogHeader><AlertDialogTitle>Apply to {original?.name}?</AlertDialogTitle>
        <AlertDialogDescription>{!preview ? 'Comparing the configurations…' : preview.changes.length
          ? `These sections of ${original?.name} are replaced by this clone’s: ${preview.changes.join(', ')}. Its name and description stay.`
          : 'The original already runs this configuration.'}</AlertDialogDescription></AlertDialogHeader>
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      <AlertDialogFooter><AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
        {compareFailed && <Button variant="outline" disabled={busy} onClick={compareAgain}>Compare again</Button>}
        <AlertDialogAction disabled={busy || !preview?.changes.length} onClick={event => { event.preventDefault(); void apply(); }}>Apply</AlertDialogAction></AlertDialogFooter>
    </AlertDialogContent></AlertDialog>
    <AlertDialog open={deleting} onOpenChange={open => { if (!open && !busy) { setDeleting(false); setError(''); } }}><AlertDialogContent>
      <AlertDialogHeader><AlertDialogTitle>Delete this clone?</AlertDialogTitle>
        <AlertDialogDescription>{profile.name} is deleted. {original?.name} keeps the applied configuration.</AlertDialogDescription></AlertDialogHeader>
      {history && <div className="flex flex-col gap-1 text-sm"><p>This clone has history that will be deleted with it:</p><p className="font-medium">{history}</p><p className="text-muted-foreground">Saved documents are kept.</p></div>}
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      <AlertDialogFooter><AlertDialogCancel disabled={busy}>Keep clone</AlertDialogCancel>
        <AlertDialogAction className={buttonVariants({ variant: 'destructive' })} disabled={busy || history === undefined} onClick={event => { event.preventDefault(); void removeClone(); }}>Delete clone</AlertDialogAction></AlertDialogFooter>
    </AlertDialogContent></AlertDialog>
  </div>;
}
