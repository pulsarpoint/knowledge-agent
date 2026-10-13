import { useEffect, useMemo, useState } from 'react';
import { ApiError } from '~/lib/api';
import { previewSession, type AgentOptions, type AgentResources, type ChatScope, type SessionDetail } from '~/lib/sessions';
import { OWN, shownTools, startInput, withScope, type AgentSettings } from '~/lib/agentSettings';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Checkbox } from '~/components/ui/checkbox';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '~/components/ui/collapsible';
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from '~/components/ui/field';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/select';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '~/components/ui/sheet';
import { Spinner } from '~/components/ui/spinner';
import { Switch } from '~/components/ui/switch';
import { Textarea } from '~/components/ui/textarea';

const KINDS: [keyof AgentOptions['resources'], keyof AgentResources, string][] = [
  ['servers', 'server_ids', 'Servers'], ['services', 'service_ids', 'Services'], ['environments', 'environment_ids', 'Environments'],
  ['code_projects', 'code_project_ids', 'Code projects'], ['collections', 'collection_ids', 'Collections']];
const GROUPS: [string, string][] = [['code', 'Code'], ['documents', 'Documents'], ['servers', 'Servers'], ['inventory', 'Inventory']];

/** The Ask agent settings for a document, or the narrowing of a saved agent: editable before a conversation starts (Preview shows the JSON the next message
 * starts with), read-only while a conversation is open (its saved configuration; Start new chat with these settings). */
