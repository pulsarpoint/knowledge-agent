import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router';
import { ApiError } from '~/lib/api';
import { agentRunPath, getAgentOptions, previewAgentSession, startAgentSession, toolApplies,
  type AgentOptions, type AgentResources, type StartInput } from '~/lib/agentSessions';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Checkbox } from '~/components/ui/checkbox';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '~/components/ui/collapsible';
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from '~/components/ui/field';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/select';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '~/components/ui/sheet';
import { Skeleton } from '~/components/ui/skeleton';
import { Spinner } from '~/components/ui/spinner';
import { Switch } from '~/components/ui/switch';
import { Textarea } from '~/components/ui/textarea';

const KINDS: [keyof AgentOptions['resources'], keyof AgentResources, string][] = [
  ['servers', 'server_ids', 'Servers'], ['services', 'service_ids', 'Services'], ['environments', 'environment_ids', 'Environments'],
  ['code_projects', 'code_project_ids', 'Code projects'], ['collections', 'collection_ids', 'Collections']];
const GROUPS: [string, string][] = [['code', 'Code'], ['documents', 'Documents'], ['servers', 'Servers'], ['inventory', 'Inventory']];
const BUILT_IN = 'built-in';

/** Ask agent on a document: an instruction, a model, the servers and resources (the document's links ticked), the
 * tools that apply (all ticked), subagents and experts (off), the system prompt under Advanced; Preview shows exactly
 * what Start saves; Start opens the run page. */
