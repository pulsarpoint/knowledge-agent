import { Link } from "react-router";
import { useEffect, useState, type FormEvent } from "react";
import { CONTENT_EDITOR_HELP, saveAgent, type AgentProfile } from "./api";
import { EXPERT_HELP, SUBAGENTS_HELP } from "./tools";
import { codeCompatible, getLlmSettings, type ModelProfile } from "~/features/llm/api";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from "~/components/ui/card";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Textarea } from "~/components/ui/textarea";
import { Switch } from "~/components/ui/switch";
import { toast } from "sonner";

const NO_MODEL = "none";
/** A model this agent can run. */
const fits = (model: ModelProfile) => model.enabled && model.api_format !== "embeddings" && codeCompatible(model);

/** The agent's General tab details: name, purpose, model, instructions, the Content editor role and its switches. Its
 * resources are saved on the Resources tab; saving here keeps them as they are saved. */
export default function AgentProfileSettings({
  profile,
  onSaved,
  onDirtyChange,
}: {
  profile: AgentProfile;
  onDirtyChange?: (dirty: boolean) => void;
  onSaved: (profile: AgentProfile) => void;
}) {
  const [draft, setDraft] = useState(profile);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [reload, setReload] = useState(0);
  const [models, setModels] = useState<ModelProfile[]>();
  const [modelsError, setModelsError] = useState("");
  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);
  useEffect(() => {
    let live = true;
    setModelsError("");
    getLlmSettings()
      .then((value) => {
        if (live) setModels(value.models);
      })
      .catch((e) => {
        if (live) setModelsError(e instanceof Error ? e.message : "Could not load models.");
      });
    return () => {
      live = false;
    };
  }, [reload]);
  // Saving the prompt or the resources elsewhere must preserve edits in this form.
  useEffect(() => {
    setDraft((current) => ({ ...current, revision: profile.revision }));
  }, [profile.revision]);
  function change(patch: Partial<AgentProfile>) {
    setDraft((current) => ({ ...current, ...patch }));
    setDirty(true);
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const saved = await saveAgent({
        ...draft,
        access: { resources: profile.access.resources, roles: draft.access.roles },
      });
      setDraft(saved);
      setDirty(false);
      onSaved(saved);
      toast.success("Agent configuration saved.", { id: "agent-configuration-saved" });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save this agent.");
    } finally {
      setBusy(false);
    }
  }
  const needsWhen = Boolean(draft.expert) && !draft.when_to_use.trim();
  return (
    <Card>
      <CardHeader>
        <CardTitle>Agent details</CardTitle>
        <CardDescription>Save changes to apply them to new runs.</CardDescription>
      </CardHeader>
      <form onSubmit={(event) => void save(event)}>
        <CardContent className="pb-6">
          <FieldGroup>
            {error && (
              <Alert variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
            <div>
              <FieldGroup>
                <Field>
                  <FieldLabel htmlFor="agent-name">Name</FieldLabel>
                  <Input
                    id="agent-name"
                    required
                    maxLength={100}
                    disabled={busy}
                    value={draft.name}
                    onChange={(e) => change({ name: e.target.value })}
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor="agent-purpose">Purpose</FieldLabel>
                  <Textarea
                    id="agent-purpose"
                    maxLength={4000}
                    disabled={busy}
                    value={draft.description}
                    onChange={(e) => change({ description: e.target.value })}
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor="agent-when">When to use this agent</FieldLabel>
                  <Textarea
                    id="agent-when"
                    maxLength={4000}
                    disabled={busy}
                    value={draft.when_to_use}
                    onChange={(e) => change({ when_to_use: e.target.value })}
                  />
                  <FieldDescription>
                    Describe the tasks that belong to this agent. Agents are currently selected explicitly.
                  </FieldDescription>
                </Field>
                <Field>
                  <FieldLabel htmlFor="agent-model">Model</FieldLabel>
                  <Select
                    value={draft.model_id ?? NO_MODEL}
                    disabled={busy || !models}
                    onValueChange={(value) => change({ model_id: value === NO_MODEL ? null : value })}
                  >
                    <SelectTrigger id="agent-model">
                      <SelectValue placeholder={modelsError ? "Models unavailable" : "Loading models…"} />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectGroup>
                        <SelectItem value={NO_MODEL}>Not configured</SelectItem>
                        {models
                          ?.filter((m) => fits(m) || m.id === draft.model_id)
                          .map((m) => (
                            <SelectItem key={m.id} value={m.id} disabled={!fits(m)}>
                              {m.name}
                              {!m.enabled && " (disabled)"}
                            </SelectItem>
                          ))}
                      </SelectGroup>
                    </SelectContent>
                  </Select>
                  <FieldDescription>
                    {modelsError || (
                      <>
                        An enabled Responses connection or a Claude model. An agent runs only after a model is selected.{" "}
                        <Link to="/llm-settings" className="underline underline-offset-4">
                          Manage model connections
                        </Link>
                      </>
                    )}
                  </FieldDescription>
                </Field>
                <Field>
                  <FieldLabel htmlFor="agent-instructions">Instructions</FieldLabel>
                  <Textarea
                    id="agent-instructions"
                    rows={5}
                    maxLength={40000}
                    disabled={busy}
                    value={draft.instructions}
                    onChange={(e) => change({ instructions: e.target.value })}
                    placeholder="Task guidance, required checks, and how results should be verified…"
                  />
                  <FieldDescription>
                    Additional notes for every run. The system prompt below always applies.
                  </FieldDescription>
                </Field>
                <Field orientation="horizontal">
                  <Switch
                    id="agent-content-editor"
                    disabled={busy}
                    checked={draft.access.roles.content_editor}
                    onCheckedChange={(content_editor) =>
                      change({ access: { ...draft.access, roles: { content_editor } } })
                    }
                  />
                  <div>
                    <FieldLabel htmlFor="agent-content-editor">Content editor</FieldLabel>
                    <FieldDescription>{CONTENT_EDITOR_HELP}</FieldDescription>
                  </div>
                </Field>
                <Field orientation="horizontal">
                  <Switch
                    id="documents-grounded"
                    disabled={busy}
                    checked={draft.documents_grounded}
                    onCheckedChange={(documents_grounded) => change({ documents_grounded })}
                  />
                  <div>
                    <FieldLabel htmlFor="documents-grounded">Answer only from documents</FieldLabel>
                    <FieldDescription>
                      Search the knowledge base, cite every claim, and report questions the documents do not cover.
                    </FieldDescription>
                  </div>
                </Field>
                <Field orientation="horizontal">
                  <Switch
                    id="agent-subagents"
                    disabled={busy}
                    checked={Boolean(draft.subagents)}
                    onCheckedChange={(subagents) => change({ subagents })}
                  />
                  <div>
                    <FieldLabel htmlFor="agent-subagents">May start subagents</FieldLabel>
                    <FieldDescription>{SUBAGENTS_HELP}</FieldDescription>
                  </div>
                </Field>
                <Field orientation="horizontal" data-invalid={needsWhen || undefined}>
                  <Switch
                    id="agent-expert"
                    disabled={busy}
                    checked={Boolean(draft.expert)}
                    onCheckedChange={(expert) => change({ expert })}
                  />
                  <div>
                    <FieldLabel htmlFor="agent-expert">Offer as an expert</FieldLabel>
                    <FieldDescription>{EXPERT_HELP}</FieldDescription>
                    {needsWhen && (
                      <p role="alert" className="text-sm text-destructive">
                        Experts need When to use.
                      </p>
                    )}
                  </div>
                </Field>
                <Field orientation="horizontal">
                  <Switch
                    id="agent-available"
                    disabled={busy}
                    checked={draft.enabled}
                    onCheckedChange={(enabled) => change({ enabled })}
                  />
                  <div>
                    <FieldLabel htmlFor="agent-available">Enable agent</FieldLabel>
                    <FieldDescription>
                      Disabled agents retain their settings and cannot start new tasks.
                    </FieldDescription>
                  </div>
                </Field>
              </FieldGroup>
            </div>
          </FieldGroup>
        </CardContent>
        <CardFooter className="flex-wrap gap-2">
          <Button disabled={busy || !dirty || !draft.name.trim() || needsWhen}>Save details</Button>
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => {
              setDraft(profile);
              setDirty(false);
              setError("");
              setReload((n) => n + 1);
            }}
          >
            {dirty ? "Discard changes" : "Refresh models"}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}
