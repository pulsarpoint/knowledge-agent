import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { ApiError, getEnvironments, getServers, getServices } from '~/lib/api';
import { createSession } from '~/lib/claudeCode';
import { getCodeProjects } from '~/lib/codeProjects';
import { defaultFolder, documentSlug, folderError, getDocumentationFolders } from '~/lib/documents';
import { agentTypeLabel, getAgents, getRunResourceOptions, type AgentSummary, type RunResourceKind, type RunResourceOptions } from '~/lib/profiles';
import { getCollectionNames, type CollectionSummary } from '~/lib/rag';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Checkbox } from '~/components/ui/checkbox';
import { Field, FieldContent, FieldDescription, FieldLabel, FieldLegend, FieldSet } from '~/components/ui/field';
import { Input } from '~/components/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/select';
import { Skeleton } from '~/components/ui/skeleton';
import { Textarea } from '~/components/ui/textarea';

type Kind = 'code' | 'documents' | 'environments' | 'services' | 'servers';
type Item = { id: string; name: string };
type Data = { agents: AgentSummary[]; items: Record<Kind, Item[]>; folders: string[]; noModel: string };
const KINDS: { kind: Kind; label: string; legend: string }[] = [
  { kind: 'code', label: 'Code', legend: 'Code projects' }, { kind: 'documents', label: 'Documents', legend: 'Collections' },
  { kind: 'environments', label: 'Environment', legend: 'Environments' },
  { kind: 'services', label: 'Services', legend: 'Services' }, { kind: 'servers', label: 'Servers', legend: 'Servers' }];
