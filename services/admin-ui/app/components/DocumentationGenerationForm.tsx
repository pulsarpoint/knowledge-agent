import { useDocumentationDraft } from '~/hooks/useDocumentationDraft';
import CodeGraphPhase from './CodeGraphPhase';
import CodeDocumentationPhase from './CodeDocumentationPhase';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { getServices, getEnvironments, getServers, type ServiceAsset, type EnvironmentAsset, type ServerAsset } from '~/lib/api';
import { codeCompatible, getLlmSettings, type ModelProfile } from '~/lib/llm';
import { generateDocumentation, getDocumentationPrompt, getDocumentationPrompts, type DocumentationPrompt, type GenerationInput, type DocumentationWorkflow, type DocumentationPromptSelection } from '~/lib/documents';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Checkbox } from '~/components/ui/checkbox';
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldSet, FieldLegend } from '~/components/ui/field';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/select';
import { Skeleton } from '~/components/ui/skeleton';
import { Textarea } from '~/components/ui/textarea';

type Kind = Exclude<GenerationInput['units'][number]['kind'], 'overview' | 'api' | 'messaging'>;
type PromptSelection = DocumentationPromptSelection;
const titles = { code: 'Code analysis', diagram: 'Code diagram (D2 / TALA)', deployment_diagram: 'Deployment diagram (D2 / TALA)', deployment: 'Deployment on an environment', architecture: 'Full environment architecture', infrastructure: 'Server infrastructure architecture' };
const promptNames = { code: 'Code analysis and architecture', diagram: 'Code component diagram (D2 / TALA)', deployment_diagram: 'Deployment diagram (D2 / TALA)', deployment: 'Deployment and service operations', architecture: 'Environment architecture', infrastructure: 'Server infrastructure architecture' };
const serviceKinds: Kind[] = ['code', 'diagram', 'deployment', 'deployment_diagram'];
const environmentKinds: Kind[] = ['architecture', 'infrastructure'];

