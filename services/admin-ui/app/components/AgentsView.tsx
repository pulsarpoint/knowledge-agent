import { useEffect, useState, type FormEvent } from 'react';
import { Link, Navigate, useNavigate, useSearchParams, useLocation } from 'react-router';
import { ArrowRight, Plus, Sparkles, Trash2 } from 'lucide-react';
import AgentWizard from './AgentWizard';
import { createAgent, deleteAgent, getAgents, type AgentSummary } from '../lib/profiles';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '~/components/ui/card';
import { Field, FieldGroup, FieldLabel, FieldDescription } from '~/components/ui/field';
import { Input } from '~/components/ui/input';
import { Textarea } from '~/components/ui/textarea';
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
  const [wizard, setWizard] = useState(false);
  const [removing, setRemoving] = useState<AgentSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [draft, setDraft] = useState({ name: '', description: '', when_to_use: '', system_prompt: '' });
  useEffect(() => { let live = true; getAgents().then(data => { if (live) { setAgents(data.agents); setError(''); } }).catch(e => { if (live) setError(e.message); }); return () => { live = false; }; }, [reload]);
  const legacy = params.get('agent');
  if (legacy) return <Navigate replace to={`/agents/${encodeURIComponent(legacy)}?view=${params.get('view') === 'test' ? 'test' : 'settings'}${hash}`} />;
  async function create(event: FormEvent) {
    event.preventDefault();
    if (!draft.name.trim() || !draft.system_prompt.trim()) { setError('Name and system prompt are required.'); return; }
    setBusy(true); setError('');
    try { const profile = await createAgent(draft); navigate(`/agents/${profile.id}`); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not create the agent.'); }
    finally { setBusy(false); }
  }
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
  const filtered = agents?.filter(a => `${a.name} ${a.description} ${a.is_coordinator ? 'coordinator' : ''}`.toLowerCase().includes(query.toLowerCase()));
  return <section className="flex flex-col gap-6" aria-label="Agent list">
    <div className="flex flex-wrap items-center justify-between gap-3"><Input aria-label="Find agents" placeholder="Find an agent…" className="max-w-sm" value={query} onChange={e => setQuery(e.target.value)} /><div className="flex flex-wrap gap-2"><Button variant="outline" onClick={() => setCreating(true)} disabled={creating || wizard}><Plus />Create agent</Button><Button onClick={() => setWizard(true)} disabled={creating || wizard}><Sparkles data-icon="inline-start" />New Agent Wizard</Button></div></div>
    {wizard && <AgentWizard onClose={() => setWizard(false)} onCreated={id => navigate(`/agents/${id}`)} />}
    {error && !removing && <Alert variant="destructive"><AlertDescription>{error}<Button variant="outline" size="sm" onClick={() => setReload(n => n + 1)}>Retry</Button></AlertDescription></Alert>}
    {creating && <Card><CardHeader><CardTitle>Create agent</CardTitle><CardDescription>Name and system prompt are required. Each agent owns its configuration and prompt versions.</CardDescription></CardHeader>
      <form onSubmit={event => void create(event)}><CardContent className="pb-6"><FieldGroup>
        <Field><FieldLabel htmlFor="new-agent-name">Name</FieldLabel><Input id="new-agent-name" autoFocus required maxLength={100} value={draft.name} disabled={busy} onChange={e => setDraft({ ...draft, name: e.target.value })} placeholder="Payments investigation" /></Field>

        <Field><FieldLabel htmlFor="new-agent-description">Purpose</FieldLabel><Textarea id="new-agent-description" maxLength={4000} value={draft.description} disabled={busy} onChange={e => setDraft({ ...draft, description: e.target.value })} /></Field>
        <Field><FieldLabel htmlFor="new-agent-when">When to use this agent</FieldLabel><Textarea id="new-agent-when" maxLength={4000} value={draft.when_to_use} disabled={busy} onChange={e => setDraft({ ...draft, when_to_use: e.target.value })} placeholder="Investigate failed payment retries and explain the supporting code." /><FieldDescription>Describe the tasks this agent is intended to handle.</FieldDescription></Field>
        <Field><FieldLabel htmlFor="new-agent-prompt">System prompt</FieldLabel><Textarea id="new-agent-prompt" required rows={8} maxLength={40000} value={draft.system_prompt} disabled={busy} onChange={e => setDraft({ ...draft, system_prompt: e.target.value })} placeholder="Define this agent’s purpose, behavior, and response requirements." /><FieldDescription>Required. Saved as this agent’s first active prompt. Later versions are managed in its Prompts tab.</FieldDescription></Field>
      </FieldGroup></CardContent><CardFooter className="gap-2"><Button disabled={busy || !draft.name.trim() || !draft.system_prompt.trim()}>{busy ? 'Creating…' : 'Create and configure'}</Button><Button type="button" variant="outline" disabled={busy} onClick={() => setCreating(false)}>Cancel</Button></CardFooter></form>
    </Card>}
    {!agents ? <Skeleton className="h-64 w-full" /> : <div className="overflow-hidden rounded-lg border"><Table><TableHeader><TableRow><TableHead>Agent</TableHead><TableHead>Capabilities</TableHead><TableHead>Model</TableHead><TableHead>System prompt</TableHead><TableHead>Status</TableHead><TableHead><span className="sr-only">Actions</span></TableHead></TableRow></TableHeader><TableBody>
      {filtered?.map(a => <TableRow key={a.id}><TableCell className="max-w-sm whitespace-normal"><Link className="font-medium underline-offset-4 hover:underline" to={`/agents/${a.id}`}>{a.name}</Link>{a.config_version && <Badge variant="outline" className="ml-2">Agent v{a.config_version}</Badge>}{a.is_coordinator && <Badge className="ml-2">Coordinator</Badge>}{a.description && <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{a.description}</p>}</TableCell><TableCell className="max-w-xs whitespace-normal"><div className="flex flex-wrap gap-1">{a.is_coordinator && <Badge variant="outline">Delegation</Badge>}{([['documents_enabled','Documents'],['repositories_enabled','Code'],['ssh_enabled','SSH'],['skills_enabled','Skills'],['mcp_enabled','MCP'],['services_enabled','Services']] as const).filter(([key]) => a[key]).map(([key,label]) => <Badge key={key} variant="outline">{label}</Badge>)}{!a.is_coordinator && ![a.documents_enabled,a.repositories_enabled,a.ssh_enabled,a.skills_enabled,a.mcp_enabled,a.services_enabled].some(Boolean) && <span className="text-muted-foreground">None enabled</span>}</div></TableCell><TableCell>{a.model_name ?? 'Not configured'}</TableCell><TableCell>{a.prompt_version ? `v${a.prompt_version} · ${a.prompt_title}` : 'Not configured'}</TableCell><TableCell><Badge variant={a.enabled ? 'secondary' : 'outline'}>{a.enabled ? 'Enabled' : 'Disabled'}</Badge></TableCell><TableCell><Button variant="ghost" size="icon" asChild><Link to={`/agents/${a.id}`} aria-label={`Open ${a.name}`}><ArrowRight /></Link></Button>{!a.is_coordinator && <Button variant="ghost" size="icon" disabled={busy} aria-label={`Remove ${a.name}`} onClick={() => { setError(''); setRemoving(a); }}><Trash2 data-icon="inline-start" /></Button>}</TableCell></TableRow>)}
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