const NOTHING: Record<Kind, string[]> = { code: [], documents: [], environments: [], services: [], servers: [] };
/** The run resource kind a choice passes to a per-run agent; an environment brings its services and servers. */
const RUN_KIND: Partial<Record<Kind, RunResourceKind>> = { code: 'code_projects', documents: 'collections', services: 'services', servers: 'servers' };
const NOTES = {
  fixed: 'This agent works with its own resources. What you choose is described in its first message.',
  perRun: 'This agent works only with what you choose here, from what it accepts. Environments and services also bring their code projects and servers.',
};
const sourcesNote = (agent?: AgentSummary) => !agent ? 'Choose an agent to see how it uses what you choose here.'
  : agent.resources === 'per_run' ? NOTES.perRun : NOTES.fixed;

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
  const [runOptions, setRunOptions] = useState<RunResourceOptions | null>(null);

  useEffect(() => {
    let cancelled = false;
    // The documentation folder list only feeds suggestions: a failing request must not block the form.
    // RAG being unavailable leaves Documents empty instead of blocking the form.
    Promise.all([getAgents(), getCodeProjects(), getCollectionNames().catch(() => ({ collections: [] as CollectionSummary[] })), getEnvironments(), getServices(), getServers(),
      getDocumentationFolders().catch(() => ({ folders: [] as { folder: string }[] }))])
      .then(([agents, projects, collections, environments, services, servers, docFolders]) => {
        if (cancelled) return;
        // Both agent types; only agents that can run: enabled, with a model, not the board's coordinator.
        const usable = agents.agents.filter(agent => agent.enabled && !agent.is_coordinator);
        const listed = usable.filter(agent => agent.model_id);
        const noModel = usable.find(agent => agent.id === initialAgentId && !agent.model_id)?.name ?? '';
        setData({ agents: listed, noModel, folders: docFolders.folders.map(item => item.folder), items: {
          code: projects.projects, documents: collections.collections.map(item => ({ id: item.id, name: item.name })), environments: environments.environments, services: services.services,
          servers: servers.servers.map(server => ({ id: server.id, name: `${server.name} (${server.hostname})` })) } });
        if (!listed.some(agent => agent.id === initialAgentId)) setAgentId('');
      }).catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load agents and what to document'); });
    return () => { cancelled = true; };
  }, [initialAgentId]);

  const agent = data?.agents.find(item => item.id === agentId);
  const perRun = agent?.resources === 'per_run';
  useEffect(() => {
    setRunOptions(null);
    if (!agent || agent.resources !== 'per_run') return;
    let live = true;
    getRunResourceOptions(agent.id).then(value => { if (live) setRunOptions(value); })
      .catch(e => { if (live) setError(e instanceof Error ? e.message : 'Could not load what this agent accepts'); });
    return () => { live = false; };
  }, [agent?.id, agent?.resources]);
  /** What a choice offers: every item for a fixed agent; for a per-run agent only what it accepts (null: a kind it
   * does not take). */
  function offered(kind: Kind): Item[] | null {
    const items = data?.items[kind] ?? [];
    const runKind = RUN_KIND[kind];
    if (!perRun || !runKind) return items;
    const allowed = runOptions?.[runKind];
    if (allowed === undefined) return [];  // still loading
    return allowed === null ? null : items.filter(item => allowed.some(option => option.id === item.id));
  }
  const picked = (kind: Kind) => {
    const items = offered(kind);
    return kinds.includes(kind) && items ? chosen[kind].filter(id => items.some(item => item.id === id)) : [];
  };
  const toggleKind = (kind: Kind, checked: boolean) => setKinds(current => checked ? [...current, kind] : current.filter(item => item !== kind));
  function toggle(kind: Kind, id: string, checked: boolean) {
    const next = checked ? [...chosen[kind], id] : chosen[kind].filter(item => item !== id);
    setChosen(current => ({ ...current, [kind]: next }));
    if (kind === 'code' && !folderTouched) {
      const first = data?.items.code.find(project => project.id === next[0]);
      setFolder(first ? defaultFolder(first.name) : 'docs');
    }
  }
  const target = folder.trim();
  const badFolder = target ? folderError(target) : '';
  const ready = Boolean(agent?.model_id && (!perRun || runOptions) && KINDS.some(({ kind }) => picked(kind).length > 0) && documentSlug(name) && target && !badFolder);

  async function submit() {
    if (!ready || pending) return;
    setPending(true); setError('');
    try {
      const session = await createSession({ agent_id: agentId, code_project_ids: picked('code'), collection_ids: picked('documents'), environment_ids: picked('environments'),
        service_ids: picked('services'), server_ids: picked('servers'), instructions, document_name: name.trim(), folder: target });
      onCreated(session.id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not start generating');
      setPending(false);
    }
  }
  function check(id: string, label: string, checked: boolean, change: (checked: boolean) => void, disabled = false) {
    return <Field key={id} orientation="horizontal"><Checkbox id={id} disabled={disabled} checked={checked} onCheckedChange={value => change(value === true)} />
      <FieldContent><FieldLabel htmlFor={id}>{label}</FieldLabel></FieldContent></Field>;
  }
  if (!data) return error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : <Skeleton className="h-48 w-full" />;
  return <form className="flex max-w-2xl flex-col gap-5" onSubmit={e => { e.preventDefault(); void submit(); }}>
    <fieldset disabled={pending} className="flex min-w-0 flex-col gap-5">
      <Field><FieldLabel htmlFor="generate-agent">Agent</FieldLabel>
        <Select value={agentId || 'none'} onValueChange={value => setAgentId(value === 'none' ? '' : value)}>
          <SelectTrigger id="generate-agent"><SelectValue /></SelectTrigger>
          <SelectContent><SelectGroup><SelectItem value="none">Select…</SelectItem>
            {data.agents.map(item => <SelectItem key={item.id} value={item.id}>{`${item.name} (${agentTypeLabel(item.runtime)})`}</SelectItem>)}</SelectGroup></SelectContent></Select>
        <FieldDescription className={!agent && data.noModel ? 'text-destructive' : undefined}>{data.agents.length === 0
          ? 'Create one in Agents → New agent and choose its model.'
          : !agent && data.noModel ? `${data.noModel} has no model. Choose one on its General tab.`
          : `The agent works with its own model, prompt, instructions, skills and tools${agent?.model_name ? ` (${agent.model_name})` : ''}.`}</FieldDescription></Field>
      <FieldSet><FieldLegend>What to document</FieldLegend>
        <FieldDescription>{sourcesNote(agent)}</FieldDescription>
        {KINDS.map(({ kind, label, legend }) => { const items = offered(kind); return <div key={kind} className="flex flex-col gap-2">
          {check(`what-${kind}`, label, kinds.includes(kind) && items !== null, value => toggleKind(kind, value), items === null)}
          {items === null && <FieldDescription className="ml-6">This agent does not take {legend.toLowerCase()}.</FieldDescription>}
          {kinds.includes(kind) && items !== null && <FieldSet className="ml-6"><FieldLegend variant="label">{legend}</FieldLegend>
            {items.length === 0 && <FieldDescription>Nothing to choose yet.</FieldDescription>}
            {items.map(item => check(`generate-${kind}-${item.id}`, item.name, chosen[kind].includes(item.id), value => toggle(kind, item.id, value)))}
          </FieldSet>}
        </div>; })}
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
