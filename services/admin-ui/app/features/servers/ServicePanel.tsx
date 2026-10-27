import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router";
import { Pencil, Plus } from "lucide-react";
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
import { serviceRoles, serviceTypes } from "./serviceClassification";
import ServiceFields, { serviceFields, serviceValue } from "./ServiceFields";
import AddConnectionDialog from "./AddConnectionDialog";
import AddRelationDialog from "./AddRelationDialog";
import { DraftBadge, DraftRowMenu } from "./ConfirmDraftSheet";
import { CONFLICT, draftOf, List, Row, removeItem } from "./rows";
import ConfirmRemove, { type Removal } from "~/components/ConfirmRemove";
import EntryDocuments from "./EntryDocuments";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { cn } from "~/lib/utils";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { Field, FieldGroup, FieldLabel, FieldSet } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Skeleton } from "~/components/ui/skeleton";
import { Spinner } from "~/components/ui/spinner";

const message = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

/** How a deployment reads: "docker · payments · :8000 · 1.4". */
export const deploymentText = (deployment: Deployment) =>
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

/** The connections of a service's deployments `from` (ids), out (→) and in (←; an external service's connections are
 * in, to the service itself): each with how it reads and the other service, when known. */
export function connectionRows(
  services: ServiceAsset[],
  connections: Connection[],
  from: Set<string>,
  serviceId: string,
): { item: Connection; text: string; to?: string }[] {
  const labels = deploymentLabels(services);
  const names = new Map(services.map((item) => [item.id, item.name]));
  const tail = (connection: Connection) =>
    `${connection.port === null ? "" : ` :${connection.port}`} ${connection.protocol}`;
  return [
    ...connections
      .filter((item) => from.has(item.from_deployment_id))
      .map((item) => {
        const target =
          item.to_host ??
          (item.to_service_id ? names.get(item.to_service_id) : labels.get(item.to_deployment_id ?? "")?.label) ??
          "an unknown target";
        const to = item.to_service_id ?? labels.get(item.to_deployment_id ?? "")?.service_id;
        return { item, text: `→ ${target}${tail(item)}`, to };
      }),
    ...connections
      .filter((item) => from.has(item.to_deployment_id ?? "") || item.to_service_id === serviceId)
      .map((item) => ({
        item,
        text: `← ${labels.get(item.from_deployment_id)?.label ?? "an unknown deployment"}${tail(item)}`,
        to: labels.get(item.from_deployment_id)?.service_id,
      })),
  ];
}

/** A service's page (its View). */
export const servicePath = (id: string, page = "view") => `/services/${encodeURIComponent(id)}/${page}`;

/** The environment workspace's service column: the service's fields, its deployment on `serverId` in `environmentId`
 * (and where else it runs), connections out and in, relations both ways, code projects, and documents in a tab beside
 * Details. Every change sends the revision it read; a stale one shows the conflict text and reloads. `onChanged` follows
 * each change. A new `refresh` number reloads it. */
