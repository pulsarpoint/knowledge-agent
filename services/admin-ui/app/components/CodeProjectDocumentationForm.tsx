import { useEffect, useRef, useState } from 'react';
import { ApiError } from '~/lib/api';
import { getCodeProjects, type CodeProject } from '~/lib/codeProjects';
import { defaultFolder, documentSlug, folderError, generateDocumentation, getDocumentationFolders, getDocumentationPrompt,
  getDocumentationPrompts, PROMPT_CHANGED, SAME_DOCUMENT_NAME, type DocumentationPrompt } from '~/lib/documents';
import CodeGraphPhase from './CodeGraphPhase';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle } from '~/components/ui/alert-dialog';
import { Button } from '~/components/ui/button';
import { Field, FieldDescription, FieldLabel } from '~/components/ui/field';
import { Input } from '~/components/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/select';
import { Skeleton } from '~/components/ui/skeleton';
import { Textarea } from '~/components/ui/textarea';

type Prompt = { id: string; versionId: string; version: number; error?: string };

/** One code project, a code or mixed prompt plus comments, one document saved into a documentation folder. */
export default function CodeProjectDocumentationForm({ modelId, onQueued }: { modelId: string; onQueued: () => void }) {
  const [data, setData] = useState<{ projects: CodeProject[]; prompts: DocumentationPrompt[]; folders: string[] } | null>(null);
  const [projectId, setProjectId] = useState('');
  const [prompt, setPrompt] = useState<Prompt | null>(null);
  const [comments, setComments] = useState('');
  const [name, setName] = useState('');
  const [folder, setFolder] = useState('');
  const [folderTouched, setFolderTouched] = useState(false);
  const [graphHashes, setGraphHashes] = useState<Record<string, string>>({});
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const promptRequest = useRef(0);

  useEffect(() => {
    let cancelled = false;
    // The folder list only feeds suggestions: a failing request must not block the form.
    Promise.all([getCodeProjects(), getDocumentationPrompts(), getDocumentationFolders().catch(() => ({ folders: [] }))]).then(([projects, prompts, folders]) => {
      if (cancelled) return;
      setData({ projects: projects.projects, folders: folders.folders.map(f => f.folder),
        // The run has no server access: server-only prompts are not offered.
        prompts: prompts.prompts.filter(p => p.enabled && p.active_version_id && (p.input_kind === 'code' || p.input_kind === 'mixed')) });
    }).catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load code projects and prompts'); });
    return () => { cancelled = true; };
  }, []);

  async function selectPrompt(id: string) {
    const request = ++promptRequest.current;
    setPrompt(id ? { id, versionId: '', version: 0 } : null);
    if (!id) return;
    try {
      const detail = await getDocumentationPrompt(id);
      const active = detail.versions.find(v => v.id === detail.active_version_id);
      if (!detail.enabled || !active) throw new Error('This prompt has no active version. Select another prompt.');
      if (promptRequest.current === request) setPrompt({ id, versionId: active.id, version: active.version });
    } catch (e) {
      if (promptRequest.current === request) setPrompt({ id, versionId: '', version: 0, error: e instanceof Error ? e.message : 'Could not load the active prompt' });
    }
  }
  function selectProject(id: string) {
    setProjectId(id);
    const project = data?.projects.find(p => p.id === id);
    if (project && !folderTouched) setFolder(defaultFolder(project.name));
  }
  const target = folder.trim();  // trimmed on submit, not while typing
  const badFolder = target ? folderError(target) : '';
  const ready = Boolean(modelId && projectId && graphHashes[projectId] && prompt?.versionId && documentSlug(name) && target && !badFolder);

  async function submit(updateExisting: boolean) {
    if (!ready || pending || !prompt) return;
    setPending(true); setError('');
    try {
      await generateDocumentation({ scope: 'code_project', model_id: modelId, code_project_id: projectId, prompt_id: prompt.id,
        prompt_version_id: prompt.versionId, code_graph_hashes: { [projectId]: graphHashes[projectId] }, comments,
        document_name: name.trim(), folder: target, update_existing: updateExisting });
      setConfirming(false);
      onQueued();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.message === SAME_DOCUMENT_NAME && !updateExisting) setConfirming(true);
      else { setConfirming(false); setError(e instanceof Error ? e.message : 'Could not start generation'); }
    } finally { setPending(false); }
  }
  function choose(label: string, id: string, current: string, items: { id: string; name: string }[], change: (id: string) => void) {
    return <Field><FieldLabel htmlFor={id}>{label}</FieldLabel><Select value={current || 'none'} onValueChange={v => change(v === 'none' ? '' : v)}><SelectTrigger id={id}><SelectValue /></SelectTrigger><SelectContent><SelectGroup><SelectItem value="none">Select…</SelectItem>{items.map(item => <SelectItem key={item.id} value={item.id}>{item.name}</SelectItem>)}</SelectGroup></SelectContent></Select></Field>;
  }
  if (!data) return error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : <Skeleton className="h-48 w-full" />;
  return <form className="flex flex-col gap-5" onSubmit={e => { e.preventDefault(); void submit(false); }}>
    <fieldset disabled={pending} className="flex min-w-0 flex-col gap-5">
      {choose('Code project', 'project-doc-project', projectId, data.projects, selectProject)}
      {projectId && <CodeGraphPhase projectIds={[projectId]} onReady={setGraphHashes} />}
      {choose('Prompt', 'project-doc-prompt', prompt?.id ?? '', data.prompts, id => void selectPrompt(id))}
      <FieldDescription>Prompts for code or mixed input. Server prompts are not offered: this run reads code only, without server access, RAG or MCP tools.</FieldDescription>
      {prompt?.error && <Alert variant="destructive"><AlertDescription>{prompt.error} <Button type="button" variant="link" onClick={() => void selectPrompt(prompt.id)}>Reload prompt</Button></AlertDescription></Alert>}
      <Field><FieldLabel htmlFor="project-doc-comments">Comments</FieldLabel><Textarea id="project-doc-comments" rows={4} maxLength={20000} value={comments} onChange={e => setComments(e.target.value)} placeholder="Extra instructions for this run, appended to the prompt" /></Field>
      <Field><FieldLabel htmlFor="project-doc-name">Document name</FieldLabel><Input id="project-doc-name" maxLength={240} value={name} onChange={e => setName(e.target.value)} placeholder="Payments – API reference" />{name && !documentSlug(name) && <FieldDescription className="text-destructive">Use letters or digits in the name.</FieldDescription>}</Field>
      <Field><FieldLabel htmlFor="project-doc-folder">Documentation folder</FieldLabel><Input id="project-doc-folder" list="documentation-folders" maxLength={200} value={folder} onChange={e => { setFolderTouched(true); setFolder(e.target.value); }} placeholder="payments or team/payments" />
        <datalist id="documentation-folders">{data.folders.map(f => <option key={f} value={f} />)}</datalist>
        <FieldDescription className={badFolder ? 'text-destructive' : undefined}>{badFolder || `Saved as ${target || '<folder>'}/${documentSlug(name) || '<document>'}/README.md in the generated documents folder. The default is the project name plus -docs; pick an existing folder or type a new one. Make it searchable in RAG → Collections.`}</FieldDescription></Field>
      {projectId && !graphHashes[projectId] && <FieldDescription>Generate the code graph before generating the document.</FieldDescription>}
    </fieldset>
    {error && <Alert variant="destructive"><AlertDescription>{error}{error === PROMPT_CHANGED && prompt && <Button type="button" variant="link" onClick={() => void selectPrompt(prompt.id)}>Reload prompt</Button>}</AlertDescription></Alert>}
    <Button type="submit" className="self-start" disabled={pending || !ready}>{pending ? 'Queuing…' : 'Generate'}</Button>
    <AlertDialog open={confirming} onOpenChange={setConfirming}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Save a new version?</AlertDialogTitle>
      <AlertDialogDescription>{`${target} already has a document named “${name.trim()}”. Generating saves a new version of it; earlier versions stay available.`}</AlertDialogDescription></AlertDialogHeader>
      <AlertDialogFooter><AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel><AlertDialogAction disabled={pending} onClick={e => { e.preventDefault(); void submit(true); }}>Save new version</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </form>;
}
