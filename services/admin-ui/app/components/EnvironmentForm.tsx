import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { X } from "lucide-react";
import { ApiError } from "../lib/api";
import { saveEnvironment, type EnvironmentAsset } from "../lib/environments";
import { type ServiceAsset } from "../lib/services";
import { type ServerAsset } from "../lib/servers";
import { serviceTypes, serviceRoles } from "../lib/serviceClassification";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import { Field, FieldContent, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Spinner } from "~/components/ui/spinner";
import { Textarea } from "~/components/ui/textarea";

export default function EnvironmentForm({ environment, services, servers, onSaved, onClose }: {
  environment: EnvironmentAsset | null; services: ServiceAsset[]; servers: ServerAsset[];
  onSaved: (environment: EnvironmentAsset) => void; onClose: () => void;
}) {
  const [name, setName] = useState(environment?.name ?? "");
  const [description, setDescription] = useState(environment?.description ?? "");
  const [serviceIds, setServiceIds] = useState(environment?.services.map(service => service.id) ?? []);
  const [serverIds, setServerIds] = useState(environment?.servers.map(server => server.id) ?? []);
  const [serviceQuery, setServiceQuery] = useState("");
  const [serverQuery, setServerQuery] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const matchingServices = services.filter(service => `${service.name} ${service.description} ${serviceRoles[service.service_role].label}`.toLowerCase().includes(serviceQuery.trim().toLowerCase()));
  const matchingServers = servers.filter(server => `${server.name} ${server.hostname} ${server.description}`.toLowerCase().includes(serverQuery.trim().toLowerCase()));

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (saving) return;
    setSaving(true); setError(null);
    try {
      const saved = await saveEnvironment({ name, description, service_ids: serviceIds, server_ids: serverIds,
        ...(environment ? { revision: environment.revision } : {}) }, environment?.id);
      onSaved(saved);
    } catch (error) {
      setError(error instanceof ApiError ? error.message : "Could not save the environment. Try again.");
    } finally { setSaving(false); }
  }

  return <form onSubmit={submit} className="flex flex-col gap-6 rounded-lg border p-6">
    <div className="flex items-center justify-between gap-3"><h2 className="text-lg font-semibold">{environment ? "Edit environment" : "Add environment"}</h2>
      <Button type="button" variant="ghost" size="icon" aria-label="Close environment form" disabled={saving} onClick={onClose}><X /></Button></div>
    {error && <Alert variant="destructive"><AlertTitle>Environment could not be saved</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
    <FieldSet disabled={saving}><FieldGroup>
      <Field><FieldLabel htmlFor="environment-name">Environment name</FieldLabel><Input id="environment-name" required maxLength={100} value={name} onChange={event => setName(event.target.value)} placeholder="Development, Staging, Production…" /></Field>
      <Field><FieldLabel htmlFor="environment-description">Description (optional)</FieldLabel><Textarea id="environment-description" maxLength={4000} rows={3} value={description} onChange={event => setDescription(event.target.value)} placeholder="What this environment is used for and any useful context." /></Field>
    </FieldGroup></FieldSet>
    <p className="text-sm text-muted-foreground">Select the services and servers that belong to this environment. Each asset can belong to multiple environments.</p>
    <FieldGroup className="grid gap-6 md:grid-cols-2">
      <FieldSet disabled={saving}><FieldLegend>Services</FieldLegend><FieldDescription>{serviceIds.length} selected</FieldDescription>
        {services.length === 0 ? <FieldDescription>Add services on the <Link className="underline" to="/services">Services page</Link>, then select them here.</FieldDescription> : <>
          <Input aria-label="Filter environment services" placeholder="Find a service…" value={serviceQuery} onChange={event => setServiceQuery(event.target.value)} />
          <FieldGroup className="max-h-80 gap-4 overflow-y-auto p-1">
            {matchingServices.map(service => <Field key={service.id} orientation="horizontal" data-disabled={saving}>
              <Checkbox id={`environment-service-${service.id}`} disabled={saving} checked={serviceIds.includes(service.id)} onCheckedChange={checked => setServiceIds(ids => checked === true ? [...ids, service.id] : ids.filter(id => id !== service.id))} />
              <FieldContent><FieldLabel htmlFor={`environment-service-${service.id}`}>{service.name}</FieldLabel><FieldDescription>{serviceTypes[service.service_type]} · {serviceRoles[service.service_role].label}</FieldDescription></FieldContent>
            </Field>)}
            {matchingServices.length === 0 && <FieldDescription>No services match. Your selections are kept.</FieldDescription>}
          </FieldGroup>
        </>}
      </FieldSet>
      <FieldSet disabled={saving}><FieldLegend>Servers</FieldLegend><FieldDescription>{serverIds.length} selected</FieldDescription>
        {servers.length === 0 ? <FieldDescription>Add servers on the <Link className="underline" to="/servers">Servers page</Link>, then select them here.</FieldDescription> : <>
          <Input aria-label="Filter environment servers" placeholder="Find a server or hostname…" value={serverQuery} onChange={event => setServerQuery(event.target.value)} />
          <FieldGroup className="max-h-80 gap-4 overflow-y-auto p-1">
            {matchingServers.map(server => <Field key={server.id} orientation="horizontal" data-disabled={saving}>
              <Checkbox id={`environment-server-${server.id}`} disabled={saving} checked={serverIds.includes(server.id)} onCheckedChange={checked => setServerIds(ids => checked === true ? [...ids, server.id] : ids.filter(id => id !== server.id))} />
              <FieldContent><FieldLabel htmlFor={`environment-server-${server.id}`}>{server.name}</FieldLabel><FieldDescription>{server.hostname}</FieldDescription></FieldContent>
            </Field>)}
            {matchingServers.length === 0 && <FieldDescription>No servers match. Your selections are kept.</FieldDescription>}
          </FieldGroup>
        </>}
      </FieldSet>
    </FieldGroup>
    <div className="flex gap-2"><Button type="submit" disabled={saving}>{saving && <Spinner data-icon="inline-start" />}{environment ? "Save changes" : "Save environment"}</Button>
      <Button type="button" variant="outline" disabled={saving} onClick={onClose}>Cancel</Button></div>
  </form>;
}
