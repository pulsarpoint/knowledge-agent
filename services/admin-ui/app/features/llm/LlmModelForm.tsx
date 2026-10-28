import { useState, type FormEvent } from "react";
import { ApiError } from "~/lib/api";
import { providerLabels, saveModel, type ModelInput, type ModelProfile, type Provider } from "./api";
import LeaveGuard, { useSheetGuard } from "~/components/LeaveGuard";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldContent, FieldDescription, FieldGroup, FieldLabel, FieldSet } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Switch } from "~/components/ui/switch";

/** New model / Edit <name> in a right Sheet: Create model (or Save) and Cancel; closing it or leaving the page with
 * unsaved changes asks Stay / Leave. */
export default function LlmModelForm({
  model,
  onSaved,
  onClose,
}: {
  model: ModelProfile | null;
  onSaved: (model: ModelProfile) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(model?.name ?? "");
  const [provider, setProvider] = useState<Provider>(model?.provider ?? "openai");
  const [modelId, setModelId] = useState(model?.model ?? "");
  const [baseUrl, setBaseUrl] = useState(model?.base_url ?? "https://api.openai.com/v1");
  const [region, setRegion] = useState(model?.aws_region ?? "");
  const [format, setFormat] = useState<ModelInput["api_format"]>(model?.api_format ?? "responses");
  const [dimensions, setDimensions] = useState(model?.embedding_dimensions ?? 1024);
  const [version, setVersion] = useState(model?.embedding_version ?? "1");
  const embeddings = format === "embeddings";
  const [apiKey, setApiKey] = useState("");
  const [removeKey, setRemoveKey] = useState(false);
  const [enabled, setEnabled] = useState(model?.enabled ?? true);
  // Runs with an output schema send it as strict structured output; off: the schema goes into the instructions.
  const [structured, setStructured] = useState(
    model?.capabilities?.supports_structured_output ?? (model?.provider ?? "openai") === "openai",
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const values = JSON.stringify([
    name,
    provider,
    modelId,
    baseUrl,
    region,
    format,
    dimensions,
    version,
    apiKey,
    removeKey,
    enabled,
    structured,
  ]);
  const [initial] = useState(values);
  const dirty = !saving && values !== initial;
  const guard = useSheetGuard(dirty);
  const close = () => guard.close(onClose);

  function changeProvider(value: Provider) {
    setProvider(value);
    setFormat(embeddings ? "embeddings" : value === "openai" ? "responses" : "native");
    setBaseUrl(
      value === "openai" ? "https://api.openai.com/v1" : value === "anthropic" ? "https://api.anthropic.com" : "",
    );
    setApiKey("");
    setRemoveKey(false);
    if (!model) setStructured(value === "openai");
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      onSaved(
        await saveModel(
          {
            name,
            provider,
            model: modelId,
            api_format: format,
            base_url: provider === "bedrock" ? null : baseUrl,
            aws_region: provider === "bedrock" ? region : null,
            enabled,
            ...(embeddings ? {} : { capabilities: { supports_structured_output: structured } }),
            ...(model ? { revision: model.revision } : {}),
            ...(embeddings ? { embedding_dimensions: dimensions, embedding_version: version } : {}),
            ...(apiKey ? { api_key: apiKey } : removeKey ? { api_key: null } : {}),
          },
          model?.id,
        ),
      );
    } catch (error) {
      setError(
        error instanceof ApiError ? error.message : "Could not save the model. Your changes are still in the form.",
      );
    } finally {
      setSaving(false);
    }
  }
  return (
    <Sheet open onOpenChange={(next) => !next && close()}>
      <LeaveGuard dirty={dirty} />
      {guard.dialog}
      <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>{model ? `Edit ${model.name}` : "New model"}</SheetTitle>
          <SheetDescription>Save and test a model connection for agents or memory embeddings.</SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-col gap-6">
          <div>
            {error && (
              <Alert variant="destructive" className="mb-4">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
            <FieldSet disabled={saving}>
              <FieldGroup>
                <Field>
                  <FieldLabel htmlFor="llm-name">Connection name</FieldLabel>
                  <Input
                    id="llm-name"
                    required
                    maxLength={100}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="Production search"
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor="llm-type">Model type</FieldLabel>
                  <Select
                    value={embeddings ? "embeddings" : "chat"}
                    disabled={saving || model !== null}
                    onValueChange={(value) => {
                      changeProvider("openai");
                      setFormat(value === "embeddings" ? "embeddings" : "responses");
                    }}
                  >
                    <SelectTrigger id="llm-type">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectGroup>
                        <SelectItem value="chat">Chat / agents</SelectItem>
                        <SelectItem value="embeddings">Embeddings</SelectItem>
                      </SelectGroup>
                    </SelectContent>
                  </Select>
                  <FieldDescription>
                    {model
                      ? "Create a separate connection to use a different model type."
                      : "Chat models answer questions; embedding models embed memory entries for memory search."}
                  </FieldDescription>
                </Field>
                <Field>
                  <FieldLabel htmlFor="llm-provider">Provider</FieldLabel>
                  <Select
                    value={provider}
                    disabled={saving}
                    onValueChange={(value) => changeProvider(value as Provider)}
                  >
                    <SelectTrigger id="llm-provider">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectGroup>
                        {Object.entries(providerLabels)
                          .filter(([value]) => !embeddings || value !== "anthropic")
                          .map(([value, label]) => (
                            <SelectItem key={value} value={value}>
                              {label}
                            </SelectItem>
                          ))}
                      </SelectGroup>
                    </SelectContent>
                  </Select>
                </Field>
                <Field>
                  <FieldLabel htmlFor="llm-model">Model ID</FieldLabel>
                  <Input
                    id="llm-model"
                    required
                    maxLength={500}
                    value={modelId}
                    onChange={(e) => setModelId(e.target.value)}
                    placeholder={provider === "bedrock" ? "Model or inference profile ID" : "Exact provider model ID"}
                  />
                  <FieldDescription>
                    {embeddings
                      ? provider === "bedrock"
                        ? "Use amazon.titan-embed-text-v2:0 (Titan Text Embeddings V2)."
                        : "Use an embedding model served by an OpenAI-compatible /embeddings endpoint."
                      : provider === "bedrock"
                        ? "Use a model or inference profile available in your AWS region. The source-code agent requires an Anthropic Claude model."
                        : "The model must support tool calling for agent use."}
                  </FieldDescription>
                </Field>
                {provider === "bedrock" ? (
                  <Field>
                    <FieldLabel htmlFor="llm-region">AWS region</FieldLabel>
                    <Input
                      id="llm-region"
                      required
                      maxLength={60}
                      value={region}
                      onChange={(e) => setRegion(e.target.value)}
                      placeholder="eu-central-1"
                    />
                  </Field>
                ) : (
                  <Field>
                    <FieldLabel htmlFor="llm-url">Base URL</FieldLabel>
                    <Input
                      id="llm-url"
                      type="url"
                      required
                      maxLength={2000}
                      value={baseUrl}
                      onChange={(e) => setBaseUrl(e.target.value)}
                    />
                    <FieldDescription>
                      {provider === "openai"
                        ? "Include the API prefix, usually /v1. Local OpenAI-compatible servers are supported."
                        : "Anthropic API origin, without /v1/messages."}
                    </FieldDescription>
                  </Field>
                )}
                {embeddings && (
                  <div className="grid gap-5 sm:grid-cols-2">
                    <Field>
                      <FieldLabel htmlFor="llm-dimensions">Embedding dimensions</FieldLabel>
                      <Input
                        id="llm-dimensions"
                        required
                        type="number"
                        min={1}
                        max={16000}
                        value={dimensions}
                        onChange={(e) => setDimensions(Number(e.target.value))}
                      />
                      <FieldDescription>
                        {provider === "bedrock"
                          ? "Titan V2 supports 256, 512, or 1024 dimensions."
                          : "Must match the number of dimensions returned by this model."}
                      </FieldDescription>
                    </Field>
                    <Field>
                      <FieldLabel htmlFor="llm-version">Embedding version</FieldLabel>
                      <Input
                        id="llm-version"
                        required
                        maxLength={200}
                        value={version}
                        onChange={(e) => setVersion(e.target.value)}
                      />
                      <FieldDescription>
                        Increment when replacing a model behind the same name. Memory search then compares only vectors
                        of the new version; re-embed entries on the Memory page.
                      </FieldDescription>
                    </Field>
                  </div>
                )}
                {provider === "openai" && !embeddings && (
                  <Field>
                    <FieldLabel htmlFor="llm-format">API format</FieldLabel>
                    <Select
                      value={format}
                      disabled={saving}
                      onValueChange={(value) => setFormat(value as ModelInput["api_format"])}
                    >
                      <SelectTrigger id="llm-format">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectGroup>
                          <SelectItem value="responses">Responses</SelectItem>
                          <SelectItem value="chat_completions">Chat Completions</SelectItem>
                        </SelectGroup>
                      </SelectContent>
                    </Select>
                    <FieldDescription>
                      The source-code agent uses Responses. Search supports both formats.
                    </FieldDescription>
                  </Field>
                )}
                <Field>
                  <FieldLabel htmlFor="llm-key">{provider === "bedrock" ? "Bedrock API key" : "API key"}</FieldLabel>
                  <Input
                    id="llm-key"
                    type="password"
                    autoComplete="new-password"
                    maxLength={8192}
                    value={apiKey}
                    disabled={removeKey || saving}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder={model?.has_api_key ? "Saved key — leave blank to keep" : "Enter API key"}
                  />
                  <FieldDescription>
                    {provider === "bedrock"
                      ? "Enter an AWS Bedrock API key (bearer token). IAM access key pairs are not used by this connection."
                      : "Encrypted in storage. Local OpenAI-compatible servers may use no key."}{" "}
                    {model?.has_api_key &&
                      "Changing provider, URL, or region requires replacing or removing the saved key."}
                  </FieldDescription>
                </Field>
                {model?.has_api_key && provider === "openai" && (
                  <Field orientation="horizontal">
                    <FieldContent>
                      <FieldLabel htmlFor="llm-remove-key">Remove saved key</FieldLabel>
                    </FieldContent>
                    <Switch
                      id="llm-remove-key"
                      checked={removeKey}
                      disabled={saving}
                      onCheckedChange={(value) => {
                        setRemoveKey(value);
                        if (value) setApiKey("");
                      }}
                    />
                  </Field>
                )}
                {!embeddings && (
                  <Field orientation="horizontal">
                    <FieldContent>
                      <FieldLabel htmlFor="llm-structured">Supports structured output</FieldLabel>
                      <FieldDescription>
                        The model accepts a JSON Schema as its answer format. Off: runs with an output schema get the
                        schema in their instructions instead; their answer is still checked.
                      </FieldDescription>
                    </FieldContent>
                    <Switch
                      id="llm-structured"
                      checked={structured}
                      disabled={saving}
                      onCheckedChange={setStructured}
                    />
                  </Field>
                )}
                <Field orientation="horizontal">
                  <FieldContent>
                    <FieldLabel htmlFor="llm-enabled">Enabled</FieldLabel>
                    <FieldDescription>Disabling stops new requests that use this connection.</FieldDescription>
                  </FieldContent>
                  <Switch id="llm-enabled" checked={enabled} disabled={saving} onCheckedChange={setEnabled} />
                </Field>
              </FieldGroup>
            </FieldSet>
          </div>
          <SheetFooter className="gap-2 sm:justify-start sm:space-x-0">
            <Button disabled={saving}>
              {saving && <Spinner data-icon="inline-start" />}
              {model ? "Save" : "Create model"}
            </Button>
            <Button type="button" variant="outline" disabled={saving} onClick={close}>
              Cancel
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
