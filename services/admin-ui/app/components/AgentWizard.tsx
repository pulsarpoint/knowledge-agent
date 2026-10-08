import { useEffect, useId, useState } from 'react';
import { Link } from 'react-router';
import { Sparkles } from 'lucide-react';
import { createWizardAgent, generateAgent, getWizardOptions, type AgentProposal, type WizardOptions, type WizardResource, type WizardServer } from '../lib/agentWizard';
import { AGENT_TYPES, AGENT_TYPE_HELP, CLAUDE_CODE_TOOL_NAMES, RESOURCE_MODES, RESOURCES_HELP, agentTypeLabel, resourcesLabel, type AgentResources, type AgentRuntime } from '../lib/profiles';
import { NEED_REASONS, TOOL_GROUPS, impliedTools, toolNeedMet } from '../lib/tools';
import { claudeModel } from '~/lib/claudeCode';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '~/components/ui/card';
import { Checkbox } from '~/components/ui/checkbox';
import { Field, FieldGroup, FieldLabel, FieldDescription, FieldSet, FieldLegend, FieldContent } from '~/components/ui/field';
import { Input } from '~/components/ui/input';
import { Select, SelectTrigger, SelectValue, SelectContent, SelectGroup, SelectItem } from '~/components/ui/select';
import { Skeleton } from '~/components/ui/skeleton';
import { Spinner } from '~/components/ui/spinner';
import { Switch } from '~/components/ui/switch';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '~/components/ui/tabs';
import { Textarea } from '~/components/ui/textarea';
import { toast } from 'sonner';

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  const id = useId();
  return <Field orientation="horizontal"><Switch id={id} checked={checked} onCheckedChange={onChange} /><FieldLabel htmlFor={id}>{label}</FieldLabel></Field>;
}

function Selection({ label, options, selected, onChange }: { label: string; options: WizardResource[]; selected: string[]; onChange: (ids: string[]) => void }) {
  const id = useId();
  return <FieldSet><FieldLegend>{label}</FieldLegend><FieldGroup className="max-h-72 overflow-y-auto">
    {options.map(option => <Field key={option.id} orientation="horizontal">
      <Checkbox id={`${id}-${option.id}`} checked={selected.includes(option.id)} onCheckedChange={checked => onChange(checked ? [...selected, option.id] : selected.filter(item => item !== option.id))} />
      <FieldContent><FieldLabel htmlFor={`${id}-${option.id}`}>{option.name}</FieldLabel>{option.description && <FieldDescription>{option.description}</FieldDescription>}</FieldContent>
    </Field>)}
    {options.length === 0 && <FieldDescription>No available resources. Add them in administration first.</FieldDescription>}
  </FieldGroup></FieldSet>;
}

function blankProposal(model_id: string | null): AgentProposal {
  return { name: '', description: '', when_to_use: '', system_prompt: '', instructions: '', model_id, enabled: true,
    documents_enabled: false, repositories_enabled: false, ssh_enabled: false, skills_enabled: false, mcp_enabled: false, services_enabled: false,
    all_collections: false, all_repositories: false,
    collection_ids: [], repository_ids: [], skill_ids: [], mcp_server_ids: [], service_ids: [], servers: [] };
}

