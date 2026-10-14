import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { ChevronDown, Plus, Server, Trash2, X } from "lucide-react";
import { ApiError } from "../lib/api";
import { saveService, type DeploymentInput, type ServiceAsset } from "../lib/services";
import { type EnvironmentAsset } from "../lib/environments";
import { type Repository } from "~/features/codeProjects/api";
import { type ServerAsset } from "../lib/servers";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Badge } from "~/components/ui/badge";
import { DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuGroup, DropdownMenuTrigger } from "~/components/ui/dropdown-menu";
import { Checkbox } from "~/components/ui/checkbox";
import { Field, FieldContent, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import ServerForm from "./ServerForm";
import ServiceDependencyFields from "./ServiceDependencyFields";
import { serviceTypes, serviceRoles, type ServiceType, type ServiceRole } from "../lib/serviceClassification";
import { Spinner } from "~/components/ui/spinner";
import { Textarea } from "~/components/ui/textarea";

export default function ServiceForm({ service, services, repositories, environments, servers: initialServers, onSaved, onClose }: {
  service: ServiceAsset | null; services: ServiceAsset[]; repositories: Repository[]; servers: ServerAsset[];
  environments: EnvironmentAsset[];
  onSaved: (id: string) => void; onClose: () => void;
}) {
  const [name, setName] = useState(service?.name ?? "");
  const [serviceType, setServiceType] = useState<ServiceType>(service?.service_type ?? "custom");
  const [serviceRole, setServiceRole] = useState<ServiceRole>(service?.service_role ?? "application");
  const [description, setDescription] = useState(service?.description ?? "");
  const [owner, setOwner] = useState(service?.owner ?? "");
  const [notes, setNotes] = useState(service?.operations_notes ?? "");
  const [repositoryIds, setRepositoryIds] = useState(service?.repositories.map((repo) => repo.id) ?? []);
  const [environmentIds, setEnvironmentIds] = useState([...new Set([
    ...(service?.environments.map(environment => environment.id) ?? []),
    ...(service?.deployments.flatMap(deployment => deployment.environment_id ? [deployment.environment_id] : []) ?? []),
  ])]);
  const [dependencies, setDependencies] = useState(service?.dependencies.map(({ service_id, description }) => ({ service_id, description })) ?? []);
  const [dependents, setDependents] = useState(service?.dependents.map(({ service_id, description }) => ({ service_id, description })) ?? []);
  const [deployments, setDeployments] = useState<(DeploymentInput & { key: string })[]>(service?.deployments.map(({ server, ...deployment }) => ({ ...deployment, key: deployment.id })) ?? []);
  const [servers, setServers] = useState(initialServers);
  const [serverForm, setServerForm] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function addDeployment(serverId = "") {
    const environment = environmentIds.length === 1 ? environments.find(environment => environment.id === environmentIds[0]) : undefined;
    setDeployments((rows) => [...rows, { key: crypto.randomUUID(), server_id: serverId, environment: environment?.name ?? '', environment_id: environment?.id ?? null,
      runtime: "", runtime_name: "", port: null, log_location: "", deployed_version: "" }]);
  }
  function updateDeployment(key: string, changes: Partial<DeploymentInput>) {
    setDeployments((rows) => rows.map((row) => row.key === key ? { ...row, ...changes } : row));
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (deployments.some((deployment) => !deployment.server_id)) {
      setError("Choose a server for each deployment.");
      return;
    }
    if (deployments.some(deployment => !deployment.environment_id && !deployment.id)) {
      setError('Choose an environment for each deployment.');
      return;
    }
    setSaving(true);
    try {
      const result = await saveService({ name, service_type: serviceType, service_role: serviceRole, description, owner, operations_notes: notes,
        repository_ids: repositoryIds, deployments: deployments.map(({ key, ...deployment }) => deployment),
        dependencies, dependents, environment_ids: environmentIds,
        ...(service ? { revision: service.revision } : {}),
      }, service?.id);
      onSaved(result.id);
    } catch (error) { setError(error instanceof ApiError ? error.message : "Could not save this service. Try again."); }
    finally { setSaving(false); }
  }

  return <>
    <form onSubmit={submit} className="flex flex-col gap-6 rounded-lg border p-6">
      <div className="flex items-center justify-between gap-3"><h2 className="text-lg font-semibold">{service ? "Edit service" : "Add service"}</h2>
        <Button type="button" variant="ghost" size="icon" aria-label="Close service form" disabled={saving} onClick={onClose}><X /></Button></div>
      {error && <Alert variant="destructive"><AlertTitle>Service could not be saved</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
      <FieldSet disabled={saving}><FieldLegend>Application details</FieldLegend><FieldGroup className="grid gap-5 md:grid-cols-2">
        <Field><FieldLabel htmlFor="service-name">Service name</FieldLabel><Input id="service-name" required maxLength={200} value={name} onChange={(event) => setName(event.target.value)} placeholder="Payments API" /></Field>
        <Field><FieldLabel htmlFor="service-owner">Owner or team (optional)</FieldLabel><Input id="service-owner" maxLength={200} value={owner} onChange={(event) => setOwner(event.target.value)} placeholder="Payments team" /></Field>
        <Field><FieldLabel htmlFor="service-type">Service type</FieldLabel>
          <Select value={serviceType} onValueChange={value => setServiceType(value as ServiceType)} disabled={saving}>
            <SelectTrigger id="service-type"><SelectValue /></SelectTrigger>
            <SelectContent><SelectGroup>{Object.entries(serviceTypes).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectGroup></SelectContent>
          </Select>
          <FieldDescription>Custom: software we build. Built-in: software such as Nginx or PostgreSQL. External: a service operated outside this system.</FieldDescription>
        </Field>
        <Field><FieldLabel htmlFor="service-role">Primary role</FieldLabel>
          <Select value={serviceRole} onValueChange={value => setServiceRole(value as ServiceRole)} disabled={saving}>
            <SelectTrigger id="service-role"><SelectValue /></SelectTrigger>
            <SelectContent><SelectGroup>{Object.entries(serviceRoles).map(([value, role]) => <SelectItem key={value} value={value}>{role.label}</SelectItem>)}</SelectGroup></SelectContent>
          </Select>
          <FieldDescription>What it does in the architecture. This determines the graph color.</FieldDescription>
        </Field>
        <Field className="md:col-span-2"><FieldLabel htmlFor="service-description">Purpose (optional)</FieldLabel><Textarea id="service-description" maxLength={4000} rows={2} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="What this application does and what depends on it." /></Field>
      </FieldGroup></FieldSet>
      <Field>
        <FieldLabel htmlFor="service-environments">Environments</FieldLabel>
        <DropdownMenu><DropdownMenuTrigger asChild><Button id="service-environments" type="button" variant="outline" className="justify-between" disabled={saving || environments.length === 0} aria-label="Environments">
          {environmentIds.length ? `${environmentIds.length} environment${environmentIds.length === 1 ? '' : 's'} selected` : 'Select environments'}<ChevronDown data-icon="inline-end" />
        </Button></DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="min-w-64"><DropdownMenuGroup>{environments.map(environment => <DropdownMenuCheckboxItem key={environment.id}
            checked={environmentIds.includes(environment.id)} disabled={deployments.some(deployment => deployment.environment_id === environment.id)}
            onSelect={event => event.preventDefault()} onCheckedChange={checked => setEnvironmentIds(ids => checked ? [...ids,environment.id] : ids.filter(id => id !== environment.id))}>
            {environment.name}
          </DropdownMenuCheckboxItem>)}</DropdownMenuGroup></DropdownMenuContent>
        </DropdownMenu>
        <div className="flex flex-wrap gap-2">{environments.filter(environment => environmentIds.includes(environment.id)).map(environment => <Badge key={environment.id} variant="secondary">{environment.name}</Badge>)}</div>
        <FieldDescription>Select one or more environments for this service. To deselect an environment used by a deployment, change or remove that deployment first.</FieldDescription>
        {environments.length === 0 && <FieldDescription>No environments yet. Create one on the <Link to="/environments" className="underline">Environments page</Link>, then reopen this form. You can save a service without deployments first.</FieldDescription>}
      </Field>
      <FieldSet disabled={saving}><FieldLegend>Code projects</FieldLegend>
        <FieldDescription>Select all code projects used by this service. Code projects can be shared between services.</FieldDescription>
        {repositories.length === 0 ? <Alert><AlertDescription>Add source code on the <Link to={`/code-projects`} className="underline">Code projects page</Link>. You can save the service first and link code projects later.</AlertDescription></Alert> :
          <FieldGroup className="max-h-60 gap-3 overflow-y-auto">{repositories.map((repo) => <Field key={repo.id} orientation="horizontal">
            <Checkbox id={`service-repo-${repo.id}`} checked={repositoryIds.includes(repo.id)} disabled={saving} onCheckedChange={(checked) => setRepositoryIds((ids) => checked === true ? [...ids, repo.id] : ids.filter((id) => id !== repo.id))} />
            <FieldContent><FieldLabel htmlFor={`service-repo-${repo.id}`}>{repo.name}</FieldLabel><FieldDescription>{repo.remote_url?.replace(/\.git$/, "") ?? "Local folders"}</FieldDescription></FieldContent>
          </Field>)}</FieldGroup>}
        {service?.repositories.filter((repo) => repo.archived).map((repo) => <Field key={repo.id} orientation="horizontal">
          <Checkbox id={`archived-repo-${repo.id}`} checked={repositoryIds.includes(repo.id)} disabled={saving} onCheckedChange={(checked) => setRepositoryIds((ids) => checked === true ? [...ids, repo.id] : ids.filter((id) => id !== repo.id))} />
          <FieldLabel htmlFor={`archived-repo-${repo.id}`}>{repo.name} (archived — unlink before saving)</FieldLabel>
        </Field>)}
      </FieldSet>
      <FieldSet disabled={saving}><FieldLegend>Service relationships</FieldLegend>
        <FieldDescription>Choose either direction here. Saving also updates the relationship on the other service.</FieldDescription>
        <FieldGroup className="grid gap-6 md:grid-cols-2">
          <ServiceDependencyFields direction="dependencies" serviceName={name} services={services.filter((other) => other.id !== service?.id)} value={dependencies} onChange={setDependencies} disabled={saving} />
          <ServiceDependencyFields direction="dependents" serviceName={name} services={services.filter((other) => other.id !== service?.id)} value={dependents} onChange={setDependents} disabled={saving} />
        </FieldGroup>
      </FieldSet>
      <FieldSet disabled={saving}><FieldLegend>Where it runs</FieldLegend>
        <FieldDescription>Add a deployment for each server and environment. Selecting a deployment adds the service and server to that environment. Leave empty if deployment details are not known yet.</FieldDescription>
        <div className="flex flex-wrap gap-2"><Button type="button" variant="outline" size="sm" disabled={saving} onClick={() => addDeployment()}><Plus data-icon="inline-start" />Add deployment</Button>
          <Button type="button" variant="outline" size="sm" disabled={saving} onClick={() => setServerForm(true)}><Server data-icon="inline-start" />Register server</Button></div>
        {deployments.map((deployment, index) => <FieldSet key={deployment.key} className="rounded-lg border p-4" disabled={saving}>
          <FieldLegend>Deployment {index + 1}</FieldLegend>
          <FieldGroup className="grid gap-5 md:grid-cols-2">
            <Field data-invalid={error !== null && !deployment.server_id}><FieldLabel htmlFor={`deployment-${deployment.key}-server`}>Server</FieldLabel>
              <Select value={deployment.server_id} onValueChange={(server_id) => updateDeployment(deployment.key, { server_id })} disabled={saving}>
                <SelectTrigger id={`deployment-${deployment.key}-server`} aria-invalid={error !== null && !deployment.server_id}><SelectValue placeholder="Choose a server" /></SelectTrigger>
                <SelectContent><SelectGroup>{servers.map((server) => <SelectItem key={server.id} value={server.id}>{server.name} · {server.hostname}</SelectItem>)}</SelectGroup></SelectContent>
              </Select>
              {servers.length === 0 && <FieldDescription>Use Register server to add the first host.</FieldDescription>}
            </Field>
            <Field data-invalid={error !== null && !deployment.environment_id && !deployment.id}><FieldLabel htmlFor={`deployment-${deployment.key}-environment`}>Environment</FieldLabel>
              <Select value={deployment.environment_id ?? (deployment.id ? 'legacy' : '')} disabled={saving || environments.length === 0} onValueChange={environment_id => {
                const environment = environments.find(environment => environment.id === environment_id)!;
                updateDeployment(deployment.key, { environment_id, environment: environment.name });
                setEnvironmentIds(ids => ids.includes(environment_id) ? ids : [...ids,environment_id]);
              }}>
                <SelectTrigger id={`deployment-${deployment.key}-environment`} aria-invalid={error !== null && !deployment.environment_id && !deployment.id}><SelectValue placeholder="Choose an environment" /></SelectTrigger>
                <SelectContent><SelectGroup>{!deployment.environment_id && deployment.id && <SelectItem value="legacy" disabled>{deployment.environment} (unlinked label)</SelectItem>}
                  {environments.map(environment => <SelectItem key={environment.id} value={environment.id}>{environment.name}</SelectItem>)}
                </SelectGroup></SelectContent>
              </Select>
              {!deployment.environment_id && deployment.id && <FieldDescription>This saved label is not linked to an environment. Select an environment to link it.</FieldDescription>}
              {environments.length === 0 && <FieldDescription>Create an environment on the <Link to="/environments" className="underline">Environments page</Link> first.</FieldDescription>}
            </Field>
            <Field><FieldLabel htmlFor={`deployment-${deployment.key}-runtime`}>Runtime or manager (optional)</FieldLabel><Input id={`deployment-${deployment.key}-runtime`} maxLength={80} value={deployment.runtime} onChange={(event) => updateDeployment(deployment.key, { runtime: event.target.value })} placeholder="Docker, Compose, systemd, Kubernetes…" /></Field>
            <Field><FieldLabel htmlFor={`deployment-${deployment.key}-name`}>Container, unit, or workload name (optional)</FieldLabel><Input id={`deployment-${deployment.key}-name`} maxLength={200} value={deployment.runtime_name} onChange={(event) => updateDeployment(deployment.key, { runtime_name: event.target.value })} placeholder="payments-api.service" /></Field>
            <Field><FieldLabel htmlFor={`deployment-${deployment.key}-port`}>Application port (optional)</FieldLabel><Input id={`deployment-${deployment.key}-port`} type="number" min={1} max={65535} step={1} value={deployment.port ?? ""} onChange={(event) => updateDeployment(deployment.key, { port: event.target.value === "" ? null : Number(event.target.value) })} placeholder="8080" /></Field>
            <Field><FieldLabel htmlFor={`deployment-${deployment.key}-version`}>Deployed version (optional)</FieldLabel><Input id={`deployment-${deployment.key}-version`} maxLength={200} value={deployment.deployed_version} onChange={(event) => updateDeployment(deployment.key, { deployed_version: event.target.value })} placeholder="Image tag, release, or deployed commit" /></Field>
            <Field className="md:col-span-2"><FieldLabel htmlFor={`deployment-${deployment.key}-logs`}>Log location (optional)</FieldLabel><Input id={`deployment-${deployment.key}-logs`} maxLength={1000} value={deployment.log_location} onChange={(event) => updateDeployment(deployment.key, { log_location: event.target.value })} placeholder="/var/log/payments/app.log or container stdout" /></Field>
          </FieldGroup>
          <div><Button type="button" variant="ghost" size="sm" disabled={saving} onClick={() => setDeployments((rows) => rows.filter((row) => row.key !== deployment.key))}><Trash2 data-icon="inline-start" />Remove deployment {index + 1}</Button></div>
        </FieldSet>)}
      </FieldSet>
      <FieldSet disabled={saving}><FieldGroup><Field><FieldLabel htmlFor="service-notes">Behavior and investigation notes (optional)</FieldLabel>
        <Textarea id="service-notes" maxLength={10000} rows={4} value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="Expected behavior, important dependencies, useful log patterns, and known failure modes." />
        <FieldDescription>Context for agents investigating this service. Keep passwords and tokens in connection settings.</FieldDescription>
      </Field></FieldGroup></FieldSet>
      <div className="flex gap-2"><Button type="submit" disabled={saving}>{saving && <Spinner data-icon="inline-start" />}{service ? "Save changes" : "Save service"}</Button><Button type="button" variant="outline" disabled={saving} onClick={onClose}>Cancel</Button></div>
    </form>
    {serverForm && <ServerForm onClose={() => setServerForm(false)} onSaved={(server) => {
      setServers((rows) => [...rows, server]);
      const unassigned = deployments.find((deployment) => !deployment.server_id);
      if (unassigned) updateDeployment(unassigned.key, { server_id: server.id });
      else addDeployment(server.id);
      setServerForm(false);
    }} />}
  </>;
}
