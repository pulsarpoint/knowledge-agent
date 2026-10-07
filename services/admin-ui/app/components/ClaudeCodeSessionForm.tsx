import { useEffect, useState } from 'react';
import { ApiError, getServers, type ServerAsset } from '~/lib/api';
import { claudeModel, createSession } from '~/lib/claudeCode';
import { getCodeProjects, type CodeProject } from '~/lib/codeProjects';
import { defaultFolder, documentSlug, folderError, getDocumentationFolders, getDocumentationPrompts, PROMPT_CHANGED,
  type DocumentationPrompt } from '~/lib/documents';
import { getLlmSettings, type ModelProfile } from '~/lib/llm';
import { getAgents, type AgentSummary } from '~/lib/profiles';
import { normalizeFolder } from '~/lib/rag';
import { FolderBrowser } from './RagFolderPicker';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Checkbox } from '~/components/ui/checkbox';
import { Field, FieldContent, FieldDescription, FieldLabel, FieldLegend, FieldSet } from '~/components/ui/field';
import { Input } from '~/components/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/select';
import { Skeleton } from '~/components/ui/skeleton';
import { Textarea } from '~/components/ui/textarea';

type Data = { agents: AgentSummary[]; models: ModelProfile[]; prompts: DocumentationPrompt[]; projects: CodeProject[]; servers: ServerAsset[]; folders: string[] };

