import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { ApiError, getEnvironments, getServers, getServices } from '~/lib/api';
import { createSession, DEFAULT_SYSTEM_PROMPT, getGenerationContext, type Selection } from '~/lib/claudeCode';
import { getCodeProjects } from '~/lib/codeProjects';
import { defaultFolder, documentSlug, folderError, getDocumentationFolders, getDocumentationPrompt, getDocumentationPrompts, type DocumentationPrompt } from '~/lib/documents';
import { codeCompatible, getLlmSettings, type ModelProfile } from '~/lib/llm';
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
type Data = { models: ModelProfile[]; prompts: DocumentationPrompt[]; items: Record<Kind, Item[]>; folders: string[] };
const KINDS: { kind: Kind; label: string; legend: string; key: keyof Selection }[] = [
  { kind: 'code', label: 'Code', legend: 'Code projects', key: 'code_project_ids' },
  { kind: 'documents', label: 'Documents', legend: 'Collections', key: 'collection_ids' },
  { kind: 'environments', label: 'Environment', legend: 'Environments', key: 'environment_ids' },
  { kind: 'services', label: 'Services', legend: 'Services', key: 'service_ids' },
  { kind: 'servers', label: 'Servers', legend: 'Servers', key: 'server_ids' }];
const NOTHING: Record<Kind, string[]> = { code: [], documents: [], environments: [], services: [], servers: [] };
const OWN = 'own';
const DEBOUNCE_MS = 300;
const firstLine = (text: string) => text.trim().split('\n')[0] ?? '';

/** A two-button question inline: nothing is replaced until the user says so. */
function Confirm({ question, onReplace, onKeep }: { question: string; onReplace: () => void; onKeep: () => void }) {
  return <div role="group" aria-label={question} className="flex flex-wrap items-center gap-2 text-sm">
    <span>{question}</span>
    <Button type="button" size="sm" variant="destructive" onClick={onReplace}>Replace</Button>
    <Button type="button" size="sm" variant="outline" onClick={onKeep}>Keep my edits</Button>
  </div>;
}

/** Docs → Generate documents → New: an ad-hoc native agent (model, system prompt, context generated from what to
 * document and editable), a saved or own prompt as its first message, and the document's name and folder. */
