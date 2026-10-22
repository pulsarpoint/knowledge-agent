import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { ApiError } from "~/lib/api";
import {
  deleteItem,
  deploymentLabels,
  getServiceConnections,
  getServiceDeployments,
  getServiceRelations,
  isStale,
  updateItem,
  type Connection,
  type Deployment,
  type ItemKind,
  type Relation,
  type RelationKind,
} from "./inventory";
import { getServices, type ServiceAsset } from "./services";
import { serviceRoles, serviceTypes, type ServiceRole, type ServiceType } from "./serviceClassification";
import AddConnectionDialog from "./AddConnectionDialog";
import AddRelationDialog from "./AddRelationDialog";
import EntryDocuments from "./EntryDocuments";
import { RowAction, RowActions } from "./RowAction";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldGroup, FieldLabel, FieldSet } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Skeleton } from "~/components/ui/skeleton";
import { Spinner } from "~/components/ui/spinner";
import { Textarea } from "~/components/ui/textarea";

const CONFLICT = "This was changed meanwhile. Reload to see the latest.";
const message = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

/** How a deployment reads: "docker · payments · :8000 · 1.4". */
const deploymentText = (deployment: Deployment) =>
  [
    deployment.runtime,
    deployment.runtime_name,
    deployment.port === null ? "" : `:${deployment.port}`,
    deployment.deployed_version,
  ]
    .filter(Boolean)
    .join(" · ") || "no details";

interface Data {
  services: ServiceAsset[];
  deployments: Deployment[];
  connections: Connection[];
  relations: Relation[];
}
type Open =
  | { kind: "fields" }
  | { kind: "deployment"; deployment: Deployment }
  | { kind: "connection"; connection?: Connection }
  | { kind: "relation"; relation: RelationKind };

/** A service with what is around it: its fields, its deployments (in the workspace the one on `serverId` in
 * `environmentId`, and where else it runs), connections out and in, relations both ways, code projects and documents.
 * Every change sends the revision it read; a stale one shows the conflict text and reloads. `onChanged` follows each
 * change. Used by the environment workspace (column 3) and the service page. */
