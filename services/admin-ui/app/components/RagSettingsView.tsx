import { useEffect, useState } from "react";
import { Link } from "react-router";
import { getLlmSettings, type ModelProfile } from "../lib/llm";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { ragRequest, type RagConfig, type RagSettings } from "../lib/rag";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";
import RagSourceLocation from "./RagSourceLocation";

export default function RagSettingsView() {
  const [config, setConfig] = useState<RagConfig | null>(null);
  const [settings, setSettings] = useState<RagSettings | null>(null);
  const [models, setModels] = useState<ModelProfile[]>([]);
  const [embeddingModelId, setEmbeddingModelId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([ragRequest<RagConfig>("settings", "GET", undefined, controller.signal), getLlmSettings()]).then(([row, llm]) => {
      if (controller.signal.aborted) return;
      setConfig(row); setSettings(row.settings); setEmbeddingModelId(row.embedding_model_id);
      setModels(llm.models.filter(model => model.api_format === "embeddings")); setError("");
    }).catch((e) => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [reload]);
  const dirty = JSON.stringify(settings) !== JSON.stringify(config?.settings) || embeddingModelId !== config?.embedding_model_id;
  const selectedModel = models.find(model => model.id === embeddingModelId);
  const invalidSelection = settings?.embedding_enabled && !selectedModel?.enabled;
  async function run(action: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : "RAG request failed"); } finally { setBusy(false); }
  }
  return <div className="flex flex-col gap-5">
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {notice && <Alert><AlertDescription role="status">{notice}</AlertDescription></Alert>}
    <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-sm text-muted-foreground">Processing and model settings take effect without restarting Docker.</p><Button variant="outline" disabled={busy} onClick={() => setReload((v) => v + 1)}>Reload settings</Button></div>
    {!settings && !error && <Skeleton className="h-64" />}
    {settings && config && <form className="flex max-w-3xl flex-col gap-8" onSubmit={(e) => { e.preventDefault(); run(async () => {
      const row = await ragRequest<RagConfig>("settings", "PUT", { revision: config.revision, settings, embedding_model_id: embeddingModelId });
      setConfig(row); setSettings(row.settings); setEmbeddingModelId(row.embedding_model_id); setNotice("RAG settings saved. Process affected folders to apply indexing changes.");
    }); }}>
      <RagSourceLocation location={config.source_location} />
      <FieldSet disabled={busy} className="rounded-lg border p-5"><FieldLegend>File processing</FieldLegend><FieldGroup>
        <div className="grid gap-5 sm:grid-cols-3">{([
          ["chunk_size", "Chunk size (characters)", 200, 12000], ["chunk_overlap", "Overlap (characters)", 0, 2000], ["max_file_bytes", "Maximum file size (bytes)", 100, 10000000],
        ] as const).map(([name, label, min, max]) => <Field key={name}><FieldLabel htmlFor={name}>{label}</FieldLabel><Input id={name} required type="number" min={min} max={max} value={settings[name]} onChange={(e) => setSettings({ ...settings, [name]: Number(e.target.value) })} /></Field>)}</div>
        <Field><FieldLabel>Supported file extensions</FieldLabel><div className="flex flex-wrap gap-5">{[".md", ".markdown", ".txt", ".json"].map((extension) => <Field key={extension} orientation="horizontal" className="w-auto"><Checkbox id={`ext-${extension}`} checked={settings.extensions.includes(extension)} onCheckedChange={(checked) => setSettings({ ...settings, extensions: checked ? [...settings.extensions, extension] : settings.extensions.filter((v) => v !== extension) })} /><FieldLabel htmlFor={`ext-${extension}`}>{extension}</FieldLabel></Field>)}</div><FieldDescription>UTF-8 text only. Other files remain visible but are excluded from processing.</FieldDescription></Field>
      </FieldGroup></FieldSet>
      <FieldSet disabled={busy} className="rounded-lg border p-5"><FieldLegend>Embeddings</FieldLegend><FieldGroup>
        <Field orientation="horizontal"><Checkbox id="embedding-enabled" checked={settings.embedding_enabled} onCheckedChange={(checked) => setSettings({ ...settings, embedding_enabled: checked === true })} /><FieldLabel htmlFor="embedding-enabled">Enable embedding processing and vector search</FieldLabel></Field>
        <Field><FieldLabel htmlFor="embedding-model">Embedding model</FieldLabel>
          <Select value={embeddingModelId ?? 'none'} disabled={busy} onValueChange={value => setEmbeddingModelId(value === 'none' ? null : value)}>
            <SelectTrigger id="embedding-model"><SelectValue /></SelectTrigger><SelectContent><SelectGroup>
              <SelectItem value="none">Not configured</SelectItem>
              {models.map(model => <SelectItem key={model.id} value={model.id} disabled={!model.enabled}>{model.name}{!model.enabled && ' (disabled)'}</SelectItem>)}
            </SelectGroup></SelectContent>
          </Select>
          <FieldDescription>Manage embedding connections, dimensions, versions, and API keys in <Link className="underline underline-offset-4" to="/llm-settings">LLM Settings</Link>.</FieldDescription>
          {selectedModel && <FieldDescription>{selectedModel.model} · {selectedModel.embedding_dimensions} dimensions · Version {selectedModel.embedding_version}</FieldDescription>}
          {invalidSelection && <FieldDescription>Select an enabled embedding model before saving with embedding processing enabled.</FieldDescription>}
        </Field>
        <FieldDescription>Changing the model, version, or dimensions refreshes vectors while reusing unchanged parsed text. Increment the version when replacing a model behind the same name.</FieldDescription>
        <Button type="button" variant="outline" disabled={busy || dirty || !settings.embedding_enabled || !selectedModel?.enabled} onClick={() => run(async () => { await ragRequest("test/embedding", "POST"); setNotice("Embedding connection successful; returned dimensions match settings."); })}>Test embedding connection</Button>
      </FieldGroup></FieldSet>
      <p className="text-sm text-muted-foreground">Save settings before testing the selected embedding connection.</p>
      <Button className="self-start" type="submit" disabled={busy || !dirty || invalidSelection}>Save RAG settings</Button>
    </form>}
  </div>;
}