export default function AgentSettingsSheet({ open, onOpenChange, scope, options, settings: given, onChange,
  previewMessage, session, onStartNew }: {
  open: boolean; onOpenChange: (open: boolean) => void; scope: ChatScope; options: AgentOptions;
  settings: AgentSettings; onChange: (patch: Partial<AgentSettings>) => void; previewMessage: string;
  session: SessionDetail | null; onStartNew: () => void }) {
  // Memoised: a fresh object every render would clear the preview on unrelated re-renders.
  const scopeServer = scope.kind === 'server' ? scope.id : null;
  const settings = useMemo(() => withScope(scope, given), [scopeServer, given]);
  const agent = options.agent;
  const locked = session !== null && session.status !== 'closed';
  // A saved agent's model and prompt: the run's own while it is open, else the agent's current ones.
  const agentModel = locked ? session!.model_name : agent?.model?.name;
  const agentPrompt = locked ? session!.prompt_snapshot?.system_prompt ?? '' : agent?.system_prompt;
  const [preview, setPreview] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  useEffect(() => { setPreview(null); }, [settings, previewMessage]);  // a changed input makes the preview stale
  const shown = shownTools(options, settings);
  const { chosen, off, experts } = settings;
  const toggle = (key: keyof AgentResources, id: string, on: boolean) =>
    onChange({ chosen: { ...chosen, [key]: on ? [...chosen[key], id] : chosen[key].filter(item => item !== id) } });
  const choosePrompt = (key: string) => onChange({ promptKey: key,
    systemPrompt: key === OWN ? settings.systemPrompt : options.prompts.find(item => item.id === key)?.content ?? '' });
  const ready = !busy && (agent ? true : !!settings.modelId && !!settings.systemPrompt.trim());
  async function runPreview() {
    setBusy(true); setError(null);
    try {
      const result = await previewSession(startInput(scope, options, settings, previewMessage.trim() || '(your next message)'));
      setPreview(JSON.stringify(result, null, 2));
    } catch (e) { setError(e instanceof ApiError ? e.message : e instanceof Error ? e.message : 'The request failed.'); }
    finally { setBusy(false); }
  }

  return <Sheet open={open} onOpenChange={next => { if (!busy) onOpenChange(next); }}>
    <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
      <SheetHeader><SheetTitle>Agent settings</SheetTitle>
        <SheetDescription>{locked ? "This conversation's settings. They cannot change while it is open."
          : agent ? 'What the agent may use in the next conversation.'
          : scope.kind === 'new-document' ? `What the agent may use for “${scope.title}”.`
          : scope.kind === 'server' ? `What the agent may use to explore “${scope.name}”.`
          : `What the agent may use in the next conversation on “${scope.kind === 'document' ? scope.title : ''}”.`}</SheetDescription></SheetHeader>
      <FieldGroup className="mt-4">
        {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
        <fieldset disabled={locked} className="flex min-w-0 flex-col gap-6">
          {agent ? <Field><FieldLabel>Model</FieldLabel><p className="text-sm">{agentModel || 'No model'}</p>
            <FieldDescription>Change them in General.</FieldDescription></Field> :
          <Field><FieldLabel htmlFor="settings-model">Model</FieldLabel>
            <Select value={settings.modelId} onValueChange={modelId => onChange({ modelId })} disabled={locked || options.models.length === 0}>
              <SelectTrigger id="settings-model" aria-label="Model"><SelectValue placeholder="No models" /></SelectTrigger>
              <SelectContent><SelectGroup>{options.models.map(item => <SelectItem key={item.id} value={item.id}>{item.name}</SelectItem>)}</SelectGroup></SelectContent></Select>
            {options.models.length === 0 && <FieldDescription className="text-destructive">Add a model in Models first.</FieldDescription>}</Field>}
          <FieldSet><FieldLegend>Servers and resources</FieldLegend>
            {agent?.resources_fixed ? <FieldDescription>This agent's resources are fixed.</FieldDescription> : <>
            {/* A new document is implied: the run writes it. */}
            {scope.kind !== 'new-document' && options.resources.documents.length > 0 && <FieldSet><FieldLegend variant="label">Documents</FieldLegend>
              {options.resources.documents.map(item => <Field key={item.id} orientation="horizontal">
                <Checkbox id={`settings-document-${item.id}`} aria-label={item.name} checked disabled />
                <FieldLabel htmlFor={`settings-document-${item.id}`}>{item.name}</FieldLabel>
                <FieldDescription>{item.editable ? 'The agent may change it (as a draft).' : 'Read only.'}</FieldDescription></Field>)}
            </FieldSet>}
            {KINDS.map(([key, idsKey, label]) => {
              const items = options.resources[key];
              if (items.length === 0) return null;
              return <FieldSet key={key}><FieldLegend variant="label">{label}</FieldLegend>
                {items.map(item => {
                  const unusable = 'usable' in item && !item.usable;
                  const pinned = scope.kind === 'server' && key === 'servers' && item.id === scope.id;  // the run is about this server
                  const id = `settings-${key}-${item.id}`;
                  return <Field key={item.id} orientation="horizontal">
                    <Checkbox id={id} aria-label={item.name} disabled={locked || unusable || pinned} checked={pinned || chosen[idsKey].includes(item.id)}
                      onCheckedChange={on => toggle(idsKey, item.id, on === true)} />
                    <FieldLabel htmlFor={id}>{item.name}</FieldLabel>
                    {unusable && 'reason' in item && item.reason && <FieldDescription>{item.reason}</FieldDescription>}
                  </Field>;
                })}
              </FieldSet>;
            })}
            </>}
          </FieldSet>
          <FieldSet><FieldLegend>Tools</FieldLegend>
            {GROUPS.map(([group, label]) => {
              const tools = shown.filter(tool => tool.group === group);
              if (tools.length === 0) return null;
              return <FieldSet key={group}><FieldLegend variant="label">{label}</FieldLegend>
                {tools.map(tool => <Field key={tool.name} orientation="horizontal">
                  <Checkbox id={`settings-tool-${tool.name}`} aria-label={tool.label} disabled={locked} checked={!off.includes(tool.name)}
                    onCheckedChange={on => onChange({ off: on === true ? off.filter(name => name !== tool.name) : [...off, tool.name] })} />
                  <FieldLabel htmlFor={`settings-tool-${tool.name}`}>{tool.label}</FieldLabel>
                  <FieldDescription>{tool.summary}</FieldDescription></Field>)}
              </FieldSet>;
            })}
          </FieldSet>
          <FieldSet><FieldLegend>Agents</FieldLegend>
            {(!agent || agent.subagents_allowed) && <Field orientation="horizontal"><Switch id="settings-subagents" aria-label="Allow subagents" disabled={locked} checked={settings.subagents}
              onCheckedChange={subagents => onChange({ subagents })} />
              <FieldLabel htmlFor="settings-subagents">Allow subagents</FieldLabel></Field>}
            {options.experts.length === 0 && <FieldDescription>No experts.</FieldDescription>}
            {options.experts.map(expert => <Field key={expert.id} orientation="horizontal">
              <Checkbox id={`settings-expert-${expert.id}`} aria-label={expert.name} disabled={locked} checked={experts.includes(expert.id)}
                onCheckedChange={on => onChange({ experts: on === true ? [...experts, expert.id] : experts.filter(id => id !== expert.id) })} />
              <FieldLabel htmlFor={`settings-expert-${expert.id}`}>{expert.name}</FieldLabel>
              <FieldDescription>{expert.when_to_use}</FieldDescription></Field>)}
          </FieldSet>
        </fieldset>
        {/* Outside the disabled fieldset: a read-only run's prompt can still be expanded (its fields stay disabled). */}
        <Collapsible open={advanced} onOpenChange={setAdvanced}>
          <CollapsibleTrigger asChild><Button type="button" variant="ghost">Advanced</Button></CollapsibleTrigger>
          <CollapsibleContent className="mt-2 flex flex-col gap-4">
            {agent ? <Field><FieldLabel>Prompt</FieldLabel>
              <pre aria-label="Agent prompt" tabIndex={0} className="max-h-96 overflow-auto whitespace-pre-wrap break-words text-xs">{agentPrompt}</pre>
              <FieldDescription>Change them in General.</FieldDescription></Field> : <>
            <Field><FieldLabel htmlFor="settings-prompt">Prompt</FieldLabel>
              <Select value={settings.promptKey} onValueChange={choosePrompt} disabled={locked}>
                <SelectTrigger id="settings-prompt" aria-label="Prompt"><SelectValue /></SelectTrigger>
                <SelectContent><SelectGroup>
                  {options.prompts.map(item => <SelectItem key={item.id} value={item.id}>{item.name}</SelectItem>)}
                  <SelectItem value={OWN}>Own prompt</SelectItem>
                </SelectGroup></SelectContent></Select></Field>
            <Field><FieldLabel htmlFor="settings-system-prompt">System prompt</FieldLabel>
              <Textarea id="settings-system-prompt" aria-label="System prompt" rows={12} disabled={locked} value={settings.systemPrompt}
                onChange={e => onChange({ systemPrompt: e.target.value })} />
              <FieldDescription>Changes apply to this run only; the prompt in the library stays as it is.</FieldDescription></Field>
            </>}
          </CollapsibleContent>
        </Collapsible>
        {locked ? <>
          <pre aria-label="Run configuration" tabIndex={0} className="max-h-96 overflow-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify(session!.config ?? {}, null, 2)}</pre>
          <div className="flex justify-end gap-2"><Button type="button" onClick={onStartNew}>Start new chat with these settings</Button></div>
        </> : <>
          {preview !== null && <pre aria-label="Configuration preview" tabIndex={0} className="max-h-96 overflow-auto whitespace-pre-wrap break-words text-xs">{preview}</pre>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" disabled={!ready} onClick={() => void runPreview()}>{busy && <Spinner data-icon="inline-start" />}Preview</Button>
            <Button type="button" onClick={() => onOpenChange(false)}>Done</Button>
          </div>
        </>}
      </FieldGroup>
    </SheetContent>
  </Sheet>;
}