export default function ServicePanel({
  serviceId,
  environmentId,
  serverId,
  onChanged,
  refresh = 0,
}: {
  serviceId: string;
  environmentId: string;
  serverId: string;
  onChanged?: () => void;
  refresh?: number;
}) {
  const [data, setData] = useState<Data | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [open, setOpen] = useState<Open | null>(null);
  const [removal, setRemoval] = useState<Removal | null>(null);

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
  }, [serviceId, version, refresh]);

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
  /** Asks first: "Remove <what>?" with what it means, then removes. */
  const ask = (kind: ItemKind, item: { id: string; revision: number }, what: string, description: string) =>
    setRemoval({ title: `Remove ${what}?`, description, action: "Remove", run: () => void remove(kind, item) });
  const remove = (kind: ItemKind, item: { id: string; revision: number }) => removeItem(kind, item, changed, setError);

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

  const here = data.deployments.filter((item) => item.environment_id === environmentId && item.server_id === serverId);
  const hereIds = new Set(here.map((item) => item.id));
  const elsewhere = service.deployments.filter((item) => !hereIds.has(item.id));

  const names = new Map(data.services.map((item) => [item.id, item.name]));
  const connections = connectionRows(data.services, data.connections, hereIds, serviceId);
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

  const details = (
    <>
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-1">
          <h2 className="break-words text-lg font-semibold">{service.name}</h2>
          {service.status === "draft" && (
            <span>
              <DraftBadge />
            </span>
          )}
          <p className="text-sm text-muted-foreground">
            {[serviceTypes[service.service_type], serviceRoles[service.service_role]?.label, service.owner]
              .filter(Boolean)
              .join(" · ")}
          </p>
          {service.address && <p className="font-mono text-sm">{service.address}</p>}
          {service.description && <Description text={service.description} />}
          <Link className="self-start text-sm underline" to={servicePath(serviceId)}>
            Service page
          </Link>
        </div>
        <DraftRowMenu
          name={service.name}
          draft={
            service.status === "draft"
              ? { confirm: { kind: "service", id: service.id, revision: service.revision }, onDone: changed }
              : undefined
          }
          items={[{ label: "Edit", icon: Pencil, onSelect: () => setOpen({ kind: "fields" }) }]}
        />
      </div>

      <Part title="Deployment here">
        {here.length === 0 ? (
          <p className="text-sm text-muted-foreground">No deployment recorded.</p>
        ) : (
          <List>
            {here.map((item) => (
              <Row
                key={item.id}
                text={deploymentText(item)}
                name="the deployment here"
                draft={draftOf("deployment", item)}
                onDone={changed}
                onEdit={() => setOpen({ kind: "deployment", deployment: item })}
                onRemove={() =>
                  ask(
                    "deployments",
                    item,
                    `the deployment of ${service.name} here`,
                    "Its connections are removed with it.",
                  )
                }
              />
            ))}
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
            {connections.map(({ item, text, to }) => (
              <Row
                key={`${item.id}${text[0]}`}
                text={text}
                href={to ? servicePath(to) : undefined}
                name={text}
                draft={draftOf("connection", item)}
                onDone={changed}
                onEdit={() => setOpen({ kind: "connection", connection: item })}
                onRemove={() =>
                  ask("connections", item, `the connection ${text}`, "The services stay; only this connection goes.")
                }
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
              <Row
                key={item.id}
                text={text}
                name={text}
                draft={draftOf("relation", item)}
                onDone={changed}
                onRemove={() =>
                  ask("relations", item, `the relation “${text}”`, "Both services stay; only this relation goes.")
                }
              />
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
              <Row
                key={item.id}
                text={text}
                name={text}
                draft={draftOf("relation", item)}
                onDone={changed}
                onRemove={() =>
                  ask(
                    "relations",
                    item,
                    `code project ${text} from ${service.name}`,
                    "The code project stays; only the link goes.",
                  )
                }
              />
            ))}
          </List>
        )}
      </Part>
    </>
  );

  return (
    <section aria-label={service.name} className="flex min-w-0 flex-col gap-4 rounded-lg border p-3">
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <Tabs defaultValue="details" className="flex min-w-0 flex-col gap-4">
        <TabsList aria-label="Service">
          <TabsTrigger value="details">Details</TabsTrigger>
          <TabsTrigger value="documents">Documents</TabsTrigger>
        </TabsList>
        <TabsContent value="details" className="flex min-w-0 flex-col gap-4">
          {details}
        </TabsContent>
        <TabsContent value="documents" className="min-w-0">
          <EntryDocuments entry={{ kind: "service", id: serviceId, name: service.name }} onChanged={onChanged} />
        </TabsContent>
      </Tabs>

      <ConfirmRemove removal={removal} onClose={() => setRemoval(null)} />
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
  const [value, setValue] = useState(serviceFields(service));
  return (
    <EditSheet
      title="Edit service"
      button="Save service"
      save={() => updateItem("services", service.id, service.revision, serviceValue(value))}
      onSaved={onSaved}
      onConflict={onConflict}
      onClose={onClose}
    >
      <ServiceFields idPrefix="service-fields" value={value} onChange={setValue} />
    </EditSheet>
  );
}

export function DeploymentSheet({
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

/** A long description shows its first lines, with Show more / Show less. */
export function Description({ text }: { text: string }) {
  const [full, setFull] = useState(false);
  const long = text.length > 300 || text.split("\n").length > 4;
  return (
    <div className="flex flex-col items-start gap-1">
      <p className={cn("whitespace-pre-wrap text-sm [overflow-wrap:anywhere]", long && !full && "line-clamp-4")}>
        {text}
      </p>
      {long && (
        <Button
          type="button"
          variant="link"
          size="sm"
          className="h-auto p-0"
          onClick={() => setFull((value) => !value)}
        >
          {full ? "Show less" : "Show more"}
        </Button>
      )}
    </div>
  );
}