export default function AgentWizard({ onClose, onCreated }: { onClose: () => void; onCreated: (id: string) => void }) {
  const [options, setOptions] = useState<WizardOptions>();
  const [role, setRole] = useState('');
  const [model, setModel] = useState('');
  const [proposal, setProposal] = useState<AgentProposal>();
  const [toolChoice, setToolChoice] = useState<string[] | null>(null);
  const [step, setStep] = useState<'describe' | 'review'>('describe');
  const [tab, setTab] = useState('general');
  const [busy, setBusy] = useState<'generate' | 'create' | null>(null);
  const [error, setError] = useState('');
  const [showContext, setShowContext] = useState(false);
  const [reload, setReload] = useState(0);
  const [runtime, setRuntime] = useState<AgentRuntime>('openai');
  const [resources, setResources] = useState<AgentResources>('fixed');
  useEffect(() => {
    let live = true;
    getWizardOptions().then(data => {
      if (!live) return;
      setOptions(data); setError('');
      setModel(current => data.catalog.models.some(m => m.id === current) ? current : data.catalog.models[0]?.id ?? '');
    }).catch(reason => { if (live) setError(reason.message); });
    return () => { live = false; };
  }, [reload]);
  const catalog = options?.catalog;
  const catalogTools = options?.tools ?? [];
  const impliedNames = proposal ? impliedTools(catalogTools, proposal) : [];
  const chosenTools = toolChoice ?? impliedNames;
  const inCatalogOrder = (names: string[]) => catalogTools.map(tool => tool.name).filter(name => names.includes(name));
  const agentModels = catalog?.models.filter(m => runtime !== 'claude_code' || claudeModel(m)) ?? [];
  function chooseType(next: AgentRuntime) {
    setRuntime(next);
    if (next === 'claude_code' && proposal?.model_id && !catalog?.models.some(m => m.id === proposal.model_id && claudeModel(m))) edit({ model_id: null });
  }
  const agentType = <Field><FieldLabel htmlFor="wizard-agent-type">Agent type</FieldLabel><Select value={runtime} onValueChange={value => chooseType(value as AgentRuntime)}><SelectTrigger id="wizard-agent-type"><SelectValue /></SelectTrigger><SelectContent><SelectGroup>{AGENT_TYPES.map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectGroup></SelectContent></Select><FieldDescription>{AGENT_TYPE_HELP[runtime]} Every other setting is the same for both types.</FieldDescription></Field>;
  const resourcesField = <Field><FieldLabel htmlFor="wizard-resources">Resources</FieldLabel><Select value={resources} onValueChange={value => setResources(value as AgentResources)}><SelectTrigger id="wizard-resources"><SelectValue /></SelectTrigger><SelectContent><SelectGroup>{RESOURCE_MODES.map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectGroup></SelectContent></Select><FieldDescription>{RESOURCES_HELP[resources]}</FieldDescription></Field>;
  function edit(value: Partial<AgentProposal>) { setProposal(current => current ? { ...current, ...value } : current); }
  function editServer(id: string, value: Partial<WizardServer>) {
    if (proposal) edit({ servers: proposal.servers.map(server => server.server_id === id ? { ...server, ...value } : server) });
  }
  async function generate() {
    setBusy('generate'); setError('');
    try { const data = await generateAgent(role, model, runtime); setProposal(data.proposal); setToolChoice(null); setStep('review'); setTab('general'); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not generate an agent.'); }
    finally { setBusy(null); }
  }
  function startBlank() {
    setError(''); setToolChoice(null); setProposal(blankProposal(agentModels.some(m => m.id === model) ? model : null)); setStep('review'); setTab('general');
  }
  async function create() {
    if (!proposal) return;
    if (!proposal.name.trim() || !proposal.system_prompt.trim()) {
      setError('Name and system prompt are required.'); setTab(!proposal.system_prompt.trim() ? 'prompt' : 'general'); return;
    }
    setBusy('create'); setError('');
    try { const saved = await createWizardAgent({ ...proposal, runtime, resources, claude_code_tools: [...CLAUDE_CODE_TOOL_NAMES], ...(runtime === 'openai' && toolChoice ? { tools: inCatalogOrder(toolChoice) } : {}) }); toast.success(`Agent ${proposal.name.trim()} created`); onCreated(saved.id); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not create the agent.'); }
    finally { setBusy(null); }
  }
  return <Card aria-label="New Agent Wizard"><CardHeader>
    <CardTitle>New Agent Wizard</CardTitle><CardDescription>{step === 'describe'
      ? '1. Describe the role and let AI propose a configuration from your resources, or start blank and fill it in yourself.'
      : '2. Review and edit. Nothing is saved until you create the agent.'}</CardDescription>
  </CardHeader><CardContent className="flex flex-col gap-6 pb-6">
    {error && <Alert variant="destructive"><AlertDescription>{error}{!options && <Button variant="outline" size="sm" onClick={() => setReload(n => n + 1)}>Retry</Button>}</AlertDescription></Alert>}
    {!options || !catalog ? <Skeleton className="h-48 w-full" /> : <fieldset disabled={busy !== null} className="min-w-0">
      {step === 'describe' ? <FieldGroup>
        <div className="grid gap-4 sm:grid-cols-2">{agentType}{resourcesField}</div>
        <Field><FieldLabel htmlFor="wizard-role">What should this agent do?</FieldLabel>
          <Textarea id="wizard-role" autoFocus rows={5} maxLength={20000} value={role} onChange={e => setRole(e.target.value)} placeholder="Create a payments investigator that reads the payments code and runbooks, checks staging logs over SSH, and explains failures with evidence. Do not allow sudo." />
          <FieldDescription>Describe its role, expected output, resources, and any restrictions. Do not include passwords or private keys.</FieldDescription></Field>
        <Field><FieldLabel htmlFor="wizard-generator-model">Model for generating the configuration</FieldLabel>
          <Select value={model} onValueChange={setModel}><SelectTrigger id="wizard-generator-model"><SelectValue placeholder="Select a model" /></SelectTrigger>
            <SelectContent><SelectGroup>{catalog.models.map(m => <SelectItem key={m.id} value={m.id}>{m.name} · {m.model}</SelectItem>)}</SelectGroup></SelectContent></Select>
          {catalog.models.length === 0 && <FieldDescription><Link className="underline" to="/llm-settings">Add an enabled model in LLM Settings</Link> to generate a proposal.</FieldDescription>}
        </Field>
        <FieldSet><FieldLegend>Available context</FieldLegend><FieldDescription>The model receives resource names, descriptions, folder paths, and saved credential references. Private keys and tokens are excluded. It designs the agent without accessing these resources.</FieldDescription>
          <div className="flex flex-wrap gap-2">{([['Code projects', catalog.code_projects.length], ['Collections', catalog.collections.length], ['SSH servers', catalog.servers.length], ['Skills', catalog.skills.length], ['MCP servers', catalog.mcp_servers.length], ['Services', catalog.services.length]] as const).map(([label, count]) => <Badge key={label} variant="secondary">{count} {label}</Badge>)}</div>
          <Button type="button" variant="outline" className="w-fit" onClick={() => setShowContext(!showContext)}>{showContext ? 'Hide design context' : 'Show design context'}</Button>
          {showContext && <Field><FieldLabel htmlFor="wizard-context">Generation prompt, configuration schema, and resource catalog</FieldLabel><Textarea id="wizard-context" className="font-mono text-xs" readOnly rows={14} value={JSON.stringify({ instructions: options.prompt, output_schema: options.schema, task: { role, agent_type: runtime, generation_model_id: model, catalog: { ...catalog, models: agentModels }, output_schema: options.schema } }, null, 2)} /></Field>}
        </FieldSet>
      </FieldGroup> : proposal && <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="mb-4 h-auto flex-wrap justify-start">{[['general','General'],['prompt','Prompt'],['documents','Documents'],['code','Code projects'],['ssh','SSH'],['skills','Skills'],['mcp','MCP'],['services','Services'],...(runtime === 'openai' ? [['tools','Tools']] : [])].map(([value,label]) => <TabsTrigger key={value} value={value}>{label}</TabsTrigger>)}</TabsList>
        <TabsContent value="general"><FieldGroup>
          <p className="text-sm" aria-label="Agent type and resources"><span className="font-medium">{agentTypeLabel(runtime)} · {resourcesLabel(resources)}</span> <span className="text-muted-foreground">Change them on page 1 with Edit role.</span></p>
          <Field><FieldLabel htmlFor="wizard-name">Agent name</FieldLabel><Input id="wizard-name" maxLength={100} value={proposal.name} onChange={e => edit({ name: e.target.value })} /></Field>
          <Field><FieldLabel htmlFor="wizard-purpose">Purpose</FieldLabel><Textarea id="wizard-purpose" maxLength={4000} value={proposal.description} onChange={e => edit({ description: e.target.value })} /></Field>
          <Field><FieldLabel htmlFor="wizard-when">When to use this agent</FieldLabel><Textarea id="wizard-when" maxLength={4000} value={proposal.when_to_use} onChange={e => edit({ when_to_use: e.target.value })} /></Field>
          <Field><FieldLabel htmlFor="wizard-agent-model">Agent model</FieldLabel><Select value={proposal.model_id ?? 'none'} onValueChange={value => edit({ model_id: value === 'none' ? null : value })}><SelectTrigger id="wizard-agent-model"><SelectValue /></SelectTrigger><SelectContent><SelectGroup><SelectItem value="none">No model (choose later)</SelectItem>{agentModels.map(m => <SelectItem key={m.id} value={m.id}>{m.name} · {m.model}</SelectItem>)}</SelectGroup></SelectContent></Select><FieldDescription>An agent runs only after a model is selected.</FieldDescription></Field>
          <Toggle label="Enable agent" checked={proposal.enabled} onChange={enabled => edit({ enabled })} />
          <Field><FieldLabel htmlFor="wizard-instructions">Additional instructions</FieldLabel><Textarea id="wizard-instructions" rows={5} maxLength={40000} value={proposal.instructions} onChange={e => edit({ instructions: e.target.value })} /></Field>
          <FieldSet><FieldLegend>Proposed access</FieldLegend><div className="flex flex-wrap gap-2">{([['documents_enabled','Documents'],['repositories_enabled','Code projects'],['ssh_enabled','SSH'],['skills_enabled','Skills'],['mcp_enabled','MCP'],['services_enabled','Services']] as const).map(([key,label]) => <Badge key={key} variant={proposal[key] ? 'default' : 'outline'}>{label}: {proposal[key] ? 'on' : 'off'}</Badge>)}</div><FieldDescription>Review each tab to change access and resource selections.</FieldDescription></FieldSet>
        </FieldGroup></TabsContent>
        <TabsContent value="prompt"><FieldGroup><Field><FieldLabel htmlFor="wizard-prompt">System prompt</FieldLabel><Textarea id="wizard-prompt" rows={16} maxLength={40000} value={proposal.system_prompt} onChange={e => edit({ system_prompt: e.target.value })} /><FieldDescription>Required. Saved as this agent’s system prompt; edit it later on its General tab.</FieldDescription></Field></FieldGroup></TabsContent>
        <TabsContent value="documents"><FieldGroup>
          <Toggle label="Enable documents" checked={proposal.documents_enabled} onChange={documents_enabled => edit({ documents_enabled })} />
          {proposal.documents_enabled && <><Toggle label="All collections" checked={proposal.all_collections} onChange={all_collections => edit({ all_collections, collection_ids: [] })} />
            {proposal.all_collections ? <FieldDescription>Includes all current and future collections.</FieldDescription> : <Selection label="Collections" options={catalog.collections} selected={proposal.collection_ids} onChange={collection_ids => edit({ collection_ids })} />}</>}
        </FieldGroup></TabsContent>
        <TabsContent value="code"><FieldGroup>
          <Toggle label="Enable code projects" checked={proposal.repositories_enabled} onChange={repositories_enabled => edit({ repositories_enabled })} />
          {proposal.repositories_enabled && <><Toggle label="All code projects" checked={proposal.all_repositories} onChange={all_repositories => edit({ all_repositories, repository_ids: [] })} />
            {proposal.all_repositories ? <FieldDescription>Includes all current and future code projects.</FieldDescription> : <Selection label="Code projects" options={catalog.code_projects.map(p => ({ ...p, description: [p.description, p.folder_paths.join(', '), p.remote_url].filter(Boolean).join(' · ') }))} selected={proposal.repository_ids} onChange={repository_ids => edit({ repository_ids })} />}</>}
        </FieldGroup></TabsContent>
        <TabsContent value="skills"><FieldGroup><Toggle label="Enable skills" checked={proposal.skills_enabled} onChange={skills_enabled => edit({ skills_enabled })} />{proposal.skills_enabled && <Selection label="Skills" options={catalog.skills} selected={proposal.skill_ids} onChange={skill_ids => edit({ skill_ids })} />}</FieldGroup></TabsContent>
        <TabsContent value="mcp"><FieldGroup><Toggle label="Enable MCP" checked={proposal.mcp_enabled} onChange={mcp_enabled => edit({ mcp_enabled })} />{proposal.mcp_enabled && <Selection label="MCP servers" options={catalog.mcp_servers} selected={proposal.mcp_server_ids} onChange={mcp_server_ids => edit({ mcp_server_ids })} />}</FieldGroup></TabsContent>
        <TabsContent value="services"><FieldGroup><Toggle label="Enable services" checked={proposal.services_enabled} onChange={services_enabled => edit({ services_enabled })} />{proposal.services_enabled && <Selection label="Services" options={catalog.services} selected={proposal.service_ids} onChange={service_ids => edit({ service_ids })} />}</FieldGroup></TabsContent>
        {runtime === 'openai' && <TabsContent value="tools"><FieldGroup>
          <FieldDescription>{toolChoice ? 'Your choice of tools.' : 'Follows the capability switches until you change a tool.'} Usage instructions can be edited on the agent’s Tools tab after it is created.</FieldDescription>
          {TOOL_GROUPS.map(([group, label]) => {
            const items = catalogTools.filter(tool => tool.group === group);
            return items.length ? <FieldSet key={group}><FieldLegend>{label}</FieldLegend>{items.map(tool => {
              const met = toolNeedMet(tool.needs, proposal);
              return <Field key={tool.name} orientation="horizontal" className={met ? '' : 'opacity-60'}>
                <Switch id={`wizard-tool-${tool.name}`} checked={chosenTools.includes(tool.name)}
                  onCheckedChange={on => setToolChoice(inCatalogOrder([...chosenTools.filter(name => name !== tool.name), ...(on ? [tool.name] : [])]))} />
                <FieldContent><FieldLabel htmlFor={`wizard-tool-${tool.name}`}>{tool.label}</FieldLabel>
                  <FieldDescription>{tool.summary}{!met && tool.needs ? ` ${NEED_REASONS[tool.needs]}.` : ''}</FieldDescription></FieldContent>
              </Field>; })}</FieldSet> : null;
          })}
          {toolChoice && <Button type="button" variant="outline" className="w-fit" onClick={() => setToolChoice(null)}>Follow the capabilities</Button>}
        </FieldGroup></TabsContent>}
        <TabsContent value="ssh"><FieldGroup>
          <Toggle label="Enable SSH" checked={proposal.ssh_enabled} onChange={ssh_enabled => edit({ ssh_enabled })} />
          {proposal.ssh_enabled && <><Selection label="Allowed servers" options={catalog.servers.map(s => ({ ...s, description: `${s.hostname} · ${s.description ?? ''}` }))} selected={proposal.servers.map(s => s.server_id)} onChange={ids => edit({ servers: ids.map(server_id => proposal.servers.find(s => s.server_id === server_id) ?? { server_id, instructions: null, skill_ids: null, mcp_server_ids: null, connection: null }) })} />
            {proposal.servers.map(server => {
              const defaults = catalog.servers.find(s => s.id === server.server_id);
              const connection = server.connection;
              return <FieldSet key={server.server_id}><FieldLegend>{defaults?.name ?? 'Unavailable server'}</FieldLegend><FieldGroup>
                <Toggle label="Use agent instructions" checked={server.instructions === null} onChange={inherit => editServer(server.server_id, { instructions: inherit ? null : proposal.instructions })} />
                {server.instructions !== null && <Field><FieldLabel htmlFor={`server-notes-${server.server_id}`}>Instructions for {defaults?.name}</FieldLabel><Textarea id={`server-notes-${server.server_id}`} rows={4} maxLength={40000} value={server.instructions} onChange={e => editServer(server.server_id, { instructions: e.target.value })} /></Field>}
                {proposal.skills_enabled && <><Toggle label="Use agent skills" checked={server.skill_ids === null} onChange={inherit => editServer(server.server_id, { skill_ids: inherit ? null : [...proposal.skill_ids] })} />{server.skill_ids !== null && <Selection label="Server skills" options={catalog.skills} selected={server.skill_ids} onChange={skill_ids => editServer(server.server_id, { skill_ids })} />}</>}
                {proposal.mcp_enabled && <><Toggle label="Use agent MCP servers" checked={server.mcp_server_ids === null} onChange={inherit => editServer(server.server_id, { mcp_server_ids: inherit ? null : [...proposal.mcp_server_ids] })} />{server.mcp_server_ids !== null && <Selection label="Server MCP servers" options={catalog.mcp_servers} selected={server.mcp_server_ids} onChange={mcp_server_ids => editServer(server.server_id, { mcp_server_ids })} />}</>}
                <Toggle label="Override server connection" checked={connection !== null} onChange={override => editServer(server.server_id, { connection: override && defaults ? { username: defaults.username, port: defaults.port, allow_sudo: defaults.allow_sudo, known_hosts: defaults.known_hosts, credential_id: defaults.credential_id } : null })} />
                {connection === null ? <FieldDescription>Uses {defaults?.username ?? 'the configured user'} at {defaults?.hostname}:{defaults?.port}. Sudo {defaults?.allow_sudo ? 'allowed' : 'not allowed'}. {defaults?.credential_id ? 'Uses the server’s saved private key.' : 'No saved private key; configure SSH before running.'}</FieldDescription> : <>
                  <Field><FieldLabel htmlFor={`server-user-${server.server_id}`}>SSH login user</FieldLabel><Input id={`server-user-${server.server_id}`} maxLength={65} value={connection.username ?? ''} onChange={e => editServer(server.server_id, { connection: { ...connection, username: e.target.value || null } })} /></Field>
                  <Field><FieldLabel htmlFor={`server-port-${server.server_id}`}>SSH port</FieldLabel><Input id={`server-port-${server.server_id}`} type="number" min={1} max={65535} value={connection.port} onChange={e => editServer(server.server_id, { connection: { ...connection, port: Number(e.target.value) } })} /></Field>
                  <Toggle label="Allow sudo to root" checked={connection.allow_sudo} onChange={allow_sudo => editServer(server.server_id, { connection: { ...connection, allow_sudo } })} />
                  <Field><FieldLabel htmlFor={`server-key-${server.server_id}`}>Saved SSH private key</FieldLabel><Select value={connection.credential_id ?? 'none'} onValueChange={id => editServer(server.server_id, { connection: { ...connection, credential_id: id === 'none' ? null : id } })}><SelectTrigger id={`server-key-${server.server_id}`}><SelectValue /></SelectTrigger><SelectContent><SelectGroup><SelectItem value="none">No key — configure later</SelectItem>{catalog.credentials.map(c => <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>)}</SelectGroup></SelectContent></Select><FieldDescription>References an encrypted saved key. Add or replace a private key in this agent’s SSH tab after creation.</FieldDescription></Field>
                  <Field><FieldLabel htmlFor={`server-trust-${server.server_id}`}>Trusted host keys (optional)</FieldLabel><Textarea id={`server-trust-${server.server_id}`} maxLength={40000} value={connection.known_hosts} onChange={e => editServer(server.server_id, { connection: { ...connection, known_hosts: e.target.value } })} /></Field>
                </>}
              </FieldGroup></FieldSet>;
            })}</>}
        </FieldGroup></TabsContent>
      </Tabs>}
    </fieldset>}
  </CardContent><CardFooter className="flex-wrap gap-2">
    {step === 'describe' ? <>
      <Button disabled={busy !== null || !role.trim() || !model || !options} onClick={() => void generate()}>{busy === 'generate' ? <Spinner data-icon="inline-start" /> : <Sparkles data-icon="inline-start" />}{busy === 'generate' ? 'Generating proposal…' : proposal ? 'Generate new proposal' : 'Generate proposal'}</Button>
      <Button variant="outline" disabled={busy !== null || !options} onClick={startBlank}>Start blank</Button>
      {proposal && <Button variant="outline" disabled={busy !== null} onClick={() => setStep('review')}>Back to proposal</Button>}
    </> : <><Button disabled={busy !== null || !proposal?.name.trim() || !proposal?.system_prompt.trim()} onClick={() => void create()}>{busy === 'create' && <Spinner data-icon="inline-start" />}{busy === 'create' ? 'Creating…' : 'Create agent'}</Button><Button variant="outline" disabled={busy !== null} onClick={() => { setError(''); setStep('describe'); }}>Edit role</Button></>}
    <Button variant="ghost" disabled={busy !== null} onClick={onClose}>Cancel</Button>
  </CardFooter></Card>;
}
