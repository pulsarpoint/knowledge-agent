import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { ApiError } from "~/lib/api";
import { DISCOVERY_TAG, runPagePath, startDiscovery } from "~/lib/discovery";
import { getDocumentationPrompt, getDocumentationPrompts, type DocumentationPrompt } from "~/lib/documents";
import { codeCompatible, getLlmSettings, type ModelProfile } from "~/lib/llm";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Textarea } from "~/components/ui/textarea";

/** "Ask agent to collect info": a prompt tagged server-discovery (its text editable for this run only) and a model. */
export default function DiscoverySheet({ serverId, serverName, onClose }: { serverId: string; serverName: string; onClose: () => void }) {
  const navigate = useNavigate();
  const [models, setModels] = useState<ModelProfile[] | null>(null);
  const [prompts, setPrompts] = useState<DocumentationPrompt[]>([]);
  const [modelId, setModelId] = useState("");
  const [promptId, setPromptId] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const loaded = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([getLlmSettings(), getDocumentationPrompts(controller.signal, DISCOVERY_TAG)]).then(([settings, found]) => {
      if (controller.signal.aborted) return;
      const usable = settings.models.filter(model => model.enabled && model.api_format !== "embeddings" && codeCompatible(model));
      const ready = found.prompts.filter(item => item.enabled && item.active_version_id);
      setModels(usable); setModelId(usable[0]?.id ?? ""); setPrompts(ready);
      if (ready[0]) void choose(ready[0].id);
    }).catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Could not load models and prompts."); });
    return () => { controller.abort(); loaded.current++; };
  }, []);

  /** Sets the choice and its text together once the text has loaded; a failure or a stale answer leaves the previous choice. */
  async function choose(id: string) {
    const request = ++loaded.current;
    try {
      const detail = await getDocumentationPrompt(id);
      if (request !== loaded.current) return;
      setText(detail.versions.find(version => version.id === detail.active_version_id)?.content ?? "");
      setPromptId(id); setError(null);
    } catch {
      if (request === loaded.current) setError("Could not load the prompt.");
    }
  }
  async function start() {
    setBusy(true); setError(null);
    try {
      const run = await startDiscovery(serverId, { model_id: modelId, prompt: text.trim(), prompt_id: promptId });
      navigate(runPagePath(serverId, run.id));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not start the run.");
    } finally { setBusy(false); }
  }

  return <Sheet open onOpenChange={open => { if (!open && !busy) onClose(); }}>
    <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
      <SheetHeader><SheetTitle>Ask agent to collect info</SheetTitle>
        <SheetDescription>An agent connects to {serverName} over SSH, looks around and proposes services and documents. You accept or reject each proposal.</SheetDescription></SheetHeader>
      <FieldGroup className="mt-4">
        {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
        <Field><FieldLabel htmlFor="discovery-prompt">Prompt</FieldLabel>
          <Select value={promptId ?? ""} onValueChange={id => void choose(id)} disabled={prompts.length === 0}>
            <SelectTrigger id="discovery-prompt" aria-label="Prompt"><SelectValue placeholder="No prompt" /></SelectTrigger>
            <SelectContent><SelectGroup>{prompts.map(item => <SelectItem key={item.id} value={item.id}>{item.name}</SelectItem>)}</SelectGroup></SelectContent></Select>
          {models !== null && prompts.length === 0 && <FieldDescription>No prompt is tagged server-discovery. Add one in Administration → Prompts, or write the instructions here.</FieldDescription>}</Field>
        <Field><FieldLabel htmlFor="discovery-text">Prompt text</FieldLabel>
          <Textarea id="discovery-text" aria-label="Prompt text" rows={14} value={text} onChange={e => setText(e.target.value)} />
          <FieldDescription>Changes apply to this run only; the prompt in the library stays as it is.</FieldDescription></Field>
        <Field><FieldLabel htmlFor="discovery-model">Model</FieldLabel>
          <Select value={modelId} onValueChange={setModelId} disabled={!models?.length}>
            <SelectTrigger id="discovery-model" aria-label="Model"><SelectValue placeholder="No models" /></SelectTrigger>
            <SelectContent><SelectGroup>{models?.map(item => <SelectItem key={item.id} value={item.id}>{item.name}</SelectItem>)}</SelectGroup></SelectContent></Select>
          {models?.length === 0 && <FieldDescription className="text-destructive">Add a model in Models first.</FieldDescription>}</Field>
        <div className="flex justify-end gap-2"><Button variant="outline" disabled={busy} onClick={onClose}>Cancel</Button>
          <Button disabled={busy || !modelId || !text.trim()} onClick={() => void start()}>{busy && <Spinner data-icon="inline-start" />}Start</Button></div>
      </FieldGroup>
    </SheetContent>
  </Sheet>;
}