export default function ServicePanel({
  serviceId,
  environmentId,
  serverId,
  onChanged,
}: {
  serviceId: string;
  environmentId?: string;
  serverId?: string;
  onChanged?: () => void;
}) {
  const [data, setData] = useState<Data | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [open, setOpen] = useState<Open | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    Promise.all([
      getServices(signal),
      getServiceDeployments(serviceId, signal),
      getServiceConnections(serviceId, signal),
      getServiceRelations(serviceId, signal),
    ])
      .then(([{ services }, { deployments }, { connections }, { relations }]) => {
        if (signal.aborted) return;
        setData({ services, deployments, connections, relations });
        setLoadError(null);
      })
      .catch((e) => {
        if (!signal.aborted) setLoadError(message(e, "Could not load the service."));
      });
    return () => controller.abort();
  }, [serviceId, version]);

  const changed = () => {
    setOpen(null);
    setError(null);
    setVersion((value) => value + 1);
    onChanged?.();
  };
  const conflict = () => {
    changed();
    setError(CONFLICT);
  };
  async function remove(kind: ItemKind, item: { id: string; revision: number }) {
    setError(null);
    try {
      await deleteItem(kind, item.id, item.revision);
      changed();
    } catch (e) {
      if (isStale(e)) conflict();
      else setError(message(e, "Could not remove it. Try again."));
    }
  }

  if (!data)
    return loadError ? (
      <Alert variant="destructive">
        <AlertDescription>{loadError}</AlertDescription>
      </Alert>
    ) : (
      <Skeleton className="h-60" />
    );
  const service = data.services.find((item) => item.id === serviceId);
  if (!service)
    return (
      <Alert>
        <AlertDescription>This service is no longer recorded.</AlertDescription>
      </Alert>
    );

  const workspace = Boolean(environmentId && serverId);
  const places = new Map(service.deployments.map((item) => [item.id, item]));
  const place = (id: string) => {
    const item = places.get(id);
    return item ? `${item.environment} / ${item.server.name}` : "";
  };
  const here = data.deployments.filter(
    (item) => !workspace || (item.environment_id === environmentId && item.server_id === serverId),
  );
  const hereIds = new Set(here.map((item) => item.id));
  const elsewhere = workspace ? service.deployments.filter((item) => !hereIds.has(item.id)) : [];

  const labels = deploymentLabels(data.services);
  const names = new Map(data.services.map((item) => [item.id, item.name]));
  const tail = (connection: Connection) =>
    `${connection.port === null ? "" : ` :${connection.port}`} ${connection.protocol}`;
  const connections = [
    ...data.connections
      .filter((item) => hereIds.has(item.from_deployment_id))
      .map((item) => {
        const target =
          item.to_host ??
          (item.to_service_id ? names.get(item.to_service_id) : labels.get(item.to_deployment_id ?? "")?.label) ??
          "an unknown target";
        return { item, text: `→ ${target}${tail(item)}` };
      }),
    ...data.connections
      .filter((item) => hereIds.has(item.to_deployment_id ?? "") || item.to_service_id === serviceId)
      .map((item) => ({
        item,
        text: `← ${labels.get(item.from_deployment_id)?.label ?? "an unknown deployment"}${tail(item)}`,
      })),
  ];
  const named = (id: string | null) => (id && names.get(id)) || "an unknown service";
  const relations = [
    ...data.relations
      .filter((item) => item.from_service_id === serviceId && item.kind !== "built_from")
      .map((item) => ({
        item,
        text: `${item.kind === "depends_on" ? "Depends on" : "Part of"} ${named(item.to_service_id)}`,
      })),
    ...data.relations
      .filter((item) => item.to_service_id === serviceId)
      .map((item) => ({
        item,
        text: `${item.kind === "depends_on" ? "Needed by" : "Has part"} ${named(item.from_service_id)}`,
      })),
  ];
  const repositories = new Map(service.repositories.map((item) => [item.id, item.name]));
  const codeProjects = data.relations
    .filter((item) => item.kind === "built_from" && item.from_service_id === serviceId)
    .map((item) => ({ item, text: repositories.get(item.to_code_project_id ?? "") ?? "an unknown code project" }));

  return (
    <section aria-label={service.name} className="flex min-w-0 flex-col gap-4 rounded-lg border p-3">
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-1">
          {workspace && <h2 className="break-words text-lg font-semibold">{service.name}</h2>}
          <p className="text-sm text-muted-foreground">
            {[serviceTypes[service.service_type], serviceRoles[service.service_role]?.label, service.owner]
              .filter(Boolean)
              .join(" · ")}
          </p>
          {service.address && <p className="font-mono text-sm">{service.address}</p>}
          {service.description && <p className="whitespace-pre-wrap break-words text-sm">{service.description}</p>}
          {workspace && (
            <Link className="self-start text-sm underline" to={`/services/${encodeURIComponent(serviceId)}`}>
              Service page
            </Link>
          )}
        </div>
        <RowActions>
          <RowAction label="Edit" name={service.name} onClick={() => setOpen({ kind: "fields" })}>
            <Pencil />
          </RowAction>
        </RowActions>
      </div>

      <Part title={workspace ? "Deployment here" : "Runs on"}>
        {here.length === 0 ? (
          <p className="text-sm text-muted-foreground">No deployment recorded.</p>
        ) : (
          <List>
            {here.map((item) => {
              const name = workspace ? "the deployment here" : place(item.id);
              return (
                <Row
                  key={item.id}
                  text={workspace ? deploymentText(item) : `${place(item.id)} · ${deploymentText(item)}`}
                  name={name}
                  onEdit={() => setOpen({ kind: "deployment", deployment: item })}
                  onRemove={() => void remove("deployments", item)}
                />
              );
            })}
          </List>
        )}
      </Part>
      {elsewhere.length > 0 && (
        <Part title="Also runs on">
          <List>
            {elsewhere.map((item) => (
              <li key={item.id} className="text-sm">
                <Link
                  className="underline"
                  to={`/environments/${encodeURIComponent(item.environment_id)}?server=${encodeURIComponent(item.server_id)}&service=${encodeURIComponent(serviceId)}`}
                >
                  {item.environment} / {item.server.name}
                </Link>
              </li>
            ))}
          </List>
        </Part>
      )}

      <Part
        title="Connections"
        add={here.length > 0 ? () => setOpen({ kind: "connection" }) : undefined}
        addLabel="Add connection"
      >
        {connections.length === 0 ? (
          <p className="text-sm text-muted-foreground">No connections.</p>
        ) : (
          <List>
            {connections.map(({ item, text }) => (
              <Row
                key={`${item.id}${text[0]}`}
                text={text}
                name={text}
                onEdit={() => setOpen({ kind: "connection", connection: item })}
                onRemove={() => void remove("connections", item)}
              />
            ))}
          </List>
        )}
      </Part>

      <Part title="Relations" add={() => setOpen({ kind: "relation", relation: "depends_on" })} addLabel="Add relation">
        {relations.length === 0 ? (
          <p className="text-sm text-muted-foreground">No relations.</p>
        ) : (
          <List>
            {relations.map(({ item, text }) => (
              <Row key={item.id} text={text} name={text} onRemove={() => void remove("relations", item)} />
            ))}
          </List>
        )}
      </Part>

      <Part
        title="Code projects"
        add={() => setOpen({ kind: "relation", relation: "built_from" })}
        addLabel="Add code project"
      >
        {codeProjects.length === 0 ? (
          <p className="text-sm text-muted-foreground">No code projects.</p>
        ) : (
          <List>
            {codeProjects.map(({ item, text }) => (
              <Row key={item.id} text={text} name={text} onRemove={() => void remove("relations", item)} />
            ))}
          </List>
        )}
      </Part>

      <EntryDocuments entry={{ kind: "service", id: serviceId, name: service.name }} onChanged={onChanged} />

      {open?.kind === "fields" && (
        <ServiceFieldsSheet service={service} onSaved={changed} onConflict={conflict} onClose={() => setOpen(null)} />
      )}
      {open?.kind === "deployment" && (
        <DeploymentSheet
          deployment={open.deployment}
          onSaved={changed}
          onConflict={conflict}
          onClose={() => setOpen(null)}
        />
      )}
      {open?.kind === "connection" && (
        <AddConnectionDialog
          from={[...hereIds]}
          services={data.services}
          connection={open.connection}
          onSaved={changed}
          onConflict={conflict}
          onClose={() => setOpen(null)}
        />
      )}
      {open?.kind === "relation" && (
        <AddRelationDialog
          serviceId={serviceId}
          services={data.services}
          kind={open.relation}
          onSaved={changed}
          onClose={() => setOpen(null)}
        />
      )}
    </section>
  );
}

