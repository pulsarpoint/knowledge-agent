import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link as RouterLink } from "react-router";
import { ApiError } from "~/lib/api";
import { getDocuments, type DocumentSummary } from "~/features/documents/api";
import { getRepositories } from "~/features/codeProjects/api";
import {
  ENTRY_LABELS,
  ENTRY_PAGES,
  getEntryDocuments,
  linkDocument,
  unlinkDocument,
  type EntryDocuments,
  type MapEntry,
} from "./inventoryMap";
import {
  RELATION_LABELS,
  connectionText,
  createItem,
  deleteItem,
  deploymentLabels,
  getServiceConnections,
  getServiceRelations,
  relationText,
  updateItem,
  type Connection,
  type ConnectionValue,
  type DeploymentLabel,
  type Relation,
  type RelationKind,
  type RelationValue,
} from "./inventory";
import { getServices } from "./services";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Field, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Textarea } from "~/components/ui/textarea";

const say = (reason: unknown, fallback: string) => (reason instanceof ApiError ? reason.message : fallback);
const EXTERNAL = "external";
const SERVICE = "service:"; // the To value of an external service: "service:<id>"

/** A connection's fields: from one of the service's deployments to another deployment (its port prefilled), to an
 * external service, or to an external host. */
function ConnectionForm({
  id,
  label,
  from,
  deployments,
  externals,
  initial,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  id: string;
  label: string;
  from: DeploymentLabel[];
  deployments: DeploymentLabel[];
  externals: { id: string; label: string }[];
  initial?: Connection;
  submitLabel: string;
  onSubmit: (value: ConnectionValue) => Promise<boolean>;
  onCancel?: () => void;
}) {
  const [source, setSource] = useState(initial?.from_deployment_id ?? from[0]?.id ?? "");
  const [target, setTarget] = useState(
    initial
      ? (initial.to_deployment_id ?? (initial.to_service_id ? `${SERVICE}${initial.to_service_id}` : EXTERNAL))
      : "",
  );
  const [host, setHost] = useState(initial?.to_host ?? "");
  const [port, setPort] = useState(initial?.port?.toString() ?? "");
  const [protocol, setProtocol] = useState(initial?.protocol ?? "tcp");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [busy, setBusy] = useState(false);
  const targets = deployments.filter((deployment) => deployment.id !== source);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    const external = target === EXTERNAL;
    const service = target.startsWith(SERVICE) ? target.slice(SERVICE.length) : null;
    const saved = await onSubmit({
      from_deployment_id: source,
      to_deployment_id: external || service ? null : target,
      to_service_id: service,
      to_host: external ? host.trim() : null,
      port: port === "" ? null : Number(port),
      protocol: protocol.trim(),
      description,
    });
    setBusy(false);
    if (saved && !initial) {
      setTarget("");
      setHost("");
      setPort("");
      setProtocol("tcp");
      setDescription("");
    }
  }
  return (
    <form onSubmit={submit} aria-label={label} className="flex flex-col gap-2 rounded-md border p-2">
      <FieldGroup className="gap-3">
        <Field>
          <FieldLabel htmlFor={`${id}-from`}>From</FieldLabel>
          <Select value={source} onValueChange={setSource}>
            <SelectTrigger id={`${id}-from`} className="w-full">
              <SelectValue placeholder="Choose a deployment" />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                {from.map((deployment) => (
                  <SelectItem key={deployment.id} value={deployment.id}>
                    {deployment.label}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
        </Field>
        <Field>
          <FieldLabel htmlFor={`${id}-to`}>To</FieldLabel>
          <Select
            value={target}
            onValueChange={(value) => {
              setTarget(value);
              const chosen = deployments.find((deployment) => deployment.id === value);
              if (chosen) setPort(chosen.port?.toString() ?? "");
            }}
          >
            <SelectTrigger id={`${id}-to`} className="w-full">
              <SelectValue placeholder="Choose a deployment, an external service or a host" />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                {targets.map((deployment) => (
                  <SelectItem key={deployment.id} value={deployment.id}>
                    {deployment.label}
                  </SelectItem>
                ))}
                {externals.map((service) => (
                  <SelectItem key={service.id} value={`${SERVICE}${service.id}`}>
                    {service.label}
                  </SelectItem>
                ))}
                <SelectItem value={EXTERNAL}>An external host</SelectItem>
              </SelectGroup>
            </SelectContent>
          </Select>
        </Field>
        {target === EXTERNAL && (
          <Field>
            <FieldLabel htmlFor={`${id}-host`}>Host</FieldLabel>
            <Input
              id={`${id}-host`}
              value={host}
              maxLength={253}
              placeholder="smtp.example.com or 10.0.0.5"
              onChange={(event) => setHost(event.target.value)}
            />
          </Field>
        )}
        <div className="grid grid-cols-2 gap-3">
          <Field>
            <FieldLabel htmlFor={`${id}-port`}>Port</FieldLabel>
            <Input
              id={`${id}-port`}
              type="number"
              min={1}
              max={65535}
              value={port}
              onChange={(event) => setPort(event.target.value)}
            />
          </Field>
          <Field>
            <FieldLabel htmlFor={`${id}-protocol`}>Protocol</FieldLabel>
            <Input
              id={`${id}-protocol`}
              value={protocol}
              maxLength={20}
              placeholder="tcp, http, postgres…"
              onChange={(event) => setProtocol(event.target.value)}
            />
          </Field>
        </div>
        <Field>
          <FieldLabel htmlFor={`${id}-description`}>Description</FieldLabel>
          <Textarea
            id={`${id}-description`}
            value={description}
            maxLength={4000}
            rows={2}
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>
      </FieldGroup>
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={busy || !source || !target}>
          {submitLabel}
        </Button>
        {onCancel && (
          <Button type="button" size="sm" variant="outline" onClick={onCancel}>
            Cancel
          </Button>
        )}
      </div>
    </form>
  );
}

/** A new relation from this service: the kind, then a service (depends on, is part of) or a code project (is built
 * from). */
function RelationForm({
  serviceId,
  services,
  codeProjects,
  onSubmit,
}: {
  serviceId: string;
  services: { id: string; name: string }[];
  codeProjects: { id: string; name: string }[];
  onSubmit: (value: RelationValue) => Promise<boolean>;
}) {
  const [kind, setKind] = useState<RelationKind>("depends_on");
  const [target, setTarget] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const built = kind === "built_from";
  const targets = built ? codeProjects : services.filter((service) => service.id !== serviceId);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    const saved = await onSubmit({
      from_service_id: serviceId,
      kind,
      to_service_id: built ? null : target,
      to_code_project_id: built ? target : null,
      description,
    });
    setBusy(false);
    if (saved) {
      setTarget("");
      setDescription("");
    }
  }
  return (
    <form onSubmit={submit} className="flex flex-col gap-2">
      <FieldGroup className="gap-3">
        <div className="grid grid-cols-2 gap-3">
          <Field>
            <FieldLabel htmlFor="relation-kind">Relation</FieldLabel>
            <Select
              value={kind}
              onValueChange={(value) => {
                setKind(value as RelationKind);
                setTarget("");
              }}
            >
              <SelectTrigger id="relation-kind" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {(Object.keys(RELATION_LABELS) as RelationKind[]).map((item) => (
                    <SelectItem key={item} value={item}>
                      {RELATION_LABELS[item]}
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
          </Field>
          <Field>
            <FieldLabel htmlFor="relation-target">Target</FieldLabel>
            <Select value={target} onValueChange={setTarget}>
              <SelectTrigger id="relation-target" className="w-full">
                <SelectValue placeholder={built ? "Choose a code project" : "Choose a service"} />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {targets.map((item) => (
                    <SelectItem key={item.id} value={item.id}>
                      {item.name}
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
          </Field>
        </div>
        <Field>
          <FieldLabel htmlFor="relation-description">Description</FieldLabel>
          <Textarea
            id="relation-description"
            value={description}
            maxLength={4000}
            rows={2}
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>
      </FieldGroup>
      <Button type="submit" size="sm" className="self-start" disabled={busy || !target}>
        Add relation
      </Button>
    </form>
  );
}

/** What the sheet of a service shows besides documents: its deployments' connections and its relations. */
interface ServiceLinks {
  deployments: Map<string, DeploymentLabel>;
  names: Map<string, string>;
  services: { id: string; name: string }[];
  /** External services, the connection targets beside deployments: "stripe · api.stripe.com". */
  externals: { id: string; label: string }[];
  codeProjects: { id: string; name: string }[];
  connections: Connection[];
  relations: Relation[];
}

async function loadServiceLinks(serviceId: string): Promise<ServiceLinks> {
  const [{ services }, { repositories }, { connections }, { relations }] = await Promise.all([
    getServices(),
    getRepositories(),
    getServiceConnections(serviceId),
    getServiceRelations(serviceId),
  ]);
  return {
    deployments: deploymentLabels(services),
    names: new Map([...services, ...repositories].map((item) => [item.id, item.name])),
    services: services.map(({ id, name }) => ({ id, name })),
    externals: services
      .filter((service) => service.service_type === "external")
      .map(({ id, name, address }) => ({ id, label: address ? `${name} · ${address}` : name })),
    codeProjects: repositories.map(({ id, name }) => ({ id, name })),
    connections,
    relations,
  };
}

function DocumentPicker({ linked, onPick }: { linked: string[]; onPick: (document: DocumentSummary) => void }) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<DocumentSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function find(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      setFound((await getDocuments(new URLSearchParams({ q: q.trim() }))).documents);
    } catch (reason) {
      setError(say(reason, "Could not search documents."));
    }
  }
  const shown = found?.filter((document) => !linked.includes(document.id));
  return (
    <form onSubmit={find} className="flex flex-col gap-2">
      <h4 className="text-sm font-medium">Link a document</h4>
      <div className="flex gap-2">
        <Input
          aria-label="Find a document"
          value={q}
          placeholder="Title or slug"
          onChange={(event) => setQ(event.target.value)}
        />
        <Button type="submit" variant="outline">
          Find
        </Button>
      </div>
      {error && <p className="text-destructive text-sm">{error}</p>}
      {shown &&
        (shown.length === 0 ? (
          <p className="text-muted-foreground text-sm">No documents to link.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {shown.map((document) => (
              <li key={document.id} className="flex items-center justify-between gap-2 text-sm">
                <span>{document.title}</span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  aria-label={`Link: ${document.title}`}
                  onClick={() => onPick(document)}
                >
                  Link
                </Button>
              </li>
            ))}
          </ul>
        ))}
    </form>
  );
}

/** An entry of the System map: for a service, its deployments' network connections and its relations (each added,
 * edited and removed through the inventory API); for every entry, its documents. */
export default function EntrySheet({
  entry,
  onClose,
  onChanged,
}: {
  entry: MapEntry;
  onClose: () => void;
  onChanged: () => void;
}) {
  const isService = entry.kind === "service";
  const [links, setLinks] = useState<ServiceLinks | null>(null);
  const [documents, setDocuments] = useState<EntryDocuments>({ inventory_document: null, documents: [] });
  const [editing, setEditing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const [linked, documented] = await Promise.all([
        isService ? loadServiceLinks(entry.id) : Promise.resolve(null),
        getEntryDocuments(entry),
      ]);
      setLinks(linked);
      setDocuments(documented);
    } catch (reason) {
      setError(say(reason, "Could not load the entry."));
    }
  }, [entry.kind, entry.id]);
  useEffect(() => {
    void load();
  }, [load]);
  /** Runs a write; on success the map and the sheet reload, on failure the message shows and the sheet reloads (a
   * stale revision is read again). Answers whether it was saved. */
  async function act(work: () => Promise<unknown>, fallback: string): Promise<boolean> {
    setError(null);
    try {
      await work();
      setEditing(null);
      onChanged();
      await load();
      return true;
    } catch (reason) {
      setError(say(reason, fallback));
      await load();
      return false;
    }
  }
  const page = ENTRY_PAGES[entry.kind];
  // The entry's own linked documents (the list also has its children's documents); the inventory document apart.
  const own = documents.documents.filter(
    (document) => document.resource.id === entry.id && document.id !== documents.inventory_document?.id,
  );
  const deployments = links ? [...links.deployments.values()] : [];
  const mine = deployments.filter((deployment) => deployment.service_id === entry.id);
  return (
    <Sheet
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>{entry.name}</SheetTitle>
          <SheetDescription>
            {ENTRY_LABELS[entry.kind]} ·{" "}
            <RouterLink to={page.page} className="underline">
              Open in {page.label}
            </RouterLink>
          </SheetDescription>
        </SheetHeader>
        <div className="flex flex-col gap-6 px-4 pb-4">
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          {!isService ? (
            <p className="text-muted-foreground text-sm">
              Connections and relations belong to services: open a service on the map to edit them.
            </p>
          ) : links === null ? (
            <Spinner />
          ) : (
            <>
              <section aria-label="Connections" className="flex flex-col gap-2">
                <h3 className="text-sm font-medium">Connections</h3>
                {links.connections.length === 0 ? (
                  <p className="text-muted-foreground text-sm">No connections yet.</p>
                ) : (
                  <ul className="flex flex-col gap-2 text-sm">
                    {links.connections.map((connection) => {
                      const text = connectionText(connection, links.deployments, links.names);
                      const outgoing = mine.some((deployment) => deployment.id === connection.from_deployment_id);
                      return (
                        <li key={connection.id} className="flex flex-col gap-1">
                          <div className="flex items-center justify-between gap-2">
                            <span>{text}</span>
                            <span className="flex gap-1">
                              {outgoing && (
                                <Button
                                  type="button"
                                  size="sm"
                                  variant="ghost"
                                  aria-label={`Edit ${text}`}
                                  onClick={() => setEditing(connection.id)}
                                >
                                  Edit
                                </Button>
                              )}
                              <Button
                                type="button"
                                size="sm"
                                variant="ghost"
                                aria-label={`Remove ${text}`}
                                onClick={() =>
                                  void act(
                                    () => deleteItem("connections", connection.id, connection.revision),
                                    "Could not remove the connection.",
                                  )
                                }
                              >
                                Remove
                              </Button>
                            </span>
                          </div>
                          {connection.description && (
                            <span className="text-muted-foreground text-xs">{connection.description}</span>
                          )}
                          {editing === connection.id && (
                            <ConnectionForm
                              key={connection.revision}
                              id={`connection-${connection.id}`}
                              label={`Edit ${text}`}
                              from={mine}
                              deployments={deployments}
                              externals={links.externals}
                              initial={connection}
                              submitLabel="Save connection"
                              onCancel={() => setEditing(null)}
                              onSubmit={(value) =>
                                act(
                                  () => updateItem("connections", connection.id, connection.revision, value),
                                  "Could not save the connection.",
                                )
                              }
                            />
                          )}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </section>
              <section aria-label="New connection" className="flex flex-col gap-2">
                <h3 className="text-sm font-medium">Add a connection</h3>
                {mine.length === 0 ? (
                  <p className="text-muted-foreground text-sm">
                    This service has no deployments. Add one on the Services page to connect it.
                  </p>
                ) : (
                  <ConnectionForm
                    id="new-connection"
                    label="New connection"
                    from={mine}
                    deployments={deployments}
                    externals={links.externals}
                    submitLabel="Add connection"
                    onSubmit={(value) => act(() => createItem("connections", value), "Could not add the connection.")}
                  />
                )}
              </section>
              <section aria-label="Relations" className="flex flex-col gap-2">
                <h3 className="text-sm font-medium">Relations</h3>
                {links.relations.length === 0 ? (
                  <p className="text-muted-foreground text-sm">No relations yet.</p>
                ) : (
                  <ul className="flex flex-col gap-2 text-sm">
                    {links.relations.map((relation) => {
                      const text = relationText(relation, links.names);
                      return (
                        <li key={relation.id} className="flex flex-col gap-1">
                          <div className="flex items-center justify-between gap-2">
                            <span>{text}</span>
                            <Button
                              type="button"
                              size="sm"
                              variant="ghost"
                              aria-label={`Remove ${text}`}
                              onClick={() =>
                                void act(
                                  () => deleteItem("relations", relation.id, relation.revision),
                                  "Could not remove the relation.",
                                )
                              }
                            >
                              Remove
                            </Button>
                          </div>
                          {relation.description && (
                            <span className="text-muted-foreground text-xs">{relation.description}</span>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </section>
              <section aria-label="New relation" className="flex flex-col gap-2">
                <h3 className="text-sm font-medium">Add a relation</h3>
                <RelationForm
                  serviceId={entry.id}
                  services={links.services}
                  codeProjects={links.codeProjects}
                  onSubmit={(value) => act(() => createItem("relations", value), "Could not add the relation.")}
                />
              </section>
            </>
          )}
          <section aria-label="Documents" className="flex flex-col gap-2">
            <h3 className="text-sm font-medium">Documents</h3>
            {!documents.inventory_document && own.length === 0 ? (
              <p className="text-muted-foreground text-sm">No documents are linked.</p>
            ) : (
              <ul className="flex flex-col gap-1 text-sm">
                {documents.inventory_document && (
                  <li className="flex items-center gap-2">
                    <RouterLink to={`/documents/${documents.inventory_document.id}`} className="underline">
                      {documents.inventory_document.title}
                    </RouterLink>
                    <Badge variant="secondary">Inventory</Badge>
                  </li>
                )}
                {own.map((document) => (
                  <li key={document.id} className="flex items-center justify-between gap-2">
                    <span className="flex items-center gap-2">
                      <RouterLink to={`/documents/${document.id}`} className="underline">
                        {document.title}
                      </RouterLink>
                      {document.connection === "built_for" && (
                        <span className="text-muted-foreground text-xs">built for</span>
                      )}
                    </span>
                    {document.connection === "linked" && (
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        aria-label={`Remove link: ${document.title}`}
                        onClick={() => void act(() => unlinkDocument(entry, document.id), "Could not remove the link.")}
                      >
                        Remove
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            )}
            <DocumentPicker
              linked={[
                ...(documents.inventory_document ? [documents.inventory_document.id] : []),
                ...own.map((document) => document.id),
              ]}
              onPick={(document) => void act(() => linkDocument(entry, document.id), "Could not link the document.")}
            />
          </section>
        </div>
      </SheetContent>
    </Sheet>
  );
}
