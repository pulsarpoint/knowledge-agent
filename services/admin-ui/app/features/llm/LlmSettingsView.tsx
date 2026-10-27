import { useEffect, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { Pencil, Plug, Plus, RefreshCw } from "lucide-react";
import { ApiError } from "~/lib/api";
import { getLlmSettings, providerLabels, testModel, type LlmSettings, type ModelProfile } from "./api";
import LlmModelForm from "./LlmModelForm";
import RowMenu from "~/components/RowMenu";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "~/components/ui/card";
import { Spinner } from "~/components/ui/spinner";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

export default function LlmSettingsView() {
  const [settings, setSettings] = useState<LlmSettings | null>(null);
  const [editing, setEditing] = useState<ModelProfile | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

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
          .
        </p>
        <div className="flex gap-2">
          <Button
            variant="outline"
            disabled={busy || editing !== undefined || testing !== null}
            onClick={() => void refresh()}
          >
            <RefreshCw data-icon="inline-start" />
            Refresh
          </Button>
          <Button disabled={editing !== undefined || !settings} onClick={() => setEditing(null)}>
            <Plus data-icon="inline-start" />
            New model
          </Button>
        </div>
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
      {!settings && busy && (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Spinner />
          Loading model settings…
        </div>
      )}
      {settings && (
        <Card>
          <CardHeader>
            <CardTitle>Model connections</CardTitle>
            <CardDescription>
              Test connection sends a small chat or embedding request to the saved model and may incur provider usage.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {settings.models.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                No models configured. Add a connection for agents or memory embeddings.
              </p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Provider / model</TableHead>
                    <TableHead>Credential</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {settings.models.map((model) => (
                    <TableRow key={model.id}>
                      <TableCell className="font-medium">{model.name}</TableCell>
                      <TableCell>
                        <Badge variant="outline">{model.api_format === "embeddings" ? "Embeddings" : "Chat"}</Badge>
                      </TableCell>
                      <TableCell>
                        <div>{providerLabels[model.provider]}</div>
                        <div className="max-w-md break-all text-xs text-muted-foreground">{model.model}</div>
                        {model.api_format === "embeddings" && (
                          <div className="text-xs text-muted-foreground">
                            {model.embedding_dimensions} dimensions · Version {model.embedding_version}
                          </div>
                        )}
                        {model.aws_region && <div className="text-xs text-muted-foreground">{model.aws_region}</div>}
                      </TableCell>
                      <TableCell>{model.has_api_key ? "Key saved" : "No key"}</TableCell>
                      <TableCell>
                        <Badge variant={model.enabled ? "secondary" : "outline"}>
                          {model.enabled ? "Enabled" : "Disabled"}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center justify-end gap-1">
                          <Button
                            size="sm"
                            variant="ghost"
                            aria-label={`Test ${model.name}`}
                            disabled={!model.enabled || testing !== null || editing !== undefined}
                            onClick={() => void test(model)}
                          >
                            {testing === model.id ? (
                              <Spinner data-icon="inline-start" />
                            ) : (
                              <Plug data-icon="inline-start" />
                            )}
                            Test connection
                          </Button>
                          <RowMenu
                            name={model.name}
                            disabled={editing !== undefined || testing !== null}
                            items={[{ label: "Edit", icon: Pencil, onSelect: () => setEditing(model) }]}
                          />
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
