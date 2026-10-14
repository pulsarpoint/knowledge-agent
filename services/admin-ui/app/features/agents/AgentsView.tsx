import { useEffect, useState } from 'react';
import { Link, Navigate, useNavigate, useSearchParams, useLocation } from 'react-router';
import { ArrowRight, Copy, Sparkles, Trash2 } from 'lucide-react';
import AgentWizard from './AgentWizard';
import { toast } from 'sonner';
import { toolsSummaryLabel } from './tools';
import { cloneAgent } from './clones';
import { deleteAgent, resourcesSummary, describeHistory, getAgentHistory, getAgents, type AgentSummary } from './api';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Button, buttonVariants } from '~/components/ui/button';
import { Input } from '~/components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '~/components/ui/table';
import { Skeleton } from '~/components/ui/skeleton';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '~/components/ui/alert-dialog';

export default function AgentsView() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { hash } = useLocation();
  const [agents, setAgents] = useState<AgentSummary[]>();
  const [query, setQuery] = useState('');
  const [creating, setCreating] = useState(false);
  const [removing, setRemoving] = useState<AgentSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  // What would go with the agent: undefined while checking, '' without history (or when it could not be checked).
  const [history, setHistory] = useState<string>();
  useEffect(() => { let live = true; getAgents().then(data => { if (live) { setAgents(data.agents); setError(''); } }).catch(e => { if (live) setError(e.message); }); return () => { live = false; }; }, [reload]);
  useEffect(() => {
    if (!removing) return;
    let live = true; setHistory(undefined);
    getAgentHistory(removing.id).then(value => { if (live) setHistory(describeHistory(value)); }).catch(() => { if (live) setHistory(''); });
    return () => { live = false; };
  }, [removing]);
  const legacy = params.get('agent');
  if (legacy) return <Navigate replace to={`/agents/${encodeURIComponent(legacy)}?view=${params.get('view') === 'test' ? 'test' : 'settings'}${hash}`} />;
  async function remove() {
    if (!removing) return;
    setBusy(true); setError('');
    const withHistory = Boolean(history);
    try {
      await deleteAgent(removing.id, removing.revision, withHistory);
      setAgents(current => current?.filter(agent => agent.id !== removing.id));
      setRemoving(null);
      toast.success(withHistory ? `Agent ${removing.name} and its history deleted` : `Agent ${removing.name} removed`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not remove the agent.');
      // History may have appeared since the dialog opened; show it (next to the reason) so it can be deleted too.
      if (!withHistory) await getAgentHistory(removing.id).then(value => { const found = describeHistory(value); if (found) setHistory(found); }).catch(() => undefined);
    }
    finally { setBusy(false); }
  }
  async function cloneRow(agent: AgentSummary) {
    setBusy(true); setError('');
    try { const created = await cloneAgent(agent.id); toast.success(`Clone created: ${created.name}`); navigate(`/agents/${encodeURIComponent(created.id)}`); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not clone the agent.'); }
    finally { setBusy(false); }
  }
  const filtered = agents?.filter(a => `${a.name} ${a.description} ${resourcesSummary(a)} ${toolsSummaryLabel(a.tools_summary)}`.toLowerCase().includes(query.toLowerCase()));
  return <section className="flex flex-col gap-6" aria-label="Agent list">
    <div className="flex flex-wrap items-center justify-between gap-3"><Input aria-label="Find agents" placeholder="Find an agent…" className="max-w-sm" value={query} onChange={e => setQuery(e.target.value)} /><Button onClick={() => setCreating(true)} disabled={creating}><Sparkles data-icon="inline-start" />New agent</Button></div>
    {creating && <section aria-label="New agent" className="flex flex-col gap-3"><AgentWizard onClose={() => setCreating(false)} onCreated={id => navigate(`/agents/${id}`)} /></section>}
    {error && !removing && <Alert variant="destructive"><AlertDescription>{error}<Button variant="outline" size="sm" onClick={() => setReload(n => n + 1)}>Retry</Button></AlertDescription></Alert>}
    {!agents ? <Skeleton className="h-64 w-full" /> : <div className="overflow-hidden rounded-lg border"><Table><TableHeader><TableRow><TableHead>Agent</TableHead><TableHead>Resources</TableHead><TableHead>Tools</TableHead><TableHead>Capabilities</TableHead><TableHead>Model</TableHead><TableHead>Status</TableHead><TableHead><span className="sr-only">Actions</span></TableHead></TableRow></TableHeader><TableBody>
      {filtered?.map(a => <TableRow key={a.id}><TableCell className="max-w-sm whitespace-normal"><Link className="font-medium underline-offset-4 hover:underline" to={`/agents/${a.id}`}>{a.name}</Link>{a.expert && <> <Badge variant="secondary">Expert</Badge></>}{a.original_name && <p className="mt-1 text-xs text-muted-foreground">Clone of {a.original_name}</p>}{a.description && <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{a.description}</p>}</TableCell><TableCell>{resourcesSummary(a)}</TableCell><TableCell className="max-w-xs whitespace-normal">{toolsSummaryLabel(a.tools_summary)}</TableCell><TableCell className="max-w-xs whitespace-normal"><div className="flex flex-wrap gap-1">{([['documents_enabled','Documents'],['repositories_enabled','Code'],['ssh_enabled','SSH'],['skills_enabled','Skills'],['mcp_enabled','MCP'],['services_enabled','Services']] as const).filter(([key]) => a[key]).map(([key,label]) => <Badge key={key} variant="outline">{label}</Badge>)}{![a.documents_enabled,a.repositories_enabled,a.ssh_enabled,a.skills_enabled,a.mcp_enabled,a.services_enabled].some(Boolean) && <span className="text-muted-foreground">None enabled</span>}</div></TableCell><TableCell>{a.model_name ?? 'Not configured'}</TableCell><TableCell><Badge variant={a.enabled ? 'secondary' : 'outline'}>{a.enabled ? 'Enabled' : 'Disabled'}</Badge></TableCell><TableCell><Button variant="ghost" size="icon" asChild><Link to={`/agents/${a.id}`} aria-label={`Open ${a.name}`}><ArrowRight /></Link></Button><Button variant="ghost" size="icon" disabled={busy} aria-label={`Clone ${a.name}`} onClick={() => void cloneRow(a)}><Copy /></Button><Button variant="ghost" size="icon" disabled={busy} aria-label={`Remove ${a.name}`} onClick={() => { setError(''); setRemoving(a); }}><Trash2 data-icon="inline-start" /></Button></TableCell></TableRow>)}
      {filtered?.length === 0 && <TableRow><TableCell colSpan={7} className="h-24 text-center text-muted-foreground">No agents match your search.</TableCell></TableRow>}
    </TableBody></Table></div>}
    <AlertDialog open={removing !== null} onOpenChange={open => { if (!busy && !open) setRemoving(null); }}>
      <AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Remove agent?</AlertDialogTitle><AlertDialogDescription>
        Remove {removing?.name} and its saved configuration? Shared servers, code projects, skills, models, and documents will be kept.
      </AlertDialogDescription></AlertDialogHeader>
      {history && <div className="flex flex-col gap-1 text-sm"><p>This agent has history that will be deleted with it:</p><p className="font-medium">{history}</p><p className="text-muted-foreground">Saved documents are kept. To keep the history instead, disable the agent.</p></div>}
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      <AlertDialogFooter><AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>{history
        ? <AlertDialogAction className={buttonVariants({ variant: "destructive" })} disabled={busy} onClick={event => { event.preventDefault(); void remove(); }}>{busy ? 'Deleting…' : 'Delete agent and history'}</AlertDialogAction>
        : <AlertDialogAction disabled={busy || history === undefined} onClick={event => { event.preventDefault(); void remove(); }}>{busy ? 'Removing…' : history === undefined ? 'Checking history…' : 'Remove agent'}</AlertDialogAction>}</AlertDialogFooter></AlertDialogContent>
    </AlertDialog>
  </section>;
}