export default function DocumentGenerationForm({ onCreated }: { onCreated: (id: string) => void }) {
  const [data, setData] = useState<Data | null>(null);
  const [contextOpen, setContextOpen] = useState(false);
  const [modelId, setModelId] = useState('');
  const [systemPrompt, setSystemPrompt] = useState(DEFAULT_SYSTEM_PROMPT);
  const [context, setContext] = useState('');
  const [contextEdited, setContextEdited] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [contextError, setContextError] = useState('');
  const [askRegenerate, setAskRegenerate] = useState(false);
  const [kinds, setKinds] = useState<Kind[]>([]);
  const [chosen, setChosen] = useState<Record<Kind, string[]>>(NOTHING);
  const [promptChoice, setPromptChoice] = useState(OWN);
  const [prompt, setPrompt] = useState('');
  const [promptEdited, setPromptEdited] = useState(false);
  const [askPrompt, setAskPrompt] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [folder, setFolder] = useState('docs');
  const [folderTouched, setFolderTouched] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const latest = useRef(0);

  useEffect(() => {
    let cancelled = false;
    // Prompts, collections and folders only feed choices: a failing request must not block the form.
    Promise.all([getLlmSettings(), getDocumentationPrompts().catch(() => ({ prompts: [] as DocumentationPrompt[] })), getCodeProjects(),
      getCollectionNames().catch(() => ({ collections: [] as CollectionSummary[] })), getEnvironments(), getServices(), getServers(),
      getDocumentationFolders().catch(() => ({ folders: [] as { folder: string }[] }))])
      .then(([settings, prompts, projects, collections, environments, services, servers, docFolders]) => {
        if (cancelled) return;
        const models = settings.models.filter(model => model.enabled && model.api_format !== 'embeddings' && codeCompatible(model))
          .sort((a, b) => a.name.localeCompare(b.name));
        setModelId(models[0]?.id ?? '');
        setData({ models, prompts: prompts.prompts.filter(item => item.enabled && item.active_version_id),
          folders: docFolders.folders.map(item => item.folder), items: {
            code: projects.projects, documents: collections.collections.map(item => ({ id: item.id, name: item.name })),
            environments: environments.environments, services: services.services,
            servers: servers.servers.map(server => ({ id: server.id, name: `${server.name} (${server.hostname})` })) } });
      }).catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load models and what to document'); });
    return () => { cancelled = true; };
  }, []);

  const picked = (kind: Kind) => kinds.includes(kind) ? chosen[kind] : [];
  const selection = Object.fromEntries(KINDS.map(({ kind, key }) => [key, picked(kind)])) as unknown as Selection;
  const selectionKey = JSON.stringify(selection);
  const anything = KINDS.some(({ kind }) => picked(kind).length > 0);

  // The context follows the selection until the user edits it; only the newest response is applied.
  useEffect(() => {
    if (!data || contextEdited) return;
    const request = ++latest.current;
    if (!anything) { setContext(''); setUpdating(false); setContextError(''); return; }
    setUpdating(true);
    const timer = setTimeout(() => {
      getGenerationContext(selection)
        .then(result => { if (request === latest.current) { setContext(result.text); setContextError(''); setUpdating(false); } })
        .catch(e => { if (request === latest.current) { setContextError(e instanceof Error ? e.message : 'Could not update the context'); setUpdating(false); } });
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [selectionKey, contextEdited, data]);  // eslint-disable-line react-hooks/exhaustive-deps

  const toggleKind = (kind: Kind, checked: boolean) => setKinds(current => checked ? [...current, kind] : current.filter(item => item !== kind));
  function toggle(kind: Kind, id: string, checked: boolean) {
    const next = checked ? [...chosen[kind], id] : chosen[kind].filter(item => item !== id);
    setChosen(current => ({ ...current, [kind]: next }));
    if (kind === 'code' && !folderTouched) {
      const first = data?.items.code.find(project => project.id === next[0]);
      setFolder(first ? defaultFolder(first.name) : 'docs');
    }
  }
  async function applyPrompt(value: string) {
    setAskPrompt(null); setPromptChoice(value); setPromptEdited(false);
    if (value === OWN) { setPrompt(''); return; }
    try {
      const detail = await getDocumentationPrompt(value);
      setPrompt(detail.versions.find(version => version.id === detail.active_version_id)?.content ?? '');
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not load the prompt'); }
  }
  function choosePrompt(value: string) {
    if (value === promptChoice) return;
    if (promptEdited && prompt.trim()) setAskPrompt(value); else void applyPrompt(value);
  }

  const target = folder.trim();
  const badFolder = target ? folderError(target) : '';
  const ready = Boolean(modelId && systemPrompt.trim() && prompt.trim() && anything && documentSlug(name) && target && !badFolder);
  const model = data?.models.find(item => item.id === modelId);

  async function submit() {
    if (!ready || pending) return;
    setPending(true); setError('');
    try {
      const session = await createSession({ model_id: modelId, ...selection, system_prompt: systemPrompt, context, prompt,
        prompt_id: promptChoice === OWN ? null : promptChoice, document_name: name.trim(), folder: target });
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
      <div className="flex flex-col gap-2">
        <Button type="button" variant="outline" className="self-start" aria-expanded={contextOpen} aria-controls="agent-context"
          onClick={() => setContextOpen(open => !open)}>Agent context</Button>
        {!contextOpen && <p className="text-sm text-muted-foreground break-words">{model ? `${model.name} · ` : ''}{firstLine(systemPrompt)}</p>}
        <div id="agent-context" hidden={!contextOpen} className="flex flex-col gap-4 rounded-lg border p-4">
          <Field><FieldLabel htmlFor="generate-model">Model</FieldLabel>
            <Select value={modelId || 'none'} onValueChange={value => setModelId(value === 'none' ? '' : value)}>
              <SelectTrigger id="generate-model"><SelectValue /></SelectTrigger>
              <SelectContent><SelectGroup>{data.models.length === 0 && <SelectItem value="none">No models</SelectItem>}
                {data.models.map(item => <SelectItem key={item.id} value={item.id}>{item.name}</SelectItem>)}</SelectGroup></SelectContent></Select>
            {data.models.length === 0 && <FieldDescription className="text-destructive">Add a model in Models first.</FieldDescription>}</Field>
          <Field><FieldLabel htmlFor="generate-system-prompt">System prompt</FieldLabel>
            <Textarea id="generate-system-prompt" rows={4} maxLength={20000} value={systemPrompt} onChange={e => setSystemPrompt(e.target.value)} /></Field>
          <Field><FieldLabel htmlFor="generate-context">Context</FieldLabel>
            <Textarea id="generate-context" rows={10} maxLength={100000} value={context} placeholder="Choose what to document below."
              onChange={e => { setContext(e.target.value); setContextEdited(true); latest.current++; setUpdating(false); }} />
            {updating && <FieldDescription>Updating…</FieldDescription>}
            {contextError && <FieldDescription className="text-destructive">{contextError}</FieldDescription>}
            {contextEdited && !askRegenerate && <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              <span>Edited — no longer follows your selection</span>
              <Button type="button" size="sm" variant="link" onClick={() => setAskRegenerate(true)}>Regenerate from selection</Button></div>}
            {askRegenerate && <Confirm question="Replace your edits?" onKeep={() => setAskRegenerate(false)}
              onReplace={() => { setAskRegenerate(false); setContextEdited(false); }} />}
          </Field>
        </div>
      </div>
      <FieldSet><FieldLegend>What to document</FieldLegend>
        <FieldDescription>The agent gets exactly what you choose. Environments and services also bring their code projects and servers.</FieldDescription>
        {KINDS.map(({ kind, label, legend }) => <div key={kind} className="flex flex-col gap-2">
          {check(`what-${kind}`, label, kinds.includes(kind), value => toggleKind(kind, value))}
          {kinds.includes(kind) && <FieldSet className="ml-6"><FieldLegend variant="label">{legend}</FieldLegend>
            {data.items[kind].length === 0 && <FieldDescription>Nothing to choose yet.</FieldDescription>}
            {data.items[kind].map(item => check(`generate-${kind}-${item.id}`, item.name, chosen[kind].includes(item.id), value => toggle(kind, item.id, value)))}
          </FieldSet>}
        </div>)}
      </FieldSet>
      <Field><FieldLabel htmlFor="generate-prompt">Prompt</FieldLabel>
        <Select value={promptChoice} onValueChange={choosePrompt}>
          <SelectTrigger id="generate-prompt"><SelectValue /></SelectTrigger>
          <SelectContent><SelectGroup><SelectItem value={OWN}>Write my own</SelectItem>
            {data.prompts.map(item => <SelectItem key={item.id} value={item.id}>{`${item.name} · ${item.input_kind}`}</SelectItem>)}</SelectGroup></SelectContent></Select>
        {askPrompt !== null && <Confirm question="Replace your edited prompt?" onKeep={() => setAskPrompt(null)} onReplace={() => void applyPrompt(askPrompt)} />}
      </Field>
      <Field><FieldLabel htmlFor="generate-prompt-text">Prompt text</FieldLabel>
        <Textarea id="generate-prompt-text" rows={6} maxLength={100000} value={prompt} placeholder="What the agent should write"
          onChange={e => { setPrompt(e.target.value); setPromptEdited(true); }} />
        <FieldDescription>Editing a saved prompt changes only this run.</FieldDescription></Field>
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

/** Docs → Generate documents → New; Generate opens the session. */
export function NewGeneration() {
  const navigate = useNavigate();
  return <DocumentGenerationForm onCreated={id => navigate(`/documentation/generations/${id}`)} />;
}
