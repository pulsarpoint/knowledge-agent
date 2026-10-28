import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Link } from "react-router";
import { ApiError } from "~/lib/api";
import { getLlmSettings, type ModelProfile } from "~/features/llm/api";
import { getMemorySettings, saveMemorySettings } from "./api";
import { Label } from "~/components/ui/label";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "~/components/ui/select";

/** The embedding model memory search and saving use: one inline control (label beside the select) that sits in a
 * toolbar row; the note on where connections are managed is at the foot of the list. A change is saved at once;
 * `onChange` lets the page reload. */
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
    <div className="flex items-center gap-2">
      <Label htmlFor="memory-embedding-model" className="whitespace-nowrap text-muted-foreground">
        Embedding model
      </Label>
      <Select value={modelId ?? "none"} onValueChange={(value) => void choose(value)}>
        <SelectTrigger id="memory-embedding-model" className="w-48">
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
          <SelectSeparator />
          <p className="px-2 py-1.5 text-xs text-muted-foreground">
            Connections are managed in{" "}
            <Link className="underline underline-offset-4" to="/llm-settings">
              LLM Settings
            </Link>
            .
          </p>
        </SelectContent>
      </Select>
    </div>
  );
}
