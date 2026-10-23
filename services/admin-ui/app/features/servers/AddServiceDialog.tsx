import { useEffect, useState, type FormEvent } from "react";
import { ApiError } from "~/lib/api";
import { createItem, type Deployment } from "./inventory";
import { getServices, type ServiceAsset, type ServiceInput } from "./services";
import { serviceRoles, serviceTypes, type ServiceRole, type ServiceType } from "./serviceClassification";
import { DraftsUsed } from "./ConfirmDraftSheet";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Textarea } from "~/components/ui/textarea";

const NEW = "new";

/** Adds a service to the server in the environment: a recorded service or a new one (created first), and its
 * deployment there. `uses`: the names of the drafts the placement brings (the environment, the server, its
 * membership); saving confirms them and a chosen draft service. */
export default function AddServiceDialog({
  environmentId,
  server,
  uses = [],
  onAdded,
  onClose,
}: {
  environmentId: string;
  server: { id: string; name: string };
  uses?: string[];
  onAdded: (serviceId: string) => void;
  onClose: () => void;
}) {
  const [services, setServices] = useState<ServiceAsset[] | null>(null);
  const [choice, setChoice] = useState("");
  const [service, setService] = useState<ServiceInput>({
    name: "",
    service_type: "unclassified",
    service_role: "unclassified",
    owner: "",
    description: "",
  });
  const [runtime, setRuntime] = useState("");
  const [runtimeName, setRuntimeName] = useState("");
  const [port, setPort] = useState("");
  const [version, setVersion] = useState("");
  const [logLocation, setLogLocation] = useState("");
  // A new service created by an earlier, partly failed save: saving again only adds its deployment.
  const [created, setCreated] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getServices(controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setServices(value.services);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e instanceof ApiError ? e.message : "Could not load the services.");
      });
    return () => controller.abort();
  }, []);

  const recorded = [...(services ?? [])].sort((a, b) => a.name.localeCompare(b.name));
  const chosen = recorded.find((item) => item.id === choice);
  const drafts = [...uses, ...(chosen?.status === "draft" ? [chosen.name] : [])];
  const change = (field: keyof ServiceInput) => (value: string) => setService((old) => ({ ...old, [field]: value }));

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      let serviceId = choice === NEW ? created : choice;
      if (!serviceId) {
        const { address, ...fields } = service;
        const value = fields.service_type === "external" ? { ...fields, address: address?.trim() ?? "" } : fields;
        serviceId = (await createItem<ServiceAsset>("services", value)).id;
        setCreated(serviceId);
      }
      await createItem<Deployment>("deployments", {
        service_id: serviceId,
        server_id: server.id,
        environment_id: environmentId,
        runtime,
        runtime_name: runtimeName,
        port: port === "" ? null : Number(port),
        deployed_version: version,
        log_location: logLocation,
      });
      onAdded(serviceId);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not add the service. Try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Sheet
      open
      onOpenChange={(open) => {
        if (!open && !saving) onClose();
      }}
    >
      <SheetContent className="overflow-y-auto">
        <SheetHeader>
          <SheetTitle>Add service</SheetTitle>
          <SheetDescription>
            Deploy a recorded service or a new one on {server.name} in this environment.
          </SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-col gap-6 p-4">
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <FieldSet disabled={saving || !services}>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="add-service-choice">Service</FieldLabel>
                <Select value={choice} onValueChange={setChoice}>
                  <SelectTrigger id="add-service-choice" className="w-full">
                    <SelectValue placeholder="Choose a service" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {recorded.map((item) => (
                        <SelectItem key={item.id} value={item.id}>
                          {item.name}
                        </SelectItem>
                      ))}
                      <SelectItem value={NEW}>A new service</SelectItem>
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </Field>
              {choice === NEW && (
                <>
                  <Field>
                    <FieldLabel htmlFor="add-service-name">Name</FieldLabel>
                    <Input
                      id="add-service-name"
                      required
                      maxLength={200}
                      value={service.name}
                      onChange={(event) => change("name")(event.target.value)}
                    />
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="add-service-type">Type</FieldLabel>
                    <Select value={service.service_type} onValueChange={(value) => change("service_type")(value)}>
                      <SelectTrigger id="add-service-type" className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectGroup>
                          {(Object.keys(serviceTypes) as ServiceType[]).map((type) => (
                            <SelectItem key={type} value={type}>
                              {serviceTypes[type]}
                            </SelectItem>
                          ))}
                        </SelectGroup>
                      </SelectContent>
                    </Select>
                  </Field>
                  {service.service_type === "external" && (
                    <Field>
                      <FieldLabel htmlFor="add-service-address">Address</FieldLabel>
                      <Input
                        id="add-service-address"
                        maxLength={253}
                        value={service.address ?? ""}
                        onChange={(event) => change("address")(event.target.value)}
                        placeholder="api.stripe.com or 10.0.0.5"
                      />
                    </Field>
                  )}
                  <Field>
                    <FieldLabel htmlFor="add-service-role">Role</FieldLabel>
                    <Select value={service.service_role} onValueChange={(value) => change("service_role")(value)}>
                      <SelectTrigger id="add-service-role" className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectGroup>
                          {(Object.keys(serviceRoles) as ServiceRole[]).map((role) => (
                            <SelectItem key={role} value={role}>
                              {serviceRoles[role].label}
                            </SelectItem>
                          ))}
                        </SelectGroup>
                      </SelectContent>
                    </Select>
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="add-service-owner">Owner</FieldLabel>
                    <Input
                      id="add-service-owner"
                      maxLength={200}
                      value={service.owner}
                      onChange={(event) => change("owner")(event.target.value)}
                    />
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="add-service-description">Description</FieldLabel>
                    <Textarea
                      id="add-service-description"
                      maxLength={4000}
                      rows={3}
                      value={service.description}
                      onChange={(event) => change("description")(event.target.value)}
                    />
                  </Field>
                </>
              )}
            </FieldGroup>
          </FieldSet>
          <FieldSet disabled={saving || !services}>
            <FieldLegend>Deployment on {server.name}</FieldLegend>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="add-service-runtime">Runtime</FieldLabel>
                <Input
                  id="add-service-runtime"
                  maxLength={80}
                  value={runtime}
                  onChange={(event) => setRuntime(event.target.value)}
                  placeholder="docker, systemd"
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="add-service-runtime-name">Runtime name</FieldLabel>
                <Input
                  id="add-service-runtime-name"
                  maxLength={200}
                  value={runtimeName}
                  onChange={(event) => setRuntimeName(event.target.value)}
                  placeholder="Container or unit name"
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="add-service-port">Port</FieldLabel>
                <Input
                  id="add-service-port"
                  type="number"
                  min={1}
                  max={65535}
                  value={port}
                  onChange={(event) => setPort(event.target.value)}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="add-service-version">Version</FieldLabel>
                <Input
                  id="add-service-version"
                  maxLength={200}
                  value={version}
                  onChange={(event) => setVersion(event.target.value)}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="add-service-log">Log location</FieldLabel>
                <Input
                  id="add-service-log"
                  maxLength={1000}
                  value={logLocation}
                  onChange={(event) => setLogLocation(event.target.value)}
                />
              </Field>
            </FieldGroup>
          </FieldSet>
          <DraftsUsed names={drafts} />
          <div className="flex gap-2">
            <Button disabled={saving || !choice}>
              {saving && <Spinner data-icon="inline-start" />}
              Add service
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
