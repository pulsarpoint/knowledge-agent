import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Link } from "react-router";
import { ApiError } from "~/lib/api";
import { getLlmSettings, type ModelProfile } from "~/features/llm/api";
import { getMemorySettings, saveMemorySettings } from "./api";
import { Field, FieldDescription, FieldLabel } from "~/components/ui/field";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";

/** The embedding model memory search and saving use. A change is saved at once; `onChange` lets the page reload. */
export default function EmbeddingModelSelect({
  onChange,
  onError,
}: {
  onChange: () => void;
  onError: (message: string) => void;
}) {
  const [models, setModels] = useState<ModelProfile[]>([]);
  const [modelId, setModelId] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([getMemorySettings(controller.signal), getLlmSettings()])
      .then(([settings, llm]) => {
        if (controller.signal.aborted) return;
        setModelId(settings.embedding_model_id);
        setModels(llm.models.filter((model) => model.api_format === "embeddings"));
      })
      .catch(() => {});
    return () => controller.abort();
  }, []);

  async function choose(value: string) {
    try {
      const saved = await saveMemorySettings(value === "none" ? null : value);
      setModelId(saved.embedding_model_id);
      toast.success("Embedding model saved.");
      onChange();
    } catch (reason) {
      onError(reason instanceof ApiError ? reason.message : "Could not save the embedding model.");
    }
  }

  return (
    <Field className="w-full sm:w-64">
      <FieldLabel htmlFor="memory-embedding-model">Embedding model</FieldLabel>
      <Select value={modelId ?? "none"} onValueChange={(value) => void choose(value)}>
        <SelectTrigger id="memory-embedding-model" className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectItem value="none">Not configured</SelectItem>
            {models.map((model) => (
              <SelectItem key={model.id} value={model.id} disabled={!model.enabled}>
                {model.name}
                {!model.enabled && " (disabled)"}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
      <FieldDescription>
        Connections are managed in{" "}
        <Link className="underline underline-offset-4" to="/llm-settings">
          LLM Settings
        </Link>
        .
      </FieldDescription>
    </Field>
  );
}
