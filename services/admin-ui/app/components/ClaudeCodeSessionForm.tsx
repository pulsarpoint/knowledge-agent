import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { ApiError, getEnvironments, getServers, getServices } from '~/lib/api';
import { createSession } from '~/lib/claudeCode';
import { getCodeProjects } from '~/lib/codeProjects';
import { defaultFolder, documentSlug, folderError, getDocumentationFolders } from '~/lib/documents';
import { getAgents, type AgentSummary } from '~/lib/profiles';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Checkbox } from '~/components/ui/checkbox';
import { Field, FieldContent, FieldDescription, FieldLabel, FieldLegend, FieldSet } from '~/components/ui/field';
import { Input } from '~/components/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/select';
import { Skeleton } from '~/components/ui/skeleton';
import { Textarea } from '~/components/ui/textarea';

type Kind = 'code' | 'environments' | 'services' | 'servers';
type Item = { id: string; name: string };
type Data = { agents: AgentSummary[]; items: Record<Kind, Item[]>; folders: string[] };
const KINDS: { kind: Kind; label: string; legend: string }[] = [
  { kind: 'code', label: 'Code', legend: 'Code projects' }, { kind: 'environments', label: 'Environment', legend: 'Environments' },
  { kind: 'services', label: 'Services', legend: 'Services' }, { kind: 'servers', label: 'Servers', legend: 'Servers' }];
const NOTHING: Record<Kind, string[]> = { code: [], environments: [], services: [], servers: [] };

/** Docs → Generate documents → New: an agent, what to document, instructions, and the document's name and folder.
 * There is no model or prompt: the agent runs with its own. */
