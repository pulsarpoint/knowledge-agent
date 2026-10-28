import { useEffect, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import type { ColumnDef } from "@tanstack/react-table";
import { Cpu, Pencil, Plug, Plus } from "lucide-react";
import { ApiError } from "~/lib/api";
import { getLlmSettings, providerLabels, testModel, type LlmSettings, type ModelProfile } from "./api";
import LlmModelForm from "./LlmModelForm";
import DataTable, { sortHeader, sortRows, TableEmpty, type Sort } from "~/components/DataTable";
import RowMenu from "~/components/RowMenu";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Spinner } from "~/components/ui/spinner";

type ModelSort = "name" | "type" | "status";
const byName = (a: ModelProfile, b: ModelProfile) => a.name.localeCompare(b.name);
const MODEL_ORDER: Record<ModelSort, (a: ModelProfile, b: ModelProfile) => number> = {
  name: byName,
  type: (a, b) => a.api_format.localeCompare(b.api_format) || byName(a, b),
  status: (a, b) => Number(b.enabled) - Number(a.enabled) || byName(a, b),
};

/** LLM settings: the model connections in a table (name, type, provider and model, credential, Enabled / Disabled;
 * Test connection, and Edit in the row menu) with search and sortable headers; New model and Edit open a right Sheet. */
export default function LlmSettingsView() {
  const [settings, setSettings] = useState<LlmSettings | null>(null);
  const [editing, setEditing] = useState<ModelProfile | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<Sort<ModelSort>>({ key: "name", descending: false });

  async function refresh() {
    setBusy(true);
    setError(null);
    try {
      const value = await getLlmSettings();
      setSettings(value);
    } catch (error) {
      setError(error instanceof ApiError ? error.message : "Could not load model settings.");
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    void refresh();
  }, []);
  async function test(model: ModelProfile) {
    setTesting(model.id);
    setError(null);
    try {
      const result = await testModel(model);
      if (result.ok) toast.success(`${model.name}: ${result.message}`);
      else setError(`${model.name}: ${result.message}`);
    } catch (error) {
      setError(error instanceof ApiError ? error.message : "Could not test this model.");
    } finally {
      setTesting(null);
    }
  }
  function saved(model: ModelProfile) {
    setSettings(
      (value) =>
        value && {
          ...value,
          models: [...value.models.filter((row) => row.id !== model.id), model].sort((a, b) =>
            a.name.localeCompare(b.name),
          ),
        },
    );
    setEditing(undefined);
    toast.success(`Model “${model.name}” saved.`);
  }
  const text = search.trim().toLowerCase();
  const shown = sortRows(
    (settings?.models ?? []).filter((model) =>
      `${model.name}\n${providerLabels[model.provider]}\n${model.model}`.toLowerCase().includes(text),
    ),
    sort,
    MODEL_ORDER,
  );
  const newModel = (
    <Button disabled={editing !== undefined || !settings} onClick={() => setEditing(null)}>
      <Plus data-icon="inline-start" />
      New model
    </Button>
  );
  const columns: ColumnDef<ModelProfile, unknown>[] = [
    {
      id: "name",
      header: () => sortHeader("Name", "name", sort, setSort),
      cell: ({ row: { original: model } }) => <span className="font-medium">{model.name}</span>,
    },
    {
      id: "type",
      header: () => sortHeader("Type", "type", sort, setSort),
      cell: ({ row: { original: model } }) => (
        <Badge variant="outline">{model.api_format === "embeddings" ? "Embeddings" : "Chat"}</Badge>
      ),
    },
    {
      id: "provider",
      header: "Provider / model",
      cell: ({ row: { original: model } }) => (
        <>
          <div>{providerLabels[model.provider]}</div>
          <div className="max-w-md break-all text-xs text-muted-foreground">{model.model}</div>
          {model.api_format === "embeddings" && (
            <div className="text-xs text-muted-foreground">
              {model.embedding_dimensions} dimensions · Version {model.embedding_version}
            </div>
          )}
          {model.aws_region && <div className="text-xs text-muted-foreground">{model.aws_region}</div>}
        </>
      ),
    },
    {
      id: "credential",
      header: "Credential",
      cell: ({ row: { original: model } }) => (model.has_api_key ? "Key saved" : "No key"),
    },
    {
      id: "status",
      header: () => sortHeader("Status", "status", sort, setSort),
      cell: ({ row: { original: model } }) => (
        <Badge variant={model.enabled ? "secondary" : "outline"}>{model.enabled ? "Enabled" : "Disabled"}</Badge>
      ),
    },
    {
      id: "actions",
      header: () => <span className="sr-only">Actions</span>,
      cell: ({ row: { original: model } }) => (
        <div className="flex items-center justify-end gap-1">
          <Button
            size="sm"
            variant="ghost"
            aria-label={`Test connection for ${model.name}`}
            disabled={!model.enabled || testing !== null || editing !== undefined}
            onClick={() => void test(model)}
          >
            {testing === model.id ? <Spinner data-icon="inline-start" /> : <Plug data-icon="inline-start" />}
            Test connection
          </Button>
          <RowMenu
            name={model.name}
            disabled={editing !== undefined || testing !== null}
            items={[{ label: "Edit", icon: Pencil, onSelect: () => setEditing(model) }]}
          />
        </div>
      ),
    },
  ];
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          Manage model connections and credentials. Select models for each agent on the{" "}
          <Link className="underline underline-offset-4" to="/agents">
            Agents page
          </Link>{" "}
          and select the memory embedding model on the{" "}
          <Link className="underline underline-offset-4" to="/memory">
            Memory page
          </Link>
          . Test connection sends a small chat or embedding request and may incur provider usage.
        </p>
        {newModel}
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {editing !== undefined && (
        <LlmModelForm
          key={editing?.id ?? "new"}
          model={editing}
          onSaved={saved}
          onClose={() => setEditing(undefined)}
        />
      )}
      <DataTable
        columns={columns}
        data={shown}
        loading={!settings && busy}
        rowId={(model) => model.id}
        search={{ plural: "models", value: search, onChange: setSearch }}
        sort={sort}
        resetKey={`${sort.key}\n${sort.descending}`}
        empty={
          settings?.models.length === 0 ? (
            <TableEmpty
              icon={Cpu}
              title="No models yet"
              description="Add a connection for agents or memory embeddings."
            >
              {newModel}
            </TableEmpty>
          ) : settings ? (
            "No models match."
          ) : (
            "Could not load models."
          )
        }
      />
    </div>
  );
}
