import { useEffect, useState } from "react";
import { ragRequest, type RagConfig, type RagSettings } from "./api";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

export default function RagSettingsView() {
  const [config, setConfig] = useState<RagConfig | null>(null);
  const [settings, setSettings] = useState<RagSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    ragRequest<RagConfig>("settings", "GET", undefined, controller.signal)
      .then((row) => {
        if (controller.signal.aborted) return;
        setConfig(row);
        setSettings(row.settings);
        setError("");
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, [reload]);
  const dirty = JSON.stringify(settings) !== JSON.stringify(config?.settings);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : "RAG request failed");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="flex flex-col gap-5">
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {notice && (
        <Alert>
          <AlertDescription role="status">{notice}</AlertDescription>
        </Alert>
      )}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">File size settings take effect without restarting Docker.</p>
        <Button variant="outline" disabled={busy} onClick={() => setReload((v) => v + 1)}>
          Reload settings
        </Button>
      </div>
      {!settings && !error && <Skeleton className="h-64" />}
      {settings && config && (
        <form
          className="flex max-w-3xl flex-col gap-8"
          onSubmit={(e) => {
            e.preventDefault();
            run(async () => {
              const row = await ragRequest<RagConfig>("settings", "PUT", {
                revision: config.revision,
                settings,
              });
              setConfig(row);
              setSettings(row.settings);
              setNotice("RAG settings saved. Refresh affected collections to apply changes.");
            });
          }}
        >
          <FieldSet className="rounded-lg border p-5">
            <FieldLegend>Data folder</FieldLegend>
            <FieldDescription>
              Collections connect folders inside DATA_ROOT. The host folder and the Jira folder are set in the
              deployment's .env.
            </FieldDescription>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Host folder</TableHead>
                  <TableHead>In container</TableHead>
                  <TableHead>Jira exports</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow>
                  <TableCell>
                    {config.data_root.host_root ? (
                      <code className="break-all">{config.data_root.host_root}</code>
                    ) : (
                      "Not provided"
                    )}
                  </TableCell>
                  <TableCell>
                    {config.data_root.container_root ? (
                      <code className="break-all">{config.data_root.container_root}</code>
                    ) : (
                      "Not mounted"
                    )}
                  </TableCell>
                  <TableCell>{config.data_root.jira_folder ?? "Not configured"}</TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </FieldSet>
          <FieldSet disabled={busy} className="rounded-lg border p-5">
            <FieldLegend>File processing</FieldLegend>
            <FieldGroup>
              <Field className="max-w-xs">
                <FieldLabel htmlFor="max_file_bytes">Maximum file size (bytes)</FieldLabel>
                <Input
                  id="max_file_bytes"
                  required
                  type="number"
                  min={100}
                  max={10000000}
                  value={settings.max_file_bytes}
                  onChange={(e) => setSettings({ max_file_bytes: Number(e.target.value) })}
                />
                <FieldDescription>
                  Larger files are skipped. Chunking, patterns and embeddings are set per collection.
                </FieldDescription>
              </Field>
            </FieldGroup>
          </FieldSet>
          <Button className="self-start" type="submit" disabled={busy || !dirty}>
            Save RAG settings
          </Button>
        </form>
      )}
    </div>
  );
}
