import { useEffect, useState, type FormEvent } from "react";
import { ApiError } from "~/lib/api";
import { getServers, type ServerAsset } from "./api";
import { getEnvironments, type EnvironmentAsset } from "./environments";
import { createItem } from "./inventory";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldSet } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";

const message = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);
const byName = <T extends { name: string }>(a: T, b: T) => a.name.localeCompare(b.name);

/** Adds a deployment of the service: an environment, a server (the environment's members first, then any server; a
 * server that is not a member is added to the environment with the deployment), runtime, runtime name, port, version
 * and log location. A refusal stays shown; a save closes the sheet through `onSaved`. */
export default function AddDeploymentSheet({
  serviceId,
  serviceName,
  onSaved,
  onClose,
}: {
  serviceId: string;
  serviceName: string;
  onSaved: () => void;
  onClose: () => void;
}) {
  const [environments, setEnvironments] = useState<EnvironmentAsset[] | null>(null);
  const [servers, setServers] = useState<ServerAsset[]>([]);
  const [environmentId, setEnvironmentId] = useState("");
  const [serverId, setServerId] = useState("");
  const [runtime, setRuntime] = useState("");
  const [runtimeName, setRuntimeName] = useState("");
  const [port, setPort] = useState("");
  const [version, setVersion] = useState("");
  const [logLocation, setLogLocation] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([getEnvironments(controller.signal), getServers(controller.signal)])
      .then(([{ environments }, { servers }]) => {
        if (controller.signal.aborted) return;
        setEnvironments([...environments].sort(byName));
        setServers([...servers].sort(byName));
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(message(e, "Could not load the environments and servers."));
      });
    return () => controller.abort();
  }, []);

  const environment = environments?.find((item) => item.id === environmentId);
  const memberIds = new Set(environment?.servers.map((item) => item.id) ?? []);
  const members = servers.filter((item) => memberIds.has(item.id));
  const others = servers.filter((item) => !memberIds.has(item.id));
  const joining = environment && serverId && !memberIds.has(serverId) ? servers.find((s) => s.id === serverId) : null;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await createItem("deployments", {
        service_id: serviceId,
        environment_id: environmentId,
        server_id: serverId,
        runtime,
        runtime_name: runtimeName,
        port: port === "" ? null : Number(port),
        deployed_version: version,
        log_location: logLocation,
      });
      onSaved();
    } catch (e) {
      setError(message(e, "Could not add the deployment. Try again."));
    } finally {
      setSaving(false);
    }
  }

  const text = (id: string, label: string, value: string, set: (value: string) => void, max: number) => (
    <Field>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <Input id={id} maxLength={max} value={value} onChange={(event) => set(event.target.value)} />
    </Field>
  );
  return (
    <Sheet
      open
      onOpenChange={(open) => {
        if (!open && !saving) onClose();
      }}
    >
      <SheetContent className="overflow-y-auto">
        <SheetHeader>
          <SheetTitle>Add deployment</SheetTitle>
          <SheetDescription>Where {serviceName} runs.</SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-col gap-6 p-4">
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <FieldSet disabled={saving || environments === null}>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="deployment-environment">Environment</FieldLabel>
                <Select value={environmentId} onValueChange={setEnvironmentId}>
                  <SelectTrigger id="deployment-environment" className="w-full">
                    <SelectValue placeholder="Choose an environment" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {(environments ?? []).map((item) => (
                        <SelectItem key={item.id} value={item.id}>
                          {item.name}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </Field>
              <Field>
                <FieldLabel htmlFor="deployment-server">Server</FieldLabel>
                <Select value={serverId} onValueChange={setServerId} disabled={!environment}>
                  <SelectTrigger id="deployment-server" className="w-full">
                    <SelectValue placeholder="Choose a server" />
                  </SelectTrigger>
                  <SelectContent>
                    {members.length > 0 && (
                      <SelectGroup>
                        <SelectLabel>{`In ${environment?.name}`}</SelectLabel>
                        {members.map((item) => (
                          <SelectItem key={item.id} value={item.id}>
                            {item.name}
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    )}
                    {others.length > 0 && (
                      <SelectGroup>
                        <SelectLabel>Other servers</SelectLabel>
                        {others.map((item) => (
                          <SelectItem key={item.id} value={item.id}>
                            {item.name}
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    )}
                  </SelectContent>
                </Select>
                {joining && environment && (
                  <FieldDescription>{`Saving adds ${joining.name} to ${environment.name}.`}</FieldDescription>
                )}
              </Field>
              {text("deployment-runtime", "Runtime", runtime, setRuntime, 80)}
              {text("deployment-runtime-name", "Runtime name", runtimeName, setRuntimeName, 200)}
              <Field>
                <FieldLabel htmlFor="deployment-port">Port</FieldLabel>
                <Input
                  id="deployment-port"
                  type="number"
                  min={1}
                  max={65535}
                  value={port}
                  onChange={(event) => setPort(event.target.value)}
                />
              </Field>
              {text("deployment-version", "Version", version, setVersion, 200)}
              {text("deployment-log", "Log location", logLocation, setLogLocation, 1000)}
            </FieldGroup>
          </FieldSet>
          <div className="flex gap-2">
            <Button disabled={saving || !environmentId || !serverId}>
              {saving && <Spinner data-icon="inline-start" />}
              Save
            </Button>
            <Button type="button" variant="outline" disabled={saving} onClick={onClose}>
              Cancel
            </Button>
          </div>
        </form>
      </SheetContent>
    </Sheet>
  );
}