export default function DocumentationGenerationForm({ onQueued, workflow }: { onQueued: () => void; workflow?: DocumentationWorkflow }) {
  const [data, setData] = useState<{ services: ServiceAsset[]; environments: EnvironmentAsset[]; servers: ServerAsset[]; models: ModelProfile[]; prompts: DocumentationPrompt[] } | null>(null);
  const [value, setValue] = useState<GenerationInput>(workflow?.drafts.form?.value ?? { scope: 'service', model_id: '', service_id: null, environment_id: null, code_project_ids: [], server_ids: [], units: [], instructions: '', update_existing: false });
  const [graphHashes, setGraphHashes] = useState<Record<string, string>>({});
  const [kinds, setKinds] = useState<Kind[]>((workflow?.drafts.form?.kinds ?? []) as Kind[]);
  const [environmentSelection, setEnvironmentSelection] = useState<Kind[]>((workflow?.drafts.form?.environmentSelection ?? ['architecture']) as Kind[]);
  const [selection, setSelection] = useState<Partial<Record<Kind, PromptSelection>>>(workflow?.drafts.form?.selection ?? {});
  const promptRequests = useRef<Partial<Record<Kind, number>>>({});
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState('');

  const draft = useDocumentationDraft(workflow?.id, 'form', workflow?.drafts.form, {value, kinds, environmentSelection, selection}, data !== null);

  async function selectPrompt(kind: Kind, id: string) {
    const request = (promptRequests.current[kind] ?? 0) + 1;
    promptRequests.current[kind] = request;
    setSelection(current => ({ ...current, [kind]: { id, versionId: '', version: 0, activeContent: '', content: '' } }));
    if (!id) return;
    try {
      const detail = await getDocumentationPrompt(id);
      const active = detail.versions.find(v => v.id === detail.active_version_id);
      if (!detail.enabled || !active) throw new Error('This prompt has no active version. Select another prompt.');
      if (promptRequests.current[kind] === request) setSelection(current => ({ ...current, [kind]: { id, versionId: active.id, version: active.version, activeContent: active.content, content: active.content } }));
    } catch (e) {
      if (promptRequests.current[kind] === request) setSelection(current => ({ ...current, [kind]: { id, versionId: '', version: 0, activeContent: '', content: '', error: e instanceof Error ? e.message : 'Could not load the active prompt' } }));
    }
  }
  useEffect(() => {
    let cancelled = false;
    Promise.all([getServices(), getEnvironments(), getServers(), getLlmSettings(), getDocumentationPrompts()]).then(([services, environments, servers, llm, prompts]) => {
      if (cancelled) return;
      const active = prompts.prompts.filter(p => p.enabled && p.active_version_id);
      const models = llm.models.filter(m => m.enabled && m.api_format !== 'embeddings' && codeCompatible(m));
      setData({ services: services.services, environments: environments.environments, servers: servers.servers, models, prompts: active });
      if (models.length === 1) setValue(current => ({ ...current, model_id: current.model_id || models[0].id }));
      for (const kind of [...serviceKinds, ...environmentKinds]) {
        const prompt = active.find(p => p.name === promptNames[kind]);
        if (prompt && !workflow?.drafts.form?.selection?.[kind]) void selectPrompt(kind, prompt.id);
      }
    }).catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load documentation resources'); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (data && workflow && window.location.hash) document.getElementById(window.location.hash.slice(1))?.scrollIntoView();
  }, [data, workflow?.id]);

  const service = data?.services.find(s => s.id === value.service_id);
  const environment = data?.environments.find(e => e.id === value.environment_id);
  const selectedKinds: Kind[] = value.scope === 'environment' ? environmentSelection : kinds;
  const deployment = selectedKinds.includes('deployment') || selectedKinds.includes('deployment_diagram');
  const infrastructure = selectedKinds.includes('infrastructure');
  const needsCode = value.scope === 'service' && (kinds.includes('code') || kinds.includes('diagram'));
  const selectedProjectIds = value.code_project_ids.length ? value.code_project_ids : (service?.repositories ?? []).filter(p => !p.archived).map(p => p.id);
  const graphsReady = !needsCode || selectedProjectIds.every(id => graphHashes[id]);
  const ready = Boolean(graphsReady && value.model_id && selectedKinds.length && selectedKinds.every(kind => selection[kind]?.versionId && selection[kind]?.content.trim())
    && (value.scope === 'environment' ? value.environment_id : value.service_id)
    && (!(deployment || infrastructure) || (value.environment_id && value.server_ids.length)) );

  function choose(label: string, id: string, current: string, items: { id: string; name: string }[], change: (id: string) => void) {
    return <Field><FieldLabel htmlFor={id}>{label}</FieldLabel><Select value={current || 'none'} onValueChange={v => change(v === 'none' ? '' : v)}><SelectTrigger id={id}><SelectValue /></SelectTrigger><SelectContent><SelectGroup><SelectItem value="none">Select…</SelectItem>{items.map(item => <SelectItem key={item.id} value={item.id}>{item.name}</SelectItem>)}</SelectGroup></SelectContent></Select></Field>;
  }
  function promptEditor(kind: Kind) {
    const prompt = selection[kind];
    const inputKind = kind === 'deployment' || kind === 'deployment_diagram' || kind === 'infrastructure' ? 'server' : kind === 'architecture' ? 'mixed' : 'code';
    return <div className="flex flex-col gap-3">
      {choose(`${titles[kind]} prompt`, `${kind}-prompt`, prompt?.id ?? '', data!.prompts.filter(p => p.input_kind === inputKind || p.input_kind === 'mixed'), id => void selectPrompt(kind, id))}
      {prompt?.error && <Alert variant="destructive"><AlertDescription>{prompt.error} <Button type="button" variant="link" onClick={() => void selectPrompt(kind, prompt.id)}>Reload prompt</Button></AlertDescription></Alert>}
      {prompt?.versionId ? <>
        <Field><FieldLabel htmlFor={`${kind}-content`}>Prompt for this run · active version {prompt.version}</FieldLabel><Textarea id={`${kind}-content`} rows={9} maxLength={100000} className="font-mono text-sm" value={prompt.content} onChange={e => setSelection(current => ({ ...current, [kind]: { ...prompt, content: e.target.value } }))} /><FieldDescription>Changes here apply to this generation only.</FieldDescription></Field>
        <div className="flex flex-wrap items-center gap-4 text-sm"><Button type="button" variant="outline" size="sm" disabled={prompt.content === prompt.activeContent} onClick={() => setSelection(current => ({ ...current, [kind]: { ...prompt, content: prompt.activeContent } }))}>Reset to active prompt</Button><Link className="underline" to={`/documentation/prompts/${prompt.id}`} target="_blank" rel="noreferrer">Manage saved prompt versions</Link></div>
      </> : prompt?.id && !prompt.error ? <Skeleton className="h-32 w-full" /> : null}
    </div>;
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!ready || pending) return;
    setError(''); setNotice(''); setPending(true);
    try {
      await draft.flush();
      await generateDocumentation({ ...value, workflow_id: workflow?.id, code_graph_hashes: needsCode ? graphHashes : {},
        service_id: value.scope === 'service' ? value.service_id : null,
        environment_id: value.scope === 'environment' || deployment ? value.environment_id : null,
        code_project_ids: needsCode ? value.code_project_ids : [], server_ids: deployment || infrastructure ? value.server_ids : [],
        units: selectedKinds.map(kind => {
          const prompt = selection[kind]!;
          return { kind, prompt_id: prompt.id, prompt_version_id: prompt.versionId, ...(prompt.content !== prompt.activeContent ? { prompt_override: prompt.content } : {}) };
        }),
      });
      setNotice('Generation queued. The documents will appear below when ready.'); onQueued();
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not start generation'); }
    finally { setPending(false); }
  }
  if (!data) return error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : <Skeleton className="h-48 w-full" />;
  if (workflow && !(value.scope === 'service' ? service : environment)) return <Alert><AlertDescription>The {value.scope} is no longer available. Phase history and generated documents remain below; generating again requires a new workflow with an available {value.scope}.</AlertDescription></Alert>;
  return <form onSubmit={submit} className="flex flex-col gap-5 rounded-lg border p-5">
    <div><h2 className="text-lg font-semibold">Generate documentation</h2><p className="text-sm text-muted-foreground">Document a whole environment or select the documents to create for one service.</p></div>
    <fieldset disabled={pending} className="flex min-w-0 flex-col gap-5">
      <FieldGroup className="grid gap-4 md:grid-cols-2 [container-type:normal]">
        {!workflow && choose('Documentation for', 'generation-scope', value.scope, [{ id: 'environment', name: 'Whole environment' }, { id: 'service', name: 'Service' }], id => { if (id) setValue({ ...value, scope: id as GenerationInput['scope'], environment_id: null, server_ids: [] }); })}
        {choose('Model', 'generation-model', value.model_id, data.models, id => setValue({ ...value, model_id: id }))}
      </FieldGroup>
      {!data.models.length && <FieldDescription>Add an enabled text model in <Link className="underline" to="/llm-settings">LLM Settings</Link>.</FieldDescription>}
      {value.scope === 'environment' ? <>
        {!workflow && choose('Environment', 'generation-environment', value.environment_id ?? '', data.environments, id => setValue({ ...value, environment_id: id || null, server_ids: [] }))}
        {environment && <p className="text-sm">{environment.services.length} services · {environment.servers.length} servers</p>}
        <FieldSet><FieldLegend>Architecture documents</FieldLegend><FieldGroup data-slot="checkbox-group">
          {environmentKinds.map(kind => <Field orientation="horizontal" key={kind}><Checkbox id={`output-${kind}`} checked={environmentSelection.includes(kind)} onCheckedChange={checked => setEnvironmentSelection(current => checked ? [...current, kind] : current.filter(k => k !== kind))} /><FieldLabel htmlFor={`output-${kind}`}>{titles[kind]}</FieldLabel></Field>)}
        </FieldGroup><FieldDescription>Generate either document or both. The full architecture explains functional relationships, request and data flows, deployment, networking and failure impact, with three D2 / TALA views. It uses related saved documentation. Infrastructure inspects one server over SSH and contributes its findings when both are selected.</FieldDescription></FieldSet>
        {infrastructure && <>
          {choose('Infrastructure server', 'infrastructure-server', value.server_ids[0] ?? '', (environment?.servers ?? []).filter(server => {
            const available = data.servers.find(s => s.id === server.id);
            return available?.ssh_allowed && available.access_enabled && available.ssh_username;
          }).map(server => ({ id: server.id, name: `${server.name} · ${server.hostname}` })), id => setValue({ ...value, server_ids: id ? [id] : [] }))}
          <FieldDescription>Select one server in this environment with SSH access configured. Uses its saved global SSH settings. Covers runtimes, storage, networking, configuration locations, logging and monitoring.</FieldDescription>
        </>}
        {environmentKinds.filter(kind => environmentSelection.includes(kind)).map(kind => <section id={`phase-${kind}`} key={kind} className="flex flex-col gap-4 rounded-md border p-4"><h3 className="font-semibold">{titles[kind]}</h3>{promptEditor(kind)}</section>)}
      </> : <>
        {!workflow && choose('Service', 'generation-service', value.service_id ?? '', data.services, id => { setValue({ ...value, service_id: id || null, environment_id: null, code_project_ids: [], server_ids: [] }); setKinds([]); })}
        {service && <>
          <FieldGroup><h3 className="font-medium">Source code projects</h3><FieldDescription>Leave all unchecked to include all linked projects. Generate the graph explicitly after changing source files.</FieldDescription>{service.repositories.filter(p => !p.archived).map(p => <Field orientation="horizontal" key={p.id}><Checkbox id={`generation-project-${p.id}`} checked={value.code_project_ids.includes(p.id)} onCheckedChange={checked => setValue({ ...value, code_project_ids: checked ? [...value.code_project_ids, p.id] : value.code_project_ids.filter(id => id !== p.id) })} /><FieldLabel htmlFor={`generation-project-${p.id}`}>{p.name}</FieldLabel></Field>)}{!service.repositories.some(p => !p.archived) && <FieldDescription>No linked code projects. The document will say that code is not available.</FieldDescription>}</FieldGroup>
          <CodeGraphPhase projectIds={selectedProjectIds} onReady={setGraphHashes} workflowId={workflow?.id} beforeRun={draft.flush} onQueued={onQueued} />
          <CodeDocumentationPhase kind="overview" key={`${service.id}:overview`} serviceId={service.id} modelId={value.model_id} projectIds={selectedProjectIds}
            graphHashes={graphHashes} workflow={workflow} beforeRun={draft.flush} prompt={data.prompts.find(p => p.name === 'Application overview')} onQueued={onQueued} />
          <CodeDocumentationPhase kind="api" key={`${service.id}:api`} serviceId={service.id} modelId={value.model_id} projectIds={selectedProjectIds}
            graphHashes={graphHashes} workflow={workflow} beforeRun={draft.flush} prompt={data.prompts.find(p => p.name === 'API documentation')} onQueued={onQueued} />
          <CodeDocumentationPhase kind="messaging" key={`${service.id}:messaging`} serviceId={service.id} modelId={value.model_id} projectIds={selectedProjectIds}
            graphHashes={graphHashes} workflow={workflow} beforeRun={draft.flush} prompt={data.prompts.find(p => p.name === 'Messaging documentation')} onQueued={onQueued} />
          <div id="phase-additional"><h3 className="font-semibold">Additional documentation</h3><p className="text-sm text-muted-foreground">These existing document actions run separately when you click Generate documents.</p></div>
          <FieldGroup>{serviceKinds.map(kind => <Field orientation="horizontal" key={kind}><Checkbox id={`output-${kind}`} checked={kinds.includes(kind)} onCheckedChange={checked => setKinds(current => checked ? [...current, kind] : current.filter(k => k !== kind))} /><FieldLabel htmlFor={`output-${kind}`}>{titles[kind]}</FieldLabel></Field>)}</FieldGroup>
          <FieldDescription>Each selection creates a separate document. The code diagram shows internal components; the deployment diagram shows the service on the selected environment. Missing code produces an explicit document and skips the code-analysis prompt.</FieldDescription>
          {!graphsReady && <FieldDescription>Complete Phase 1 before generating code documentation.</FieldDescription>}
          {deployment && <>
            {choose('Environment', 'generation-environment', value.environment_id ?? '', data.environments.filter(e => e.services.some(s => s.id === service.id)), id => setValue({ ...value, environment_id: id || null, server_ids: [] }))}
            <FieldGroup><h3 className="font-medium">Servers to inspect</h3><FieldDescription>Uses each server’s saved global SSH user and private key for inspection. Configure access in Servers.</FieldDescription>{environment?.servers.map(server => {
              const available = data.servers.find(s => s.id === server.id);
              const enabled = available?.ssh_allowed && available.access_enabled && available.ssh_username;
              return <Field orientation="horizontal" key={server.id}><Checkbox id={`generation-server-${server.id}`} disabled={!enabled} checked={value.server_ids.includes(server.id)} onCheckedChange={checked => setValue({ ...value, server_ids: checked ? [...value.server_ids, server.id] : value.server_ids.filter(id => id !== server.id) })} /><FieldLabel htmlFor={`generation-server-${server.id}`}>{server.name} · {server.hostname}{!enabled && ' · Configure SSH access first'}</FieldLabel></Field>;
            })}{!environment?.servers.length && <FieldDescription>Select an environment with servers.</FieldDescription>}</FieldGroup>
          </>}
          {serviceKinds.filter(kind => kinds.includes(kind)).map(kind => <section key={kind} className="flex flex-col gap-4 rounded-md border p-4"><h3 className="font-semibold">{titles[kind]}</h3>{promptEditor(kind)}</section>)}
        </>}
      </>}
      <Field><FieldLabel htmlFor="generation-instructions">Additional context and requests</FieldLabel><Textarea id="generation-instructions" rows={4} maxLength={20000} value={value.instructions} onChange={e => setValue({ ...value, instructions: e.target.value })} placeholder="Important flows, runtime name, questions to answer, known deployment details…" /></Field>
      <Field orientation="horizontal"><Checkbox id="generation-update" checked={value.update_existing} onCheckedChange={checked => setValue({ ...value, update_existing: Boolean(checked) })} /><FieldLabel htmlFor="generation-update">Create new versions of existing documents</FieldLabel></Field>
      <FieldDescription>Previous versions stay available. Concurrent manual edits are preserved.</FieldDescription>
    </fieldset>
    {workflow && <p role="status" className="text-sm text-muted-foreground">Workflow settings · {draft.status || 'Saved'}</p>}
    {draft.error && <Alert variant="destructive"><AlertDescription>{draft.error}<Button type="button" variant="link" onClick={() => void draft.flush().catch(() => {})}>Retry saving</Button></AlertDescription></Alert>}
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {notice && <Alert role="status"><AlertDescription>{notice}</AlertDescription></Alert>}
    <Button type="submit" className="self-start" disabled={pending || !ready}>{pending ? 'Queuing…' : 'Generate documents'}</Button>
  </form>;
}
