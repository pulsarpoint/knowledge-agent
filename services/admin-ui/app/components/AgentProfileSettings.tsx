import { Link } from 'react-router';
import { useEffect, useState, type FormEvent } from 'react';
import { RESOURCE_MODES, RESOURCES_HELP, getAgentOptions, saveAgent, type AgentProfile, type AgentOptions, type AgentResources } from '../lib/profiles';
import { EXPERT_HELP, SUBAGENTS_HELP } from '../lib/tools';
import { codeCompatible, getLlmSettings, type ModelProfile } from '../lib/llm';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/select';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '~/components/ui/card';
import { Checkbox } from '~/components/ui/checkbox';
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldSet, FieldLegend } from '~/components/ui/field';
import { Input } from '~/components/ui/input';
import { Textarea } from '~/components/ui/textarea';
import { Switch } from '~/components/ui/switch';
import { toast } from 'sonner';

const NO_MODEL = 'none';
/** A model this agent can run. */
const fits = (model: ModelProfile) => model.enabled && model.api_format !== 'embeddings' && codeCompatible(model);

function ResourceSelection({ label, options, selected, onChange, disabled }: { label: string; options: { id: string; name: string; description?: string; enabled?: boolean }[]; selected: string[]; onChange: (ids: string[]) => void; disabled: boolean }) {
  return <FieldSet disabled={disabled}><FieldLegend variant="label">{label}</FieldLegend><div className="max-h-60 overflow-auto rounded-md border p-3">
    {!options.length && <p className="text-sm text-muted-foreground">No {label.toLowerCase()} available.</p>}
    {options.map(option => <Field key={option.id} orientation="horizontal" className="items-start py-2"><Checkbox id={`${label}-${option.id}`} disabled={disabled || option.enabled === false && !selected.includes(option.id)} checked={selected.includes(option.id)} onCheckedChange={checked => onChange(checked ? [...selected, option.id] : selected.filter(id => id !== option.id))} /><div><FieldLabel htmlFor={`${label}-${option.id}`}>{option.name}{option.enabled === false && ' (disabled)'}</FieldLabel>{option.description && <FieldDescription className="mt-1">{option.description}</FieldDescription>}</div></Field>)}
  </div></FieldSet>;
}
export default function AgentProfileSettings({ profile, onSaved, onDirtyChange, configurationDirty = false, view = "general" }: { profile: AgentProfile; view?: string; configurationDirty?: boolean; onDirtyChange?: (dirty: boolean) => void; onSaved: (profile: AgentProfile) => void }) {
  const [draft, setDraft] = useState(profile);
  const [options, setOptions] = useState<AgentOptions>();
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [skillsDirty, setSkillsDirty] = useState(false);
  const [reload, setReload] = useState(0);
  const [models, setModels] = useState<ModelProfile[]>();
  const [modelsError, setModelsError] = useState('');
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => { let live = true; setModelsError(''); getLlmSettings().then(value => { if (live) setModels(value.models); }).catch(e => { if (live) setModelsError(e instanceof Error ? e.message : 'Could not load models.'); }); return () => { live = false; }; }, [reload]);
  useEffect(() => { let live = true; getAgentOptions(profile.id).then(value => { if (live) setOptions(value); }).catch(e => { if (live) setError(e.message); }); return () => { live = false; }; }, [profile.id, reload]);
  // Saving the prompt elsewhere on this tab must preserve edits in this form.
  useEffect(() => { setDraft(current => ({ ...current, revision: profile.revision })); }, [profile.revision]);
  function change(patch: Partial<AgentProfile>) { setDraft(current => ({ ...current, ...patch })); setDirty(true); setNotice(''); }
  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setNotice('');
    if (skillsDirty && configurationDirty) { setBusy(false); return; }
    try { const saved = skillsDirty ? await saveAgent(draft, draft.skill_ids) : await saveAgent(draft); setDraft(saved); setDirty(false); setSkillsDirty(false); onSaved(saved); toast.success('Agent configuration saved.', { id: 'agent-configuration-saved' }); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not save this agent.'); }
    finally { setBusy(false); }
  }
  const needsWhen = Boolean(draft.expert) && !draft.when_to_use.trim();
  const capability = ({ documents: ['documents_enabled','Document search'], git: ['repositories_enabled','Code project access'], ssh: ['ssh_enabled','SSH access'], skills: ['skills_enabled','Skills'], mcp: ['mcp_enabled','MCP tools'], services: ['services_enabled','Service context'] } as const)[view as 'documents' | 'git' | 'ssh' | 'skills' | 'mcp' | 'services'];
  return <Card><CardHeader><CardTitle>{view === 'general' ? 'Agent details' : capability?.[1]}</CardTitle><CardDescription>Save changes to apply them. Switching a capability off preserves its selections and removes that access from the agent's runs.</CardDescription></CardHeader>
    <form onSubmit={event => void save(event)}><CardContent className="pb-6"><FieldGroup>
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}{notice && <p role="status" className="text-sm">{notice}</p>}
      <div hidden={view !== 'general'}><FieldGroup>
      <Field><FieldLabel htmlFor="agent-name">Name</FieldLabel><Input id="agent-name" required maxLength={100} disabled={busy} value={draft.name} onChange={e => change({ name: e.target.value })} /></Field>
      <Field><FieldLabel htmlFor="agent-purpose">Purpose</FieldLabel><Textarea id="agent-purpose" maxLength={4000} disabled={busy} value={draft.description} onChange={e => change({ description: e.target.value })} /></Field>
      <Field><FieldLabel htmlFor="agent-when">When to use this agent</FieldLabel><Textarea id="agent-when" maxLength={4000} disabled={busy} value={draft.when_to_use} onChange={e => change({ when_to_use: e.target.value })} /><FieldDescription>Describe the tasks that belong to this agent. Agents are currently selected explicitly.</FieldDescription></Field>
      <Field><FieldLabel htmlFor="agent-model">Model</FieldLabel>
        <Select value={draft.model_id ?? NO_MODEL} disabled={busy || !models} onValueChange={value => change({ model_id: value === NO_MODEL ? null : value })}>
          <SelectTrigger id="agent-model"><SelectValue placeholder={modelsError ? 'Models unavailable' : 'Loading models…'} /></SelectTrigger>
          <SelectContent><SelectGroup><SelectItem value={NO_MODEL}>Not configured</SelectItem>
            {models?.filter(m => fits(m) || m.id === draft.model_id).map(m => <SelectItem key={m.id} value={m.id} disabled={!fits(m)}>{m.name}{!m.enabled && ' (disabled)'}</SelectItem>)}
          </SelectGroup></SelectContent></Select>
        <FieldDescription>{modelsError || <>An enabled Responses connection or a Claude model. An agent runs only after a model is selected. <Link to="/llm-settings" className="underline underline-offset-4">Manage model connections</Link></>}</FieldDescription></Field>
            <Field><FieldLabel htmlFor="agent-resources">Resources</FieldLabel>
        <Select value={draft.resources ?? 'fixed'} disabled={busy} onValueChange={value => change({ resources: value as AgentResources })}>
          <SelectTrigger id="agent-resources"><SelectValue /></SelectTrigger>
          <SelectContent><SelectGroup>{RESOURCE_MODES.map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectGroup></SelectContent></Select>
        <FieldDescription>{RESOURCES_HELP[draft.resources ?? 'fixed']}</FieldDescription></Field>
      <Field orientation="horizontal"><Switch id="agent-subagents" disabled={busy} checked={Boolean(draft.subagents)} onCheckedChange={subagents => change({ subagents })} /><div><FieldLabel htmlFor="agent-subagents">May start subagents</FieldLabel><FieldDescription>{SUBAGENTS_HELP}</FieldDescription></div></Field>
      <Field orientation="horizontal" data-invalid={needsWhen || undefined}><Switch id="agent-expert" disabled={busy} checked={Boolean(draft.expert)} onCheckedChange={expert => change({ expert })} /><div><FieldLabel htmlFor="agent-expert">Offer as an expert</FieldLabel><FieldDescription>{EXPERT_HELP}</FieldDescription>{needsWhen && <p role="alert" className="text-sm text-destructive">Experts need When to use.</p>}</div></Field>
      <Field orientation="horizontal"><Switch id="agent-available" disabled={busy} checked={draft.enabled} onCheckedChange={enabled => change({ enabled })} /><div><FieldLabel htmlFor="agent-available">Enable agent</FieldLabel><FieldDescription>Disabled agents retain their settings and cannot start new tasks.</FieldDescription></div></Field>
      </FieldGroup></div>
      {capability && <Field orientation="horizontal"><Switch id={`capability-${capability[0]}`} checked={draft[capability[0]]} disabled={busy} onCheckedChange={enabled => { change({ [capability[0]]: enabled }); if (view === 'skills') setSkillsDirty(true); }} /><div><FieldLabel htmlFor={`capability-${capability[0]}`}>Enable {capability[1].replace(/^[A-Z][a-z]+/, word => word.toLowerCase())}</FieldLabel><FieldDescription>{draft[capability[0]] ? 'Enabled for this agent after saving.' : 'Disabled. Its saved settings are retained.'}</FieldDescription></div></Field>}
      <div hidden={view !== 'skills'}><FieldGroup>
        {skillsDirty && configurationDirty && <Alert><AlertDescription>Save or discard changes in General, SSH, or MCP before saving skills.</AlertDescription></Alert>}
        {draft.skills_enabled && <ResourceSelection label="Predefined skills" options={options?.skills ?? []} selected={draft.skill_ids} onChange={skill_ids => { change({ skill_ids }); setSkillsDirty(true); }} disabled={busy || !options} />}
        <FieldDescription>{draft.skills_enabled ? 'Choose one or more skills for this agent. ' : 'Enable skills to choose which ones this agent can use. ' }Create and edit skills in the <Link to="/skills" className="underline underline-offset-4">Skills library</Link>.</FieldDescription>
      </FieldGroup></div>
      <div hidden={view !== 'documents'}><FieldGroup>
        <Field orientation="horizontal"><Switch id="all-collections" disabled={busy || !draft.documents_enabled} checked={draft.all_collections} onCheckedChange={all_collections => change({ all_collections, collection_ids: [] })} /><FieldLabel htmlFor="all-collections">All collections</FieldLabel></Field>
        {!draft.all_collections && <ResourceSelection label="Collections" options={options?.collections ?? []} selected={draft.collection_ids} onChange={collection_ids => change({ collection_ids })} disabled={busy || !options || !draft.documents_enabled} />}
        <Field orientation="horizontal"><Switch id="documents-grounded" disabled={busy || !draft.documents_enabled} checked={draft.documents_grounded} onCheckedChange={documents_grounded => change({ documents_grounded })} /><div><FieldLabel htmlFor="documents-grounded">Answer only from documents</FieldLabel><FieldDescription>Search the knowledge base, cite every claim, and report questions the documents do not cover.</FieldDescription></div></Field>
      </FieldGroup></div>
      <div hidden={view !== 'git'}><FieldGroup>
        <Field orientation="horizontal"><Switch id="all-repositories" disabled={busy || !draft.repositories_enabled} checked={draft.all_repositories} onCheckedChange={all_repositories => change({ all_repositories, repository_ids: [] })} /><FieldLabel htmlFor="all-repositories">All code projects</FieldLabel></Field>
        {!draft.all_repositories && <ResourceSelection label="Code projects" options={options?.repositories ?? []} selected={draft.repository_ids} onChange={repository_ids => change({ repository_ids })} disabled={busy || !options || !draft.repositories_enabled} />}
      </FieldGroup></div>
      <div hidden={view !== 'services'}><FieldGroup>
      <ResourceSelection label="Services" options={options?.services ?? []} selected={draft.service_ids} onChange={service_ids => change({ service_ids })} disabled={busy || !options || !draft.services_enabled} />
      <FieldDescription>Selected service descriptions are included as context. Server and repository access are configured separately.</FieldDescription>
      </FieldGroup></div>
    </FieldGroup></CardContent><CardFooter className="flex-wrap gap-2"><Button disabled={busy || !dirty || !draft.name.trim() || needsWhen || skillsDirty && configurationDirty}>Save {view === 'general' ? 'details' : capability?.[1].replace(/^[A-Z][a-z]+/, word => word.toLowerCase())}</Button><Button type="button" variant="outline" disabled={busy} onClick={() => { setDraft(profile); setDirty(false); setSkillsDirty(false); setError(''); setNotice(''); setReload(n => n + 1); }}>{dirty ? 'Discard changes' : 'Refresh resources'}</Button></CardFooter></form>
  </Card>;
}