export default function AskAgentSheet({ documentId, documentTitle, onClose }: { documentId: string; documentTitle: string; onClose: () => void }) {
  const navigate = useNavigate();
  const [options, setOptions] = useState<AgentOptions | null>(null);
  const [instruction, setInstruction] = useState('');
  const [modelId, setModelId] = useState('');
  const [promptKey, setPromptKey] = useState(BUILT_IN);
  const [systemPrompt, setSystemPrompt] = useState('');
  const [chosen, setChosen] = useState<AgentResources>({ server_ids: [], service_ids: [], environment_ids: [], code_project_ids: [], collection_ids: [] });
  const [off, setOff] = useState<string[]>([]);  // tools the person unticked
  const [subagents, setSubagents] = useState(false);
  const [experts, setExperts] = useState<string[]>([]);
  const [preview, setPreview] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [advanced, setAdvanced] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    getAgentOptions(documentId, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      setOptions(value); setModelId(value.default_model_id ?? '');
      const first = value.prompts[0];
      setPromptKey(first ? first.id : BUILT_IN); setSystemPrompt(first ? first.content : value.default_prompt.content);
      const linked = (key: keyof AgentOptions['resources']) => value.resources[key]
        .filter(item => item.linked && (!('usable' in item) || item.usable)).map(item => item.id);
      setChosen({ server_ids: linked('servers'), service_ids: linked('services'), environment_ids: linked('environments'),
        code_project_ids: linked('code_projects'), collection_ids: linked('collections') });
    }).catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not load the options.'); });
    return () => controller.abort();
  }, [documentId]);

  // The preview shows what Start would save; any change to an input makes it stale.
  useEffect(() => { setPreview(null); }, [instruction, modelId, promptKey, systemPrompt, chosen, off, subagents, experts]);

  const shown = useMemo(() => options?.tools.filter(tool => toolApplies(tool, chosen)) ?? [], [options, chosen]);
  const value = (): StartInput => ({
    kind: 'revision', target: { document_id: documentId }, instruction: instruction.trim(), model_id: modelId,
    prompt_id: promptKey === BUILT_IN ? null : promptKey, system_prompt: systemPrompt.trim(), resources: chosen,
    tools: shown.filter(tool => !off.includes(tool.name)).map(tool => tool.name), expert_ids: experts, subagents });
  async function act(run: () => Promise<void>) {
    setBusy(true); setError(null);
    try { await run(); } catch (e) { setError(e instanceof ApiError ? e.message : e instanceof Error ? e.message : 'The request failed.'); }
    finally { setBusy(false); }
  }
  const toggle = (key: keyof AgentResources, id: string, on: boolean) => setChosen(current => ({ ...current,
    [key]: on ? [...current[key], id] : current[key].filter(item => item !== id) }));
  const ready = !busy && !!modelId && !!instruction.trim() && !!systemPrompt.trim();
  const choosePrompt = (key: string) => {
    if (!options) return;
    setPromptKey(key);
    setSystemPrompt(key === BUILT_IN ? options.default_prompt.content : options.prompts.find(item => item.id === key)?.content ?? '');
  };

  return <Sheet open onOpenChange={open => { if (!open && !busy) onClose(); }}>
    <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
      <SheetHeader><SheetTitle>Ask agent</SheetTitle>
        <SheetDescription>{`An agent revises “${documentTitle}”. It saves a draft; you review it and decide whether it becomes the live version.`}</SheetDescription></SheetHeader>
      <FieldGroup className="mt-4">
        {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
        {!options && !error && <Skeleton className="h-96" />}
        {options && <>
          <Field><FieldLabel htmlFor="ask-instruction">Instruction</FieldLabel>
            <Textarea id="ask-instruction" aria-label="Instruction" rows={6} autoFocus value={instruction} onChange={e => setInstruction(e.target.value)} /></Field>
          <Field><FieldLabel htmlFor="ask-model">Model</FieldLabel>
            <Select value={modelId} onValueChange={setModelId} disabled={options.models.length === 0}>
              <SelectTrigger id="ask-model" aria-label="Model"><SelectValue placeholder="No models" /></SelectTrigger>
              <SelectContent><SelectGroup>{options.models.map(item => <SelectItem key={item.id} value={item.id}>{item.name}</SelectItem>)}</SelectGroup></SelectContent></Select>
            {options.models.length === 0 && <FieldDescription className="text-destructive">Add a model in Models first.</FieldDescription>}</Field>
          <FieldSet><FieldLegend>Servers and resources</FieldLegend>
            {KINDS.map(([key, idsKey, label]) => {
              const items = options.resources[key];
              if (items.length === 0) return null;
              return <FieldSet key={key}><FieldLegend variant="label">{label}</FieldLegend>
                {items.map(item => {
                  const unusable = 'usable' in item && !item.usable;
                  const id = `ask-${key}-${item.id}`;
                  return <Field key={item.id} orientation="horizontal">
                    <Checkbox id={id} aria-label={item.name} disabled={unusable} checked={chosen[idsKey].includes(item.id)}
                      onCheckedChange={on => toggle(idsKey, item.id, on === true)} />
                    <FieldLabel htmlFor={id}>{item.name}</FieldLabel>
                    {unusable && 'reason' in item && item.reason && <FieldDescription>{item.reason}</FieldDescription>}
                  </Field>;
                })}
              </FieldSet>;
            })}
          </FieldSet>
          <FieldSet><FieldLegend>Tools</FieldLegend>
            {options.fixed_tools.map(tool => <Field key={tool.name} orientation="horizontal">
              <Checkbox id={`ask-fixed-${tool.name}`} aria-label={tool.label} checked disabled />
              <FieldLabel htmlFor={`ask-fixed-${tool.name}`}>{tool.label}</FieldLabel></Field>)}
            {GROUPS.map(([group, label]) => {
              const tools = shown.filter(tool => tool.group === group);
              if (tools.length === 0) return null;
              return <FieldSet key={group}><FieldLegend variant="label">{label}</FieldLegend>
                {tools.map(tool => <Field key={tool.name} orientation="horizontal">
                  <Checkbox id={`ask-tool-${tool.name}`} aria-label={tool.label} checked={!off.includes(tool.name)}
                    onCheckedChange={on => setOff(current => on === true ? current.filter(name => name !== tool.name) : [...current, tool.name])} />
                  <FieldLabel htmlFor={`ask-tool-${tool.name}`}>{tool.label}</FieldLabel>
                  <FieldDescription>{tool.summary}</FieldDescription></Field>)}
              </FieldSet>;
            })}
          </FieldSet>
          <FieldSet><FieldLegend>Agents</FieldLegend>
            <Field orientation="horizontal"><Switch id="ask-subagents" aria-label="Allow subagents" checked={subagents} onCheckedChange={setSubagents} />
              <FieldLabel htmlFor="ask-subagents">Allow subagents</FieldLabel></Field>
            {options.experts.length === 0 && <FieldDescription>No experts.</FieldDescription>}
            {options.experts.map(expert => <Field key={expert.id} orientation="horizontal">
              <Checkbox id={`ask-expert-${expert.id}`} aria-label={expert.name} checked={experts.includes(expert.id)}
                onCheckedChange={on => setExperts(current => on === true ? [...current, expert.id] : current.filter(id => id !== expert.id))} />
              <FieldLabel htmlFor={`ask-expert-${expert.id}`}>{expert.name}</FieldLabel>
              <FieldDescription>{expert.when_to_use}</FieldDescription></Field>)}
          </FieldSet>
          <Collapsible open={advanced} onOpenChange={setAdvanced}>
            <CollapsibleTrigger asChild><Button type="button" variant="ghost">Advanced</Button></CollapsibleTrigger>
            <CollapsibleContent className="mt-2 flex flex-col gap-4">
              <Field><FieldLabel htmlFor="ask-prompt">Prompt</FieldLabel>
                <Select value={promptKey} onValueChange={choosePrompt}>
                  <SelectTrigger id="ask-prompt" aria-label="Prompt"><SelectValue /></SelectTrigger>
                  <SelectContent><SelectGroup>
                    {options.prompts.map(item => <SelectItem key={item.id} value={item.id}>{item.name}</SelectItem>)}
                    <SelectItem value={BUILT_IN}>{options.default_prompt.name}</SelectItem>
                  </SelectGroup></SelectContent></Select></Field>
              <Field><FieldLabel htmlFor="ask-system-prompt">System prompt</FieldLabel>
                <Textarea id="ask-system-prompt" aria-label="System prompt" rows={12} value={systemPrompt} onChange={e => setSystemPrompt(e.target.value)} />
                <FieldDescription>Changes apply to this run only; the prompt in the library stays as it is.</FieldDescription></Field>
            </CollapsibleContent>
          </Collapsible>
          {preview !== null && <pre aria-label="Configuration preview" tabIndex={0} className="max-h-96 overflow-auto whitespace-pre-wrap break-words text-xs">{preview}</pre>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" disabled={busy} onClick={onClose}>Cancel</Button>
            <Button type="button" variant="outline" disabled={!ready} onClick={() => void act(async () => {
              const result = await previewAgentSession(value()); setPreview(JSON.stringify(result, null, 2)); })}>Preview</Button>
            <Button type="button" disabled={!ready} onClick={() => void act(async () => {
              const run = await startAgentSession(value()); navigate(agentRunPath(documentId, run.id)); })}>{busy && <Spinner data-icon="inline-start" />}Start</Button>
          </div>
        </>}
      </FieldGroup>
    </SheetContent>
  </Sheet>;
}