function Part({
  title,
  add,
  addLabel,
  children,
}: {
  title: string;
  add?: () => void;
  addLabel?: string;
  children: ReactNode;
}) {
  return (
    <section aria-label={title} className="flex flex-col gap-1">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-semibold">{title}</h3>
        {add && (
          <Button type="button" size="sm" variant="ghost" aria-label={addLabel} onClick={add}>
            <Plus />
          </Button>
        )}
      </div>
      {children}
    </section>
  );
}

const List = ({ children }: { children: ReactNode }) => <ul className="flex flex-col gap-1">{children}</ul>;

function Row({
  text,
  name,
  onEdit,
  onRemove,
}: {
  text: string;
  name: string;
  onEdit?: () => void;
  onRemove: () => void;
}) {
  return (
    <li className="flex items-center justify-between gap-2 text-sm">
      <span className="min-w-0 break-words">{text}</span>
      <RowActions>
        {onEdit && (
          <RowAction label="Edit" name={name} onClick={onEdit}>
            <Pencil />
          </RowAction>
        )}
        <RowAction label="Remove" name={name} destructive onClick={onRemove}>
          <Trash2 />
        </RowAction>
      </RowActions>
    </li>
  );
}

/** A sheet with a form that saves through `save`; a stale revision is `onConflict`, other refusals stay shown. */
function EditSheet({
  title,
  button,
  save,
  onSaved,
  onConflict,
  onClose,
  children,
}: {
  title: string;
  button: string;
  save: () => Promise<unknown>;
  onSaved: () => void;
  onConflict: () => void;
  onClose: () => void;
  children: ReactNode;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await save();
      onSaved();
    } catch (e) {
      if (isStale(e)) onConflict();
      else setError(message(e, "Could not save. Try again."));
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
          <SheetTitle>{title}</SheetTitle>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-col gap-6 p-4">
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <FieldSet disabled={saving}>
            <FieldGroup>{children}</FieldGroup>
          </FieldSet>
          <div className="flex gap-2">
            <Button disabled={saving}>
              {saving && <Spinner data-icon="inline-start" />}
              {button}
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

function TextField({
  id,
  label,
  value,
  onChange,
  max,
  type,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  max?: number;
  type?: "number";
}) {
  return (
    <Field>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <Input
        id={id}
        type={type}
        maxLength={max}
        min={type ? 1 : undefined}
        max={type ? 65535 : undefined}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </Field>
  );
}

/** The service's own fields; the Address field is shown for type external (and cleared for another type). */
function ServiceFieldsSheet({
  service,
  onSaved,
  onConflict,
  onClose,
}: {
  service: ServiceAsset;
  onSaved: () => void;
  onConflict: () => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(service.name);
  const [type, setType] = useState<ServiceType>(service.service_type);
  const [role, setRole] = useState<ServiceRole>(service.service_role);
  const [owner, setOwner] = useState(service.owner);
  const [description, setDescription] = useState(service.description);
  const [address, setAddress] = useState(service.address ?? "");
  return (
    <EditSheet
      title="Edit service"
      button="Save service"
      save={() =>
        updateItem("services", service.id, service.revision, {
          name,
          service_type: type,
          service_role: role,
          owner,
          description,
          address: type === "external" ? address.trim() : "",
        })
      }
      onSaved={onSaved}
      onConflict={onConflict}
      onClose={onClose}
    >
      <TextField id="service-fields-name" label="Name" value={name} onChange={setName} max={200} />
      <Field>
        <FieldLabel htmlFor="service-fields-type">Type</FieldLabel>
        <Select value={type} onValueChange={(value) => setType(value as ServiceType)}>
          <SelectTrigger id="service-fields-type" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {(Object.keys(serviceTypes) as ServiceType[]).map((value) => (
                <SelectItem key={value} value={value}>
                  {serviceTypes[value]}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
      </Field>
      {type === "external" && (
        <TextField id="service-fields-address" label="Address" value={address} onChange={setAddress} max={253} />
      )}
      <Field>
        <FieldLabel htmlFor="service-fields-role">Role</FieldLabel>
        <Select value={role} onValueChange={(value) => setRole(value as ServiceRole)}>
          <SelectTrigger id="service-fields-role" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {(Object.keys(serviceRoles) as ServiceRole[]).map((value) => (
                <SelectItem key={value} value={value}>
                  {serviceRoles[value].label}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
      </Field>
      <TextField id="service-fields-owner" label="Owner" value={owner} onChange={setOwner} max={200} />
      <Field>
        <FieldLabel htmlFor="service-fields-description">Description</FieldLabel>
        <Textarea
          id="service-fields-description"
          maxLength={4000}
          rows={3}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
      </Field>
    </EditSheet>
  );
}

function DeploymentSheet({
  deployment,
  onSaved,
  onConflict,
  onClose,
}: {
  deployment: Deployment;
  onSaved: () => void;
  onConflict: () => void;
  onClose: () => void;
}) {
  const [runtime, setRuntime] = useState(deployment.runtime);
  const [runtimeName, setRuntimeName] = useState(deployment.runtime_name);
  const [port, setPort] = useState(deployment.port?.toString() ?? "");
  const [version, setVersion] = useState(deployment.deployed_version);
  const [logLocation, setLogLocation] = useState(deployment.log_location);
  return (
    <EditSheet
      title="Edit deployment"
      button="Save deployment"
      save={() =>
        updateItem("deployments", deployment.id, deployment.revision, {
          runtime,
          runtime_name: runtimeName,
          port: port === "" ? null : Number(port),
          deployed_version: version,
          log_location: logLocation,
        })
      }
      onSaved={onSaved}
      onConflict={onConflict}
      onClose={onClose}
    >
      <TextField id="deployment-runtime" label="Runtime" value={runtime} onChange={setRuntime} max={80} />
      <TextField
        id="deployment-runtime-name"
        label="Runtime name"
        value={runtimeName}
        onChange={setRuntimeName}
        max={200}
      />
      <TextField id="deployment-port" label="Port" value={port} onChange={setPort} type="number" />
      <TextField id="deployment-version" label="Version" value={version} onChange={setVersion} max={200} />
      <TextField id="deployment-log" label="Log location" value={logLocation} onChange={setLogLocation} max={1000} />
    </EditSheet>
  );
}
