import { useDocumentationDraft } from '~/hooks/useDocumentationDraft';
import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';
import { generateDocumentation, getDocumentationGenerations, getDocumentationPrompt, getDocumentVersion,
  type DocumentationWorkflow, type DocumentationGeneration, type DocumentationPrompt, type DocumentationPromptVersion, type DocumentVersion } from '~/lib/documents';
import Markdown from './Markdown';
import DocumentationTokenUsage from './DocumentationTokenUsage';
import ApiAnalysisProgress from './ApiAnalysisProgress';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '~/components/ui/collapsible';
import { FieldGroup, Field, FieldDescription, FieldLabel } from '~/components/ui/field';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/select';
import { Skeleton } from '~/components/ui/skeleton';
import { Textarea } from '~/components/ui/textarea';

export default function CodeDocumentationPhase({ kind, serviceId, modelId, projectIds, graphHashes, prompt, onQueued, workflow, beforeRun }: {
  kind: 'overview' | 'api' | 'messaging'; serviceId: string; modelId: string; projectIds: string[]; graphHashes: Record<string, string>;
  workflow?: DocumentationWorkflow; beforeRun?: () => Promise<void>; prompt?: DocumentationPrompt; onQueued: () => void;
}) {
  const phase = kind === 'overview'
    ? { number: 2, title: 'Application overview', label: 'Overview', action: 'overview',
        description: 'Explain the application’s purpose, components, dependencies and main flows using the selected source and graph.',
        placeholder: 'Business context or flows the overview should explain…' }
    : kind === 'api' ? { number: 3, title: 'API documentation', label: 'API', action: 'API documentation',
        description: 'Locate endpoints in the graph, search and read their related source, then combine the saved contracts into one document.',
        placeholder: 'API consumers, important contracts or endpoint groups to investigate…' }
    : { number: 4, title: 'Messaging documentation', label: 'Messaging', action: 'messaging documentation',
        description: 'Discover producers and consumers, then trace message contracts, handlers, state changes, acknowledgements and retries through source and graph evidence.',
        placeholder: 'Important subjects, queues, events or asynchronous flows to investigate…' };
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [active, setActive] = useState<DocumentationPromptVersion | null>(null);
  const [content, setContent] = useState(workflow?.drafts[kind]?.content ?? '');
  const [instructions, setInstructions] = useState(workflow?.drafts[kind]?.instructions ?? '');
  const [rows, setRows] = useState<DocumentationGeneration[] | null>(null);
  const [selected, setSelected] = useState(params.get(`${kind}_run`) ?? '');
  const [document, setDocument] = useState<DocumentVersion | null>(null);
  const [error, setError] = useState('');
  const [previewError, setPreviewError] = useState('');
  const [pending, setPending] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [promptRefresh, setPromptRefresh] = useState(0);

  const draft = useDocumentationDraft(workflow?.id, kind, workflow?.drafts[kind], {content, instructions}, active !== null);
  function selectRun(id: string) {
    setSelected(id);
    if (workflow) { const next = new URLSearchParams(params); next.set(`${kind}_run`, id); navigate({search:next.toString(),hash:`#phase-${kind}`},{replace:true,preventScrollReset:true}); }
  }

  useEffect(() => {
    let cancelled = false;
    setActive(null);
    if (prompt) getDocumentationPrompt(prompt.id).then(detail => {
      if (cancelled) return;
      const version = detail.versions.find(v => v.id === detail.active_version_id);
      if (!detail.enabled || !version) throw new Error(`The ${phase.action} prompt has no active version.`);
      setActive(version); if (!workflow?.drafts[kind] || promptRefresh > 0) setContent(version.content);
    }).catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : `Could not load the ${phase.action} prompt`); });
    return () => { cancelled = true; };
  }, [prompt?.id, promptRefresh]);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const result = await getDocumentationGenerations(0, controller.signal, { serviceId, kind, workflowId: workflow?.id });
        if (controller.signal.aborted) return;
        setRows(result.generations);
        setSelected(previous => result.generations.some(row => row.job_id === previous) ? previous : result.generations[0]?.job_id ?? '');
        if (result.generations.some(row => ['queued', 'running'].includes(row.status))) timer = setTimeout(load, 2000);
      } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : `Could not load ${phase.action} history`); }
    }
    void load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [serviceId, kind, refresh]);
  const run = rows?.find(row => row.job_id === selected);
  const artifact = run?.result?.documents?.[0];
  useEffect(() => {
    const controller = new AbortController();
    setDocument(null); setPreviewError('');
    if (artifact) getDocumentVersion(artifact.id, artifact.version_id, controller.signal).then(value => {
      if (!controller.signal.aborted) setDocument(value);
    }).catch(e => { if (!controller.signal.aborted) setPreviewError(e instanceof Error ? e.message : 'Could not load this document version'); });
    return () => controller.abort();
  }, [artifact?.id, artifact?.version_id]);

  const busy = pending || Boolean(rows?.some(row => ['queued', 'running'].includes(row.status)));
  const graphsReady = projectIds.every(id => graphHashes[id]);
  const hashes = Object.fromEntries(projectIds.filter(id => graphHashes[id]).map(id => [id, graphHashes[id]]));
  const matchesSource = run && graphsReady && Object.keys(run.code_graph_hashes ?? {}).length === projectIds.length
    && projectIds.every(id => run.code_graph_hashes?.[id] === hashes[id]);
  const savedPrompt = run?.prompt_snapshot?.find(p => p.kind === kind);

  async function generate() {
    if (!prompt || !active || !modelId || !graphsReady || busy || !content.trim()) return;
    setPending(true); setError('');
    try {
      await beforeRun?.(); await draft.flush();
      const result = await generateDocumentation({ workflow_id: workflow?.id, scope: 'service', service_id: serviceId, model_id: modelId,
        environment_id: null, code_project_ids: projectIds, code_graph_hashes: hashes, server_ids: [],
        instructions, update_existing: true, units: [{ kind, prompt_id: prompt.id,
          prompt_version_id: active.id, ...(content !== active.content ? { prompt_override: content } : {}) }] });
      selectRun(result.job_id); setRefresh(value => value + 1); onQueued();
    } catch (e) { setError(e instanceof Error ? e.message : `Could not generate ${phase.action}`); }
    finally { setPending(false); }
  }

  return <section id={`phase-${kind}`} className="flex min-w-0 flex-col gap-4 rounded-md border p-4" aria-label={`${phase.title} phase`}>
    <div><h3 className="font-semibold">Phase {phase.number} · {phase.title}</h3><p className="text-sm text-muted-foreground">{phase.description} This phase runs only when you start it.</p></div>
    {!prompt ? <Alert><AlertDescription>Activate an <Link className="underline" to="/documentation/prompts">{phase.title} prompt</Link> to use this phase.</AlertDescription></Alert> : <FieldGroup>
      {active ? <Field><FieldLabel htmlFor={`${kind}-prompt`}>{phase.label} prompt · active version {active.version}</FieldLabel><Textarea id={`${kind}-prompt`} rows={9} maxLength={100000} value={content} onChange={e => setContent(e.target.value)} disabled={busy} /><FieldDescription>Edits apply to this run and are saved with its history.</FieldDescription></Field> : <Skeleton className="h-32 w-full" />}
      <div className="flex flex-wrap items-center gap-3"><Button type="button" variant="outline" size="sm" disabled={busy || !active || content === active.content} onClick={() => setContent(active!.content)}>Reset {phase.label.toLowerCase()} prompt</Button><Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => { setError(''); setPromptRefresh(v => v + 1); }}>Reload active {phase.label.toLowerCase()} prompt</Button><Link className="text-sm underline" to={`/documentation/prompts/${prompt.id}`} target="_blank" rel="noreferrer">Manage {phase.label.toLowerCase()} prompt versions</Link></div>
      <Field><FieldLabel htmlFor={`${kind}-instructions`}>{phase.label} context and requests</FieldLabel><Textarea id={`${kind}-instructions`} rows={2} maxLength={20000} value={instructions} onChange={e => setInstructions(e.target.value)} disabled={busy} placeholder={phase.placeholder} /></Field>
      {!graphsReady && <FieldDescription>Generate and inspect the selected code graph first.</FieldDescription>}
      {!projectIds.length && <FieldDescription>No code is linked. This phase will save a “Code is not available” document without calling the model.</FieldDescription>}
      <div className="flex flex-wrap items-center gap-3"><Button type="button" disabled={busy || rows === null || !active || !modelId || !graphsReady || !content.trim()} onClick={() => void generate()}>{busy ? `Generating ${phase.action}…` : rows?.some(row => row.document_count) ? `Regenerate ${phase.action}` : `Generate ${phase.action}`}</Button><span className="text-xs text-muted-foreground">Regeneration saves a new document version. Subsequent phases start only when you request them.</span></div>
    </FieldGroup>}
    {workflow && <p role="status" className="text-sm text-muted-foreground">{phase.label} draft · {draft.status || 'Saved'}</p>}
    {draft.error && <Alert variant="destructive"><AlertDescription>{draft.error}<Button type="button" variant="link" onClick={() => void draft.flush().catch(() => {})}>Retry saving</Button></AlertDescription></Alert>}
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <div className="flex flex-wrap items-center gap-3"><h4 className="font-medium">Inspect {phase.action}</h4><Button type="button" variant="outline" size="sm" onClick={() => { setError(''); setRefresh(v => v + 1); }}>Refresh {phase.label.toLowerCase()} history</Button></div>
    {rows === null ? <Skeleton className="h-12 w-full" /> : rows.length === 0 ? <p className="text-sm text-muted-foreground">No {phase.action} has been generated for this service.</p> : <>
      <Field><FieldLabel htmlFor={`${kind}-run`}>{phase.label} run</FieldLabel><Select value={selected} onValueChange={selectRun}><SelectTrigger id={`${kind}-run`}><SelectValue /></SelectTrigger><SelectContent><SelectGroup>{rows.map(row => <SelectItem key={row.job_id} value={row.job_id}>{new Date(row.created_at).toLocaleString()} · {row.status}{row.result?.documents?.[0] ? ` · v${row.result.documents[0].revision}` : ''}</SelectItem>)}</SelectGroup></SelectContent></Select></Field>
      {run && <>
        <DocumentationTokenUsage run={run} />
        <div className="flex flex-wrap items-center gap-3"><Badge variant={run.status === 'failed' ? 'destructive' : 'secondary'}>{run.status}</Badge>{run.status === 'running' && <span className="text-sm">{run.progress}</span>}{artifact && <Link className="text-sm underline" to={`/documents/${artifact.id}`} target="_blank" rel="noreferrer">Open document and version history</Link>}</div>
        {run.error && <Alert variant="destructive"><AlertDescription>{run.error}</AlertDescription></Alert>}
        {kind === 'api' && <ApiAnalysisProgress jobId={run.job_id} status={run.status} />}
        {artifact && !matchesSource && <Alert><AlertDescription>This version uses a different source or graph selection. Regenerate it to use the graph currently selected above.</AlertDescription></Alert>}
        {run.result?.export_error && <Alert><AlertDescription>{run.result.export_error}</AlertDescription></Alert>}
        {run.result?.output_folders?.map(folder => <p key={folder} className="break-all text-xs text-muted-foreground">{folder}/{kind}.md</p>)}
        {savedPrompt && <Collapsible><CollapsibleTrigger asChild><Button type="button" variant="outline" size="sm">Inspect prompt and source snapshot</Button></CollapsibleTrigger><CollapsibleContent className="flex flex-col gap-3 pt-3"><p className="text-sm">Prompt version {savedPrompt.version}{savedPrompt.overridden && ' · edited for this run'}</p><pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-md border p-3 text-xs">{savedPrompt.content}</pre>{run.instructions && <p className="whitespace-pre-wrap text-sm">{run.instructions}</p>}<pre className="overflow-auto text-xs">{JSON.stringify(run.code_graph_hashes, null, 2)}</pre></CollapsibleContent></Collapsible>}
        {previewError ? <Alert variant="destructive"><AlertDescription>{previewError}</AlertDescription></Alert> : document ? <Collapsible><CollapsibleTrigger asChild><Button type="button" variant="outline" size="sm">Preview {phase.action}</Button></CollapsibleTrigger><CollapsibleContent className="min-w-0 rounded-md border p-4"><Markdown content={document.markdown} /></CollapsibleContent></Collapsible> : artifact ? <Skeleton className="h-48 w-full" /> : null}
      </>}
    </>}
  </section>;
}
