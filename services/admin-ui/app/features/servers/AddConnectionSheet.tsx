import { useState, type FormEvent } from "react";
import { ApiError } from "~/lib/api";
import { createItem, deploymentLabels, draftsUsed, isStale, updateItem, type Connection } from "./inventory";
import { DraftsUsed } from "./ConfirmDraftSheet";
import type { ServiceAsset } from "./services";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldGroup, FieldLabel, FieldSet } from "~/components/ui/field";
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
import { Textarea } from "~/components/ui/textarea";

/** The picker's value for a connection's target: "deployment:<id>", "service:<id>", NEW_SERVICE or "host". */
const NEW_SERVICE = "new-service";
const targetOf = (connection: Connection) =>
  connection.to_deployment_id
    ? `deployment:${connection.to_deployment_id}`
    : connection.to_service_id
      ? `service:${connection.to_service_id}`
      : "host";

/** Adds a connection from one of the `from` deployments (a choice when there are several), or edits `connection`.
 * The target is a deployment (grouped by environment and server), an external service (a recorded one, or a new one:
 * name, address, description, created first), or a host; then port, protocol and description. A stale revision on an
 * edit is `onConflict`. */
export default function AddConnectionSheet({
  from,
  services,
  connection,
  onSaved,
  onConflict,
  onClose,
}: {
  from: string[];
  services: ServiceAsset[];
  connection?: Connection;
  onSaved: () => void;
  onConflict: () => void;
  onClose: () => void;
}) {
  const [fromId, setFromId] = useState(connection?.from_deployment_id ?? (from.length === 1 ? from[0] : ""));
  const [target, setTarget] = useState(connection ? targetOf(connection) : "");
  const [host, setHost] = useState(connection?.to_host ?? "");
  const [port, setPort] = useState(connection?.port?.toString() ?? "");
  const [protocol, setProtocol] = useState(connection?.protocol ?? "tcp");
  const [description, setDescription] = useState(connection?.description ?? "");
  const [serviceName, setServiceName] = useState("");
  const [address, setAddress] = useState("");
  const [serviceDescription, setServiceDescription] = useState("");
  // A new external service created by an earlier, partly failed save: saving again only adds the connection.
  const [created, setCreated] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const labels = deploymentLabels(services);
  const groups = new Map<string, { value: string; label: string }[]>();
  for (const service of services)
    for (const deployment of service.deployments) {
      if (deployment.id === fromId) continue;
      const group = `${deployment.environment} / ${deployment.server.name}`;
      const label =
        deployment.runtime_name && deployment.runtime_name !== service.name
          ? `${service.name} (${deployment.runtime_name})`
          : service.name;
      groups.set(group, [...(groups.get(group) ?? []), { value: `deployment:${deployment.id}`, label }]);
    }
  const byLabel = (a: { label: string }, b: { label: string }) => a.label.localeCompare(b.label);
  const external = services
    .filter((service) => service.service_type === "external")
    .map((service) => ({
      value: `service:${service.id}`,
      label: service.address ? `${service.name} · ${service.address}` : service.name,
    }))
    .sort(byLabel);

  const [kind, id] = [target.slice(0, target.indexOf(":")), target.slice(target.indexOf(":") + 1)];
  // A person's saved connection is confirmed (an edited draft stays a draft): the drafts it uses are confirmed with it.
  const uses =
    connection?.status === "draft"
      ? []
      : draftsUsed(services, {
          deployments: [fromId, kind === "deployment" ? id : null],
          services: [kind === "service" ? id : null],
        });

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      let serviceId = kind === "service" ? id : null;
      if (target === NEW_SERVICE) {
        serviceId = created;
        if (!serviceId) {
          serviceId = (
            await createItem<{ id: string }>("services", {
              name: serviceName.trim(),
              service_type: "external",
              address: address.trim(),
              description: serviceDescription,
            })
          ).id;
          setCreated(serviceId);
        }
      }
      const value = {
        to_deployment_id: kind === "deployment" ? id : null,
        to_service_id: serviceId,
        to_host: target === "host" ? host.trim() : null,
        port: port === "" ? null : Number(port),
        protocol: protocol.trim() || "tcp",
        description,
      };
      if (connection) await updateItem("connections", connection.id, connection.revision, value);
      else await createItem("connections", { from_deployment_id: fromId, ...value });
      onSaved();
    } catch (e) {
      if (connection && isStale(e)) onConflict();
      else setError(e instanceof ApiError ? e.message : "Could not save the connection. Try again.");
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
          <SheetTitle>{connection ? "Edit connection" : "Add connection"}</SheetTitle>
          <SheetDescription>
            {fromId ? `From ${labels.get(fromId)?.label ?? "a deployment"}.` : "Choose the deployment it starts from."}
          </SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-col gap-6 p-4">
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <FieldSet disabled={saving}>
            <FieldGroup>
              {!connection && from.length > 1 && (
                <Field>
                  <FieldLabel htmlFor="connection-from">From</FieldLabel>
                  <Select value={fromId} onValueChange={setFromId}>
                    <SelectTrigger id="connection-from" className="w-full">
                      <SelectValue placeholder="Choose a deployment" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectGroup>
                        {from.map((id) => (
                          <SelectItem key={id} value={id}>
                            {labels.get(id)?.label ?? id}
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    </SelectContent>
                  </Select>
                </Field>
              )}
              <Field>
                <FieldLabel htmlFor="connection-target">Target</FieldLabel>
                <Select value={target} onValueChange={setTarget}>
                  <SelectTrigger id="connection-target" className="w-full">
                    <SelectValue placeholder="Choose what it connects to" />
                  </SelectTrigger>
                  <SelectContent>
                    {[...groups.keys()].sort().map((group) => (
                      <SelectGroup key={group}>
                        <SelectLabel>{group}</SelectLabel>
                        {groups
                          .get(group)!
                          .sort(byLabel)
                          .map((item) => (
                            <SelectItem key={item.value} value={item.value}>
                              {item.label}
                            </SelectItem>
                          ))}
                      </SelectGroup>
                    ))}
                    <SelectGroup>
                      <SelectLabel>External services</SelectLabel>
                      {external.map((item) => (
                        <SelectItem key={item.value} value={item.value}>
                          {item.label}
                        </SelectItem>
                      ))}
                      <SelectItem value={NEW_SERVICE}>New external service</SelectItem>
                    </SelectGroup>
                    <SelectGroup>
                      <SelectLabel>Other</SelectLabel>
                      <SelectItem value="host">A host</SelectItem>
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </Field>
              {target === NEW_SERVICE && (
                <>
                  <Field>
                    <FieldLabel htmlFor="connection-service-name">Name</FieldLabel>
                    <Input
                      id="connection-service-name"
                      required
                      maxLength={200}
                      value={serviceName}
                      onChange={(event) => setServiceName(event.target.value)}
                      placeholder="stripe"
                    />
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="connection-service-address">Address</FieldLabel>
                    <Input
                      id="connection-service-address"
                      maxLength={253}
                      value={address}
                      onChange={(event) => setAddress(event.target.value)}
                      placeholder="api.stripe.com or 10.0.0.5"
                    />
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="connection-service-description">Service description</FieldLabel>
                    <Textarea
                      id="connection-service-description"
                      maxLength={4000}
                      rows={2}
                      value={serviceDescription}
                      onChange={(event) => setServiceDescription(event.target.value)}
                    />
                  </Field>
                </>
              )}
              {target === "host" && (
                <Field>
                  <FieldLabel htmlFor="connection-host">Host</FieldLabel>
                  <Input
                    id="connection-host"
                    required
                    maxLength={253}
                    value={host}
                    onChange={(event) => setHost(event.target.value)}
                    placeholder="smtp.example.com or 10.0.0.5"
                  />
                </Field>
              )}
              <Field>
                <FieldLabel htmlFor="connection-port">Port</FieldLabel>
                <Input
                  id="connection-port"
                  type="number"
                  min={1}
                  max={65535}
                  value={port}
                  onChange={(event) => setPort(event.target.value)}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="connection-protocol">Protocol</FieldLabel>
                <Input
                  id="connection-protocol"
                  maxLength={20}
                  value={protocol}
                  onChange={(event) => setProtocol(event.target.value)}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="connection-description">Description</FieldLabel>
                <Textarea
                  id="connection-description"
                  maxLength={4000}
                  rows={2}
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                />
              </Field>
            </FieldGroup>
          </FieldSet>
          <DraftsUsed names={uses} />
          <div className="flex gap-2">
            <Button disabled={saving || !fromId || !target}>
              {saving && <Spinner data-icon="inline-start" />}
              {connection ? "Save connection" : "Add connection"}
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
