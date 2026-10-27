import { useEffect, useMemo, useState } from "react";
import { ApiError } from "~/lib/api";
import { isResourceScope, previewSession, type AgentOptions, type ChatScope, type SessionDetail } from "./api";
import { AGENT_KINDS } from "~/lib/resources";
import ResourceTabs, { type ResourceGroup, type ResourceItem } from "~/features/agents/ResourceTabs";
import { OWN, scopeResource, startInput, withScope, type AgentSettings } from "./agentSettings";
import ToolsList from "~/features/agents/ToolsList";
import OutputSchemaPicker from "~/features/outputSchemas/OutputSchemaPicker";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "~/components/ui/collapsible";
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "~/components/ui/field";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Textarea } from "~/components/ui/textarea";

/** The documents of a document's run show before the resource kinds, always on. */
const DOCUMENTS = { key: "documents", plural: "Documents" } as const;

/** The Ask agent settings for a document, or the narrowing of a saved agent: changeable before a conversation starts (Preview shows the JSON the next message
 * starts with), read-only while a conversation is open (its saved configuration; Start new chat with these settings). */
export default function AgentSettingsSheet({
  open,
  onOpenChange,
  scope,
  options,
  settings: given,
  onChange,
  previewMessage,
  session,
  onStartNew,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  scope: ChatScope;
  options: AgentOptions;
  settings: AgentSettings;
  onChange: (patch: Partial<AgentSettings>) => void;
  previewMessage: string;
  session: SessionDetail | null;
  onStartNew: () => void;
}) {
  // Memoised: a fresh object every render would clear the preview on unrelated re-renders.
  const on = scopeResource(scope);
  const settings = useMemo(() => withScope(scope, given, options), [on?.type, on?.id, given, options]);
  // writing from a resource: the chat is on it ("what the agent may use to write about web-1")
  const onName = on ? options.resources[`${on.type}s`].find((item) => item.id === on.id)?.name : undefined;
  const agent = options.agent;
  const locked = session !== null && session.status !== "closed";
  // A saved agent's model and prompt: the run's own while it is open, else the agent's current ones.
  const agentModel = locked ? session!.model_name : agent?.model?.name;
  const agentPrompt = locked ? (session!.prompt_snapshot?.system_prompt ?? "") : agent?.system_prompt;
  const [preview, setPreview] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  useEffect(() => {
    setPreview(null);
  }, [settings, previewMessage]); // a changed input makes the preview stale
  const { chosen, experts } = settings;
  const choosePrompt = (key: string) =>
    onChange({
      promptKey: key,
      systemPrompt:
        key === OWN ? settings.systemPrompt : (options.prompts.find((item) => item.id === key)?.content ?? ""),
    });
  const ready = !busy && (agent ? true : !!settings.modelId && !!settings.systemPrompt.trim());
  async function runPreview() {
    setBusy(true);
    setError(null);
    try {
      const result = await previewSession(
        startInput(scope, options, settings, previewMessage.trim() || "(your next message)"),
      );
      setPreview(JSON.stringify(result, null, 2));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : e instanceof Error ? e.message : "The request failed.");
    } finally {
      setBusy(false);
    }
  }

  // A saved agent's run lists only the agent's resources (the options hold no others), each on until switched off; another
  // chat lists every resource. A type with none is left out. A new document is implied (the run writes it); a document's
  // run has its document always on; a server, service or environment chat has its resource always on.
  const groups: ResourceGroup[] = [DOCUMENTS, ...AGENT_KINDS]
    .map(({ key, plural }) => {
      const offered =
        key === "documents"
          ? scope.kind === "new-document"
            ? []
            : options.resources.documents
          : options.resources[key];
      const items = offered.map((item): ResourceItem => {
        if (key === "documents")
          return { id: item.id, name: item.name, locked: !agent, note: "The agent may change it (as a draft)." };
        const server = key === "servers" ? options.resources.servers.find((entry) => entry.id === item.id) : undefined;
        const unusable = server !== undefined && !server.usable;
        // A saved agent's server without usable SSH is still given (only its SSH is left out).
        return {
          id: item.id,
          name: item.name,
          locked: on !== undefined && key === `${on.type}s` && item.id === on.id,
          disabled: unusable && !agent,
          note: unusable ? server.reason : null,
        };
      });
      return { key, label: plural, many: plural.toLowerCase(), items };
    })
    .filter((group) => group.items.length > 0);

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        if (!busy) onOpenChange(next);
      }}
    >
      <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle>Agent settings</SheetTitle>
          <SheetDescription>
            {locked
              ? "This conversation's settings. They cannot change while it is open."
              : agent
                ? "What the agent may use in the next conversation."
                : scope.kind === "new-document"
                  ? on
                    ? `What the agent may use to write documents about “${onName ?? on.id}”.`
                    : "What the agent may use to write documents. Pick the resources to write about."
                  : isResourceScope(scope)
                    ? `What the agent may use in a chat on “${scope.name}”.`
                    : `What the agent may use in the next conversation on “${scope.kind === "document" ? scope.title : ""}”.`}
          </SheetDescription>
        </SheetHeader>
        <FieldGroup className="mt-4">
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <fieldset disabled={locked} className="flex min-w-0 flex-col gap-6">
            {agent ? (
              <Field>
                <FieldLabel>Model</FieldLabel>
                <p className="text-sm">{agentModel || "No model"}</p>
                <FieldDescription>Change them in General.</FieldDescription>
              </Field>
            ) : (
              <Field>
                <FieldLabel htmlFor="settings-model">Model</FieldLabel>
                <Select
                  value={settings.modelId}
                  onValueChange={(modelId) => onChange({ modelId })}
                  disabled={locked || options.models.length === 0}
                >
                  <SelectTrigger id="settings-model" aria-label="Model">
                    <SelectValue placeholder="No models" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {options.models.map((item) => (
                        <SelectItem key={item.id} value={item.id}>
                          {item.name}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
                {options.models.length === 0 && (
                  <FieldDescription className="text-destructive">Add a model in Models first.</FieldDescription>
                )}
              </Field>
            )}
          </fieldset>
          {/* Outside the disabled fieldsets: a read-only run's tabs and filter still work (its switches stay disabled). */}
          <FieldSet>
            <FieldLegend>Servers and resources</FieldLegend>
            <FieldDescription>
              Every run gets every tool; these resources decide what the tools may reach.
            </FieldDescription>
            <ResourceTabs
              groups={groups}
              disabled={locked}
              value={{
                documents: settings.documents ?? [],
                ...Object.fromEntries(AGENT_KINDS.map(({ key, field }) => [key, chosen[field]])),
              }}
              onChange={(key, ids) => {
                if (key === "documents") {
                  if (agent) onChange({ documents: ids });
                  return;
                }
                const field = AGENT_KINDS.find((kind) => kind.key === key)!.field;
                onChange({ chosen: { ...chosen, [field]: ids } });
              }}
            />
          </FieldSet>
          <fieldset disabled={locked} className="flex min-w-0 flex-col gap-6">
            <FieldSet>
              <FieldLegend>Experts</FieldLegend>
              {options.experts.length === 0 && <FieldDescription>No experts.</FieldDescription>}
              {options.experts.map((expert) => (
                <Field key={expert.id} orientation="horizontal">
                  <Checkbox
                    id={`settings-expert-${expert.id}`}
                    aria-label={expert.name}
                    disabled={locked}
                    checked={experts.includes(expert.id)}
                    onCheckedChange={(on) =>
                      onChange({
                        experts: on === true ? [...experts, expert.id] : experts.filter((id) => id !== expert.id),
                      })
                    }
                  />
                  <FieldLabel htmlFor={`settings-expert-${expert.id}`}>{expert.name}</FieldLabel>
                  <FieldDescription>{expert.when_to_use}</FieldDescription>
                </Field>
              ))}
            </FieldSet>
            {agent && (
              <OutputSchemaPicker
                idPrefix="settings"
                value={settings.outputSchema ?? null}
                disabled={locked}
                onChange={(outputSchema) => onChange({ outputSchema })}
              />
            )}
          </fieldset>
          {/* Outside the disabled fieldset: a read-only run's prompt can still be expanded (its fields stay disabled). */}
          <Collapsible open={advanced} onOpenChange={setAdvanced}>
            <CollapsibleTrigger asChild>
              <Button type="button" variant="ghost">
                Advanced
              </Button>
            </CollapsibleTrigger>
            <CollapsibleContent className="mt-2 flex flex-col gap-4">
              {agent ? (
                <Field>
                  <FieldLabel>Prompt</FieldLabel>
                  <pre
                    aria-label="Agent prompt"
                    tabIndex={0}
                    className="max-h-96 overflow-auto whitespace-pre-wrap break-words text-xs"
                  >
                    {agentPrompt}
                  </pre>
                  <FieldDescription>Change them in General.</FieldDescription>
                </Field>
              ) : (
                <>
                  <Field>
                    <FieldLabel htmlFor="settings-prompt">Prompt</FieldLabel>
                    <Select value={settings.promptKey} onValueChange={choosePrompt} disabled={locked}>
                      <SelectTrigger id="settings-prompt" aria-label="Prompt">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectGroup>
                          {options.prompts.map((item) => (
                            <SelectItem key={item.id} value={item.id}>
                              {item.name}
                            </SelectItem>
                          ))}
                          <SelectItem value={OWN}>Own prompt</SelectItem>
                        </SelectGroup>
                      </SelectContent>
                    </Select>
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="settings-system-prompt">System prompt</FieldLabel>
                    <Textarea
                      id="settings-system-prompt"
                      aria-label="System prompt"
                      rows={12}
                      disabled={locked}
                      value={settings.systemPrompt}
                      onChange={(e) => onChange({ systemPrompt: e.target.value })}
                    />
                    <FieldDescription>
                      Changes apply to this run only; the prompt in the library stays as it is.
                    </FieldDescription>
                  </Field>
                </>
              )}
              <Field>
                <FieldLabel>Tools</FieldLabel>
                <ToolsList
                  off={locked ? (session!.disabled_tools ?? []) : settings.disabledTools}
                  locked={locked ? [] : agent?.disabled_tools}
                  disabled={locked}
                  onChange={(disabledTools) => onChange({ disabledTools })}
                />
                <FieldDescription>
                  Applies to the next conversation; the chat keeps the tools it started with.
                </FieldDescription>
              </Field>
            </CollapsibleContent>
          </Collapsible>
          {locked ? (
            <>
              <pre
                aria-label="Run configuration"
                tabIndex={0}
                className="max-h-96 overflow-auto whitespace-pre-wrap break-words text-xs"
              >
                {JSON.stringify(session!.config ?? {}, null, 2)}
              </pre>
              <div className="flex justify-end gap-2">
                <Button type="button" onClick={onStartNew}>
                  Start new chat with these settings
                </Button>
              </div>
            </>
          ) : (
            <>
              {preview !== null && (
                <pre
                  aria-label="Configuration preview"
                  tabIndex={0}
                  className="max-h-96 overflow-auto whitespace-pre-wrap break-words text-xs"
                >
                  {preview}
                </pre>
              )}
              <div className="flex justify-end gap-2">
                <Button type="button" variant="outline" disabled={!ready} onClick={() => void runPreview()}>
                  {busy && <Spinner data-icon="inline-start" />}Preview
                </Button>
                <Button type="button" onClick={() => onOpenChange(false)}>
                  Done
                </Button>
              </div>
            </>
          )}
        </FieldGroup>
      </SheetContent>
    </Sheet>
  );
}