export default function ClaudeCodeSessionForm({ onCreated, initialAgentId = '' }: { onCreated: (id: string) => void; initialAgentId?: string }) {
  const [data, setData] = useState<Data | null>(null);
  const [agentId, setAgentId] = useState(initialAgentId);
  const [kinds, setKinds] = useState<Kind[]>([]);
  const [chosen, setChosen] = useState<Record<Kind, string[]>>(NOTHING);
  const [instructions, setInstructions] = useState('');
  const [name, setName] = useState('');
  const [folder, setFolder] = useState('docs');
  const [folderTouched, setFolderTouched] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    // The documentation folder list only feeds suggestions: a failing request must not block the form.
    Promise.all([getAgents(), getCodeProjects(), getEnvironments(), getServices(), getServers(),
      getDocumentationFolders().catch(() => ({ folders: [] as { folder: string }[] }))])
      .then(([agents, projects, environments, services, servers, docFolders]) => {
        if (cancelled) return;
        const claudeAgents = agents.agents.filter(agent => agent.runtime === 'claude_code' && agent.enabled);
        setData({ agents: claudeAgents, folders: docFolders.folders.map(item => item.folder), items: {
          code: projects.projects, environments: environments.environments, services: services.services,
          servers: servers.servers.map(server => ({ id: server.id, name: `${server.name} (${server.hostname})` })) } });
        if (!claudeAgents.some(agent => agent.id === initialAgentId)) setAgentId('');
      }).catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load agents and what to document'); });
    return () => { cancelled = true; };
  }, [initialAgentId]);

  const picked = (kind: Kind) => kinds.includes(kind) ? chosen[kind] : [];
  const toggleKind = (kind: Kind, checked: boolean) => setKinds(current => checked ? [...current, kind] : current.filter(item => item !== kind));
  function toggle(kind: Kind, id: string, checked: boolean) {
    const next = checked ? [...chosen[kind], id] : chosen[kind].filter(item => item !== id);
    setChosen(current => ({ ...current, [kind]: next }));
    if (kind === 'code' && !folderTouched) {
      const first = data?.items.code.find(project => project.id === next[0]);
      setFolder(first ? defaultFolder(first.name) : 'docs');
    }
  }
  const agent = data?.agents.find(item => item.id === agentId);
  const target = folder.trim();
  const badFolder = target ? folderError(target) : '';
  const ready = Boolean(agent?.model_id && KINDS.some(({ kind }) => picked(kind).length > 0) && documentSlug(name) && target && !badFolder);

  async function submit() {
    if (!ready || pending) return;
    setPending(true); setError('');
    try {
      const session = await createSession({ agent_id: agentId, code_project_ids: picked('code'), environment_ids: picked('environments'),
        service_ids: picked('services'), server_ids: picked('servers'), instructions, document_name: name.trim(), folder: target });
      onCreated(session.id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not start generating');
      setPending(false);
    }
  }
  function check(id: string, label: string, checked: boolean, change: (checked: boolean) => void) {
    return <Field key={id} orientation="horizontal"><Checkbox id={id} checked={checked} onCheckedChange={value => change(value === true)} />
      <FieldContent><FieldLabel htmlFor={id}>{label}</FieldLabel></FieldContent></Field>;
  }
  if (!data) return error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : <Skeleton className="h-48 w-full" />;
  return <form className="flex max-w-2xl flex-col gap-5" onSubmit={e => { e.preventDefault(); void submit(); }}>
    <fieldset disabled={pending} className="flex min-w-0 flex-col gap-5">
      <Field><FieldLabel htmlFor="generate-agent">Agent</FieldLabel>
        <Select value={agentId || 'none'} onValueChange={value => setAgentId(value === 'none' ? '' : value)}>
          <SelectTrigger id="generate-agent"><SelectValue /></SelectTrigger>
          <SelectContent><SelectGroup><SelectItem value="none">Select…</SelectItem>
            {data.agents.map(item => <SelectItem key={item.id} value={item.id}>{item.name}</SelectItem>)}</SelectGroup></SelectContent></Select>
        <FieldDescription className={agent && !agent.model_id ? 'text-destructive' : undefined}>{data.agents.length === 0
          ? 'Create one in Agents → New agent with Agent type Claude Code.'
          : agent && !agent.model_id ? 'This agent has no model. Choose a Claude model on its General tab.'
          : `The agent works with its own model, prompt, instructions, skills and tools${agent?.model_name ? ` (${agent.model_name})` : ''}.`}</FieldDescription></Field>
      <FieldSet><FieldLegend>What to document</FieldLegend>
        <FieldDescription>Code projects, and the code projects of the environments and services you choose, become the agent's read-only working folders. Environments, services and servers are described to the agent as text; this form gives no server access.</FieldDescription>
        {KINDS.map(({ kind, label, legend }) => <div key={kind} className="flex flex-col gap-2">
          {check(`what-${kind}`, label, kinds.includes(kind), value => toggleKind(kind, value))}
          {kinds.includes(kind) && <FieldSet className="ml-6"><FieldLegend variant="label">{legend}</FieldLegend>
            {data.items[kind].length === 0 && <FieldDescription>Nothing to choose yet.</FieldDescription>}
            {data.items[kind].map(item => check(`generate-${kind}-${item.id}`, item.name, chosen[kind].includes(item.id), value => toggle(kind, item.id, value)))}
          </FieldSet>}
        </div>)}
      </FieldSet>
      <Field><FieldLabel htmlFor="generate-instructions">Instructions</FieldLabel><Textarea id="generate-instructions" rows={3} maxLength={20000} value={instructions} onChange={e => setInstructions(e.target.value)} placeholder="Optional: extra instructions for this run" /></Field>
      <Field><FieldLabel htmlFor="generate-document-name">Document name</FieldLabel><Input id="generate-document-name" maxLength={240} value={name} onChange={e => setName(e.target.value)} placeholder="Payments – API reference" />
        {name && !documentSlug(name) && <FieldDescription className="text-destructive">Use letters or digits in the name.</FieldDescription>}</Field>
      <Field><FieldLabel htmlFor="generate-folder">Documentation folder</FieldLabel><Input id="generate-folder" list="generate-documentation-folders" maxLength={200} value={folder} onChange={e => { setFolderTouched(true); setFolder(e.target.value); }} />
        <datalist id="generate-documentation-folders">{data.folders.map(item => <option key={item} value={item} />)}</datalist>
        <FieldDescription className={badFolder ? 'text-destructive' : undefined}>{badFolder || `Save document stores it as ${target || '<folder>'}/${documentSlug(name) || '<document>'}/README.md in the generated documents folder.`}</FieldDescription></Field>
    </fieldset>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <Button type="submit" className="self-start" disabled={pending || !ready}>{pending ? 'Starting…' : 'Generate'}</Button>
  </form>;
}

/** Docs → Generate documents → New. ?agent=<id> (an agent's Test tab) preselects the agent; Generate opens the session. */
export function NewGeneration() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  return <ClaudeCodeSessionForm initialAgentId={params.get('agent') ?? ''} onCreated={id => navigate(`/documentation/generations/${id}`)} />;
}
