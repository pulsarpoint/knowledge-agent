import { useEffect, useState } from 'react';
import { Link, Navigate, useNavigate, useSearchParams, useLocation } from 'react-router';
import { ArrowRight, Sparkles, Trash2 } from 'lucide-react';
import AgentWizard from './AgentWizard';
import ClaudeCodeAgentForm from './ClaudeCodeAgentForm';
import ReaderAssistantCard from './ReaderAssistantCard';
import { deleteAgent, getAgents, type AgentSummary } from '../lib/profiles';
import { CLAUDE_CODE_SDK, CLAUDE_CODE_TOOLS } from '~/lib/claudeCode';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
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
  const [wizard, setWizard] = useState(false);
  const [creating, setCreating] = useState(false);
  const [removing, setRemoving] = useState<AgentSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  useEffect(() => { let live = true; getAgents().then(data => { if (live) { setAgents(data.agents); setError(''); } }).catch(e => { if (live) setError(e.message); }); return () => { live = false; }; }, [reload]);
  const legacy = params.get('agent');
  if (legacy) return <Navigate replace to={`/agents/${encodeURIComponent(legacy)}?view=${params.get('view') === 'test' ? 'test' : 'settings'}${hash}`} />;
  async function remove() {
    if (!removing || removing.is_coordinator) return;
    setBusy(true); setError('');
    try {
      await deleteAgent(removing.id, removing.revision);
      setAgents(current => current?.filter(agent => agent.id !== removing.id));
      setRemoving(null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not remove the agent.'); }
    finally { setBusy(false); }
  }
  const filtered = agents?.filter(a => `${a.name} ${a.description} ${a.is_coordinator ? 'coordinator' : ''} ${a.runtime === 'claude_code' ? 'claude code' : ''}`.toLowerCase().includes(query.toLowerCase()));
  return <section className="flex flex-col gap-6" aria-label="Agent list">
    <ReaderAssistantCard />
    <div className="flex flex-wrap items-center justify-between gap-3"><Input aria-label="Find agents" placeholder="Find an agent…" className="max-w-sm" value={query} onChange={e => setQuery(e.target.value)} /><Button onClick={() => setWizard(true)} disabled={wizard}><Sparkles data-icon="inline-start" />New Agent Wizard</Button><Button variant="outline" onClick={() => setCreating(true)} disabled={creating}>New Claude Code agent</Button></div>
    {wizard && <AgentWizard onClose={() => setWizard(false)} onCreated={id => navigate(`/agents/${id}`)} />}
    {creating && <ClaudeCodeAgentForm onCancel={() => setCreating(false)} onSaved={saved => navigate(`/agents/${saved.id}`)} />}
    {error && !removing && <Alert variant="destructive"><AlertDescription>{error}<Button variant="outline" size="sm" onClick={() => setReload(n => n + 1)}>Retry</Button></AlertDescription></Alert>}
    {!agents ? <Skeleton className="h-64 w-full" /> : <div className="overflow-hidden rounded-lg border"><Table><TableHeader><TableRow><TableHead>Agent</TableHead><TableHead>Capabilities</TableHead><TableHead>Model</TableHead><TableHead>System prompt</TableHead><TableHead>Status</TableHead><TableHead><span className="sr-only">Actions</span></TableHead></TableRow></TableHeader><TableBody>
      {filtered?.map(a => { const claudeCode = a.runtime === 'claude_code'; return <TableRow key={a.id}><TableCell className="max-w-sm whitespace-normal"><Link className="font-medium underline-offset-4 hover:underline" to={`/agents/${a.id}`}>{a.name}</Link>{claudeCode && <Badge variant="secondary" className="ml-2">{CLAUDE_CODE_SDK}</Badge>}{a.config_version && <Badge variant="outline" className="ml-2">Agent v{a.config_version}</Badge>}{a.is_coordinator && <Badge className="ml-2">Coordinator</Badge>}{a.description && <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{a.description}</p>}</TableCell><TableCell className="max-w-xs whitespace-normal">{claudeCode ? <span>{CLAUDE_CODE_TOOLS}</span> : <div className="flex flex-wrap gap-1">{a.is_coordinator && <Badge variant="outline">Delegation</Badge>}{([['documents_enabled','Documents'],['repositories_enabled','Code'],['ssh_enabled','SSH'],['skills_enabled','Skills'],['mcp_enabled','MCP'],['services_enabled','Services']] as const).filter(([key]) => a[key]).map(([key,label]) => <Badge key={key} variant="outline">{label}</Badge>)}{!a.is_coordinator && ![a.documents_enabled,a.repositories_enabled,a.ssh_enabled,a.skills_enabled,a.mcp_enabled,a.services_enabled].some(Boolean) && <span className="text-muted-foreground">None enabled</span>}</div>}</TableCell><TableCell>{claudeCode ? 'Chosen per session' : a.model_name ?? 'Not configured'}</TableCell><TableCell>{claudeCode ? 'Built in' : a.prompt_version ? `v${a.prompt_version} · ${a.prompt_title}` : 'Not configured'}</TableCell><TableCell><Badge variant={a.enabled ? 'secondary' : 'outline'}>{a.enabled ? 'Enabled' : 'Disabled'}</Badge></TableCell><TableCell><Button variant="ghost" size="icon" asChild><Link to={`/agents/${a.id}`} aria-label={`Open ${a.name}`}><ArrowRight /></Link></Button>{!a.is_coordinator && <Button variant="ghost" size="icon" disabled={busy} aria-label={`Remove ${a.name}`} onClick={() => { setError(''); setRemoving(a); }}><Trash2 data-icon="inline-start" /></Button>}</TableCell></TableRow>; })}
      {filtered?.length === 0 && <TableRow><TableCell colSpan={6} className="h-24 text-center text-muted-foreground">No agents match your search.</TableCell></TableRow>}
    </TableBody></Table></div>}
    <AlertDialog open={removing !== null} onOpenChange={open => { if (!busy && !open) setRemoving(null); }}>
      <AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Remove agent?</AlertDialogTitle><AlertDialogDescription>
        Remove {removing?.name} and its saved configuration and prompt versions? Shared servers, code projects, skills, models, and documents will be kept. Agents with retained run history cannot be removed.
      </AlertDialogDescription></AlertDialogHeader>
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      <AlertDialogFooter><AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel><AlertDialogAction disabled={busy} onClick={event => { event.preventDefault(); void remove(); }}>{busy ? 'Removing…' : 'Remove agent'}</AlertDialogAction></AlertDialogFooter></AlertDialogContent>
    </AlertDialog>
  </section>;
}