/** An interactive session: a Claude Code agent, a Claude model, a prompt, context and the document to write. */
export default function ClaudeCodeSessionForm({ onCreated }: { onCreated: (id: string) => void }) {
  const [data, setData] = useState<Data | null>(null);
  const [reload, setReload] = useState(0);
  const [agentId, setAgentId] = useState('');
  const [modelId, setModelId] = useState('');
  const [promptId, setPromptId] = useState('');
  const [projectIds, setProjectIds] = useState<string[]>([]);
  const [folders, setFolders] = useState<string[]>([]);
  const [choosing, setChoosing] = useState(false);
  const [folderValue, setFolderValue] = useState('');
  const [serverIds, setServerIds] = useState<string[]>([]);
  const [notes, setNotes] = useState('');
  const [comments, setComments] = useState('');
  const [name, setName] = useState('');
  const [folder, setFolder] = useState('docs');
  const [folderTouched, setFolderTouched] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    // The documentation folder list only feeds suggestions: a failing request must not block the form.
    Promise.all([getAgents(), getLlmSettings(), getDocumentationPrompts(), getCodeProjects(), getServers(),
      getDocumentationFolders().catch(() => ({ folders: [] as { folder: string }[] }))]).then(([agents, llm, prompts, projects, servers, docFolders]) => {
      if (cancelled) return;
      const models = llm.models.filter(model => model.enabled && claudeModel(model));
      setData({ agents: agents.agents.filter(agent => agent.runtime === 'claude_code' && agent.enabled), models,
        prompts: prompts.prompts.filter(prompt => prompt.enabled && prompt.active_version_id), projects: projects.projects,
        servers: servers.servers, folders: docFolders.folders.map(item => item.folder) });
      if (models.length === 1) setModelId(models[0].id);
    }).catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load agents, models and context'); });
    return () => { cancelled = true; };
  }, [reload]);

  function toggleProject(id: string, checked: boolean) {
    const next = checked ? [...projectIds, id] : projectIds.filter(item => item !== id);
    setProjectIds(next);
    const first = data?.projects.find(project => project.id === next[0]);
    if (!folderTouched) setFolder(first ? defaultFolder(first.name) : 'docs');
  }
  const toggleServer = (id: string, checked: boolean) => setServerIds(current => checked ? [...current, id] : current.filter(item => item !== id));
  const prompt = data?.prompts.find(item => item.id === promptId);
  const target = folder.trim();
  const badFolder = target ? folderError(target) : '';
  const picked = normalizeFolder(folderValue);
  const ready = Boolean(agentId && modelId && prompt?.active_version_id && (projectIds.length || folders.length)
    && documentSlug(name) && target && !badFolder);

  async function submit() {
    if (!ready || pending || !prompt?.active_version_id) return;
    setPending(true); setError('');
    try {
      const session = await createSession({ agent_id: agentId, model_id: modelId, prompt_id: prompt.id, prompt_version_id: prompt.active_version_id,
        comments, code_project_ids: projectIds, folders, server_ids: serverIds, notes, document_name: name.trim(), folder: target });
      onCreated(session.id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not start the session');
      setPending(false);
    }
  }
  function choose(label: string, id: string, current: string, items: { id: string; name: string }[], change: (id: string) => void) {
    return <Field><FieldLabel htmlFor={id}>{label}</FieldLabel><Select value={current || 'none'} onValueChange={value => change(value === 'none' ? '' : value)}>
      <SelectTrigger id={id}><SelectValue /></SelectTrigger><SelectContent><SelectGroup><SelectItem value="none">Select…</SelectItem>
        {items.map(item => <SelectItem key={item.id} value={item.id}>{item.name}</SelectItem>)}</SelectGroup></SelectContent></Select></Field>;
  }
  function check(id: string, label: string, checked: boolean, change: (checked: boolean) => void) {
    return <Field key={id} orientation="horizontal"><Checkbox id={id} checked={checked} onCheckedChange={value => change(value === true)} />
      <FieldContent><FieldLabel htmlFor={id}>{label}</FieldLabel></FieldContent></Field>;
  }
  if (!data) return error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : <Skeleton className="h-48 w-full" />;
  return <form className="flex flex-col gap-5" onSubmit={e => { e.preventDefault(); void submit(); }}>
    <fieldset disabled={pending} className="flex min-w-0 flex-col gap-5">
      {choose('Agent', 'session-agent', agentId, data.agents, setAgentId)}
      {data.agents.length === 0 && <FieldDescription>Create a Claude Code agent in Agents → New Claude Code agent.</FieldDescription>}
      {choose('Model', 'session-model', modelId, data.models, setModelId)}
      <FieldDescription>Claude connections only: Bedrock inference profiles (eu.anthropic.claude-…) or the Anthropic API, from LLM Settings.</FieldDescription>
      {choose('Prompt', 'session-prompt', promptId, data.prompts, setPromptId)}
      <FieldSet><FieldLegend>Context</FieldLegend>
        <FieldDescription>Code project folders and folders under DATA_ROOT become the agent's read-only working folders; the first one is its working directory. Servers and notes are passed as text: the agent has no server access.</FieldDescription>
        <p className="text-sm font-medium">Code projects</p>
        {data.projects.map(project => check(`session-project-${project.id}`, project.name, projectIds.includes(project.id), checked => toggleProject(project.id, checked)))}
        <p className="text-sm font-medium">Folders under DATA_ROOT</p>
        {folders.length > 0 && <ul className="flex flex-col gap-1">{folders.map(item => <li key={item} className="flex items-center gap-2 text-sm"><code className="break-all">{item}</code>
          <Button type="button" variant="ghost" size="sm" aria-label={`Remove ${item}`} onClick={() => setFolders(current => current.filter(f => f !== item))}>Remove</Button></li>)}</ul>}
        {choosing ? <>
          <FolderBrowser value={folderValue} onChange={setFolderValue} />
          <Button type="button" variant="outline" className="self-start" disabled={!picked || folders.includes(picked)}
            onClick={() => { setFolders(current => [...current, picked]); setFolderValue(''); setChoosing(false); }}>Add folder</Button>
        </> : <Button type="button" variant="outline" className="self-start" onClick={() => setChoosing(true)}>Choose a folder</Button>}
        <p className="text-sm font-medium">Servers</p>
        {data.servers.map(server => check(`session-server-${server.id}`, `${server.name} (${server.hostname})`, serverIds.includes(server.id), checked => toggleServer(server.id, checked)))}
        <Field><FieldLabel htmlFor="session-notes">Notes</FieldLabel><Textarea id="session-notes" rows={3} maxLength={20000} value={notes} onChange={e => setNotes(e.target.value)} /></Field>
      </FieldSet>
      <Field><FieldLabel htmlFor="session-comments">Comments</FieldLabel><Textarea id="session-comments" rows={3} maxLength={20000} value={comments} onChange={e => setComments(e.target.value)} placeholder="Extra instructions for this run" /></Field>
      <Field><FieldLabel htmlFor="session-document-name">Document name</FieldLabel><Input id="session-document-name" maxLength={240} value={name} onChange={e => setName(e.target.value)} placeholder="Payments – API reference" />
        {name && !documentSlug(name) && <FieldDescription className="text-destructive">Use letters or digits in the name.</FieldDescription>}</Field>
      <Field><FieldLabel htmlFor="session-folder">Documentation folder</FieldLabel><Input id="session-folder" list="session-documentation-folders" maxLength={200} value={folder} onChange={e => { setFolderTouched(true); setFolder(e.target.value); }} />
        <datalist id="session-documentation-folders">{data.folders.map(item => <option key={item} value={item} />)}</datalist>
        <FieldDescription className={badFolder ? 'text-destructive' : undefined}>{badFolder || `Save document stores it as ${target || '<folder>'}/${documentSlug(name) || '<document>'}/README.md in the generated documents folder.`}</FieldDescription></Field>
    </fieldset>
    {error && <Alert variant="destructive"><AlertDescription>{error}{error === PROMPT_CHANGED && <Button type="button" variant="link" onClick={() => setReload(n => n + 1)}>Reload prompts</Button>}</AlertDescription></Alert>}
    <Button type="submit" className="self-start" disabled={pending || !ready}>{pending ? 'Starting…' : 'Generate'}</Button>
  </form>;
}
