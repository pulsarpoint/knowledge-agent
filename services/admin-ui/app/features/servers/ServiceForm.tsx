import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { Plus, Server, Trash2, X } from "lucide-react";
import { ApiError } from "~/lib/api";
import { type ServiceAsset } from "./services";
import { type EnvironmentAsset } from "./environments";
import { type Repository } from "~/features/codeProjects/api";
import { type ServerAsset } from "./api";
import {
  afterChange,
  applyChange,
  createItem,
  deploymentChanges,
  getServiceDeployments,
  getServiceRelations,
  relationChanges,
  updateItem,
  type Deployment,
  type DeploymentRow,
  type Relation,
  type RelationKind,
  type RelationValue,
} from "./inventory";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Skeleton } from "~/components/ui/skeleton";
import ServerForm from "./ServerForm";
import ServiceDependencyFields, { type RelatedService } from "./ServiceDependencyFields";
import { serviceTypes, serviceRoles, type ServiceType, type ServiceRole } from "./serviceClassification";
import { Spinner } from "~/components/ui/spinner";
import { Textarea } from "~/components/ui/textarea";

/** The relation lists the form edits: [kind, this service is the "from" end]. */
const RELATION_LISTS = {
  dependsOn: ["depends_on", true],
  usedBy: ["depends_on", false],
  partOf: ["part_of", true],
  parts: ["part_of", false],
} as const satisfies Record<string, readonly [RelationKind, boolean]>;
type RelationList = keyof typeof RELATION_LISTS;
type RelationLists = Record<RelationList, RelatedService[]>;

function relationLists(serviceId: string, relations: Relation[]): RelationLists {
  const list = ([kind, outgoing]: readonly [RelationKind, boolean]) =>
    relations
      .filter((r) => r.kind === kind && (outgoing ? r.from_service_id : r.to_service_id) === serviceId)
      .map((r) => ({ service_id: (outgoing ? r.to_service_id : r.from_service_id)!, description: r.description }));
  return {
    dependsOn: list(RELATION_LISTS.dependsOn),
    usedBy: list(RELATION_LISTS.usedBy),
    partOf: list(RELATION_LISTS.partOf),
    parts: list(RELATION_LISTS.parts),
  };
}

/** The relations the form wants for the service: the four lists and a built_from per selected code project (a kept
 * one keeps its description). */
function wantedRelations(
  serviceId: string,
  lists: RelationLists,
  repositoryIds: string[],
  saved: Relation[],
): RelationValue[] {
  const between = (kind: RelationKind, from: string, to: string, description: string): RelationValue => ({
    from_service_id: from,
    kind,
    to_service_id: to,
    to_code_project_id: null,
    description,
  });
  return [
    ...(Object.keys(RELATION_LISTS) as RelationList[]).flatMap((name) => {
      const [kind, outgoing] = RELATION_LISTS[name];
      return lists[name].map((item) =>
        outgoing
          ? between(kind, serviceId, item.service_id, item.description)
          : between(kind, item.service_id, serviceId, item.description),
      );
    }),
    ...repositoryIds.map((id) => ({
      from_service_id: serviceId,
      kind: "built_from" as const,
      to_service_id: null,
      to_code_project_id: id,
      description:
        saved.find((r) => r.kind === "built_from" && r.from_service_id === serviceId && r.to_code_project_id === id)
          ?.description ?? "",
    })),
  ];
}

const deploymentRow = ({
  id,
  server_id,
  environment_id,
  runtime,
  runtime_name,
  port,
  log_location,
  deployed_version,
}: Deployment): DeploymentRow => ({
  key: id,
  id,
  server_id,
  environment_id,
  runtime,
  runtime_name,
  port,
  log_location,
  deployed_version,
});

/** A service's details, deployments and relations. Saving writes the service, then each changed deployment and
 * relation through the inventory API; if a write fails the form stays open with what was saved so far, so saving again
 * applies only the rest. */
export default function ServiceForm({
  service,
  services,
  repositories,
  environments,
  servers: initialServers,
  onSaved,
  onClose,
}: {
  service: ServiceAsset | null;
  services: ServiceAsset[];
  repositories: Repository[];
  servers: ServerAsset[];
  environments: EnvironmentAsset[];
  onSaved: (id: string) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(service?.name ?? "");
  const [serviceType, setServiceType] = useState<ServiceType>(service?.service_type ?? "custom");
  const [serviceRole, setServiceRole] = useState<ServiceRole>(service?.service_role ?? "application");
  const [description, setDescription] = useState(service?.description ?? "");
  const [owner, setOwner] = useState(service?.owner ?? "");
  // What is saved: the service, its deployments and relations (null while they load).
  const [saved, setSaved] = useState(service ? { id: service.id, revision: service.revision } : null);
  const [savedDeployments, setSavedDeployments] = useState<Deployment[] | null>(service ? null : []);
  const [savedRelations, setSavedRelations] = useState<Relation[] | null>(service ? null : []);
  // What the form holds.
  const [repositoryIds, setRepositoryIds] = useState<string[]>([]);
  const [lists, setLists] = useState<RelationLists>({ dependsOn: [], usedBy: [], partOf: [], parts: [] });
  const [deployments, setDeployments] = useState<DeploymentRow[]>([]);
  const [servers, setServers] = useState(initialServers);
  const [serverForm, setServerForm] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    if (!service) return;
    const controller = new AbortController();
    Promise.all([
      getServiceDeployments(service.id, controller.signal),
      getServiceRelations(service.id, controller.signal),
    ])
      .then(([{ deployments }, { relations }]) => {
        if (controller.signal.aborted) return;
        setSavedDeployments(deployments);
        setSavedRelations(relations);
        setDeployments(deployments.map(deploymentRow));
        setLists(relationLists(service.id, relations));
        setRepositoryIds(
          relations.flatMap((r) =>
            r.kind === "built_from" && r.from_service_id === service.id ? [r.to_code_project_id!] : [],
          ),
        );
      })
      .catch((reason) => {
        if (!controller.signal.aborted)
          setLoadError(
            reason instanceof ApiError ? reason.message : "Could not load this service's deployments and relations.",
          );
      });
    return () => controller.abort();
  }, [service?.id]);

  function addDeployment(serverId = "") {
    setDeployments((rows) => [
      ...rows,
      {
        key: crypto.randomUUID(),
        server_id: serverId,
        environment_id: environments.length === 1 ? environments[0].id : "",
        runtime: "",
        runtime_name: "",
        port: null,
        log_location: "",
        deployed_version: "",
      },
    ]);
  }
  function updateDeployment(key: string, changes: Partial<DeploymentRow>) {
    setDeployments((rows) => rows.map((row) => (row.key === key ? { ...row, ...changes } : row)));
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (savedDeployments === null || savedRelations === null) return;
    setError(null);
    if (deployments.some((deployment) => !deployment.server_id)) {
      setError("Choose a server for each deployment.");
      return;
    }
    if (deployments.some((deployment) => !deployment.environment_id)) {
      setError("Choose an environment for each deployment.");
      return;
    }
    setSaving(true);
    let rows = deployments;
    let savedNow = savedDeployments;
    let relationsNow = savedRelations;
    try {
      const fields = { name, service_type: serviceType, service_role: serviceRole, description, owner };
      const item = saved
        ? await updateItem<{ id: string; revision: number }>("services", saved.id, saved.revision, fields)
        : await createItem<{ id: string; revision: number }>("services", fields);
      setSaved({ id: item.id, revision: item.revision });
      for (const change of deploymentChanges(item.id, savedNow, rows)) {
        const result = await applyChange<Deployment>(change);
        savedNow = afterChange(savedNow, change, result);
        setSavedDeployments(savedNow);
        if (change.key) {
          rows = rows.map((row) => (row.key === change.key ? { ...row, id: result.id } : row));
          setDeployments(rows);
        }
      }
      for (const change of relationChanges(
        relationsNow,
        wantedRelations(item.id, lists, repositoryIds, relationsNow),
      )) {
        relationsNow = afterChange(relationsNow, change, await applyChange<Relation>(change));
        setSavedRelations(relationsNow);
      }
      onSaved(item.id);
    } catch (error) {
      setError(error instanceof ApiError ? error.message : "Could not save this service. Try again.");
    } finally {
      setSaving(false);
    }
  }
  const others = services.filter((other) => other.id !== saved?.id);
  const current = name.trim() || "this service";
  const relationFields: { list: RelationList; title: string; help: string; relationship: (other: string) => string }[] =
    [
      {
        list: "dependsOn",
        title: "Depends on",
        help: "Services this service needs to operate.",
        relationship: (other) => `${current} depends on ${other}`,
      },
      {
        list: "usedBy",
        title: "Used by",
        help: "Services that depend on this service.",
        relationship: (other) => `${other} depends on ${current}`,
      },
      {
        list: "partOf",
        title: "Part of",
        help: "Services this one is a component or library of.",
        relationship: (other) => `${current} is part of ${other}`,
      },
      {
        list: "parts",
        title: "Parts",
        help: "Components or libraries of this service.",
        relationship: (other) => `${other} is part of ${current}`,
      },
    ];

  if (savedDeployments === null || savedRelations === null)
    return loadError ? (
      <div className="flex flex-col gap-3">
        <Alert variant="destructive">
          <AlertTitle>Service could not be opened</AlertTitle>
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
        <Button type="button" variant="outline" className="self-start" onClick={onClose}>
          Close
        </Button>
      </div>
    ) : (
      <Skeleton className="h-60 w-full" />
    );
  return (
    <>
      <form onSubmit={submit} className="flex flex-col gap-6 rounded-lg border p-6">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">{service ? "Edit service" : "Add service"}</h2>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Close service form"
            disabled={saving}
            onClick={onClose}
          >
            <X />
          </Button>
        </div>
        {error && (
          <Alert variant="destructive">
            <AlertTitle>Service could not be saved</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <FieldSet disabled={saving}>
          <FieldLegend>Application details</FieldLegend>
          <FieldGroup className="grid gap-5 md:grid-cols-2">
            <Field>
              <FieldLabel htmlFor="service-name">Service name</FieldLabel>
              <Input
                id="service-name"
                required
                maxLength={200}
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Payments API"
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="service-owner">Owner or team (optional)</FieldLabel>
              <Input
                id="service-owner"
                maxLength={200}
                value={owner}
                onChange={(event) => setOwner(event.target.value)}
                placeholder="Payments team"
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="service-type">Service type</FieldLabel>
              <Select
                value={serviceType}
                onValueChange={(value) => setServiceType(value as ServiceType)}
                disabled={saving}
              >
                <SelectTrigger id="service-type">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    {Object.entries(serviceTypes).map(([value, label]) => (
                      <SelectItem key={value} value={value}>
                        {label}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
              <FieldDescription>
                Custom: software we build. Built-in: software such as Nginx or PostgreSQL. External: a service operated
                outside this system.
              </FieldDescription>
            </Field>
            <Field>
              <FieldLabel htmlFor="service-role">Primary role</FieldLabel>
              <Select
                value={serviceRole}
                onValueChange={(value) => setServiceRole(value as ServiceRole)}
                disabled={saving}
              >
                <SelectTrigger id="service-role">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    {Object.entries(serviceRoles).map(([value, role]) => (
                      <SelectItem key={value} value={value}>
                        {role.label}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
              <FieldDescription>What it does in the architecture. This determines the graph color.</FieldDescription>
            </Field>
            <Field className="md:col-span-2">
              <FieldLabel htmlFor="service-description">Purpose (optional)</FieldLabel>
              <Textarea
                id="service-description"
                maxLength={4000}
                rows={2}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="What this application does and what depends on it."
              />
            </Field>
          </FieldGroup>
        </FieldSet>
        <FieldSet disabled={saving}>
          <FieldLegend>Code projects</FieldLegend>
          <FieldDescription>
            Select all code projects used by this service. Code projects can be shared between services.
          </FieldDescription>
          {repositories.length === 0 ? (
            <Alert>
              <AlertDescription>
                Add source code on the{" "}
                <Link to={`/code-projects`} className="underline">
                  Code projects page
                </Link>
                . You can save the service first and link code projects later.
              </AlertDescription>
            </Alert>
          ) : (
            <FieldGroup className="max-h-60 gap-3 overflow-y-auto">
              {repositories.map((repo) => (
                <Field key={repo.id} orientation="horizontal">
                  <Checkbox
                    id={`service-repo-${repo.id}`}
                    checked={repositoryIds.includes(repo.id)}
                    disabled={saving}
                    onCheckedChange={(checked) =>
                      setRepositoryIds((ids) =>
                        checked === true ? [...ids, repo.id] : ids.filter((id) => id !== repo.id),
                      )
                    }
                  />
                  <FieldContent>
                    <FieldLabel htmlFor={`service-repo-${repo.id}`}>{repo.name}</FieldLabel>
                    <FieldDescription>{repo.remote_url?.replace(/\.git$/, "") ?? "Local folders"}</FieldDescription>
                  </FieldContent>
                </Field>
              ))}
            </FieldGroup>
          )}
          {service?.repositories
            .filter((repo) => repo.archived)
            .map((repo) => (
              <Field key={repo.id} orientation="horizontal">
                <Checkbox
                  id={`archived-repo-${repo.id}`}
                  checked={repositoryIds.includes(repo.id)}
                  disabled={saving}
                  onCheckedChange={(checked) =>
                    setRepositoryIds((ids) =>
                      checked === true ? [...ids, repo.id] : ids.filter((id) => id !== repo.id),
                    )
                  }
                />
                <FieldLabel htmlFor={`archived-repo-${repo.id}`}>
                  {repo.name} (archived — unlink before saving)
                </FieldLabel>
              </Field>
            ))}
        </FieldSet>
        <FieldSet disabled={saving}>
          <FieldLegend>Service relationships</FieldLegend>
          <FieldDescription>
            Logical relations between services, in either direction. Network connections between deployments are edited
            on the System map.
          </FieldDescription>
          <FieldGroup className="grid gap-6 md:grid-cols-2">
            {relationFields.map(({ list, title, help, relationship }) => (
              <ServiceDependencyFields
                key={list}
                name={list}
                title={title}
                help={help}
                relationship={relationship}
                services={others}
                value={lists[list]}
                onChange={(value) => setLists((current) => ({ ...current, [list]: value }))}
                disabled={saving}
              />
            ))}
          </FieldGroup>
        </FieldSet>
        <FieldSet disabled={saving}>
          <FieldLegend>Where it runs</FieldLegend>
          <FieldDescription>
            Add a deployment for each server and environment it runs in: a service and a server are in an environment
            through their deployments. Removing a saved deployment also removes its network connections. Leave empty if
            deployment details are not known yet.
          </FieldDescription>
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" size="sm" disabled={saving} onClick={() => addDeployment()}>
              <Plus data-icon="inline-start" />
              Add deployment
            </Button>
            <Button type="button" variant="outline" size="sm" disabled={saving} onClick={() => setServerForm(true)}>
              <Server data-icon="inline-start" />
              Register server
            </Button>
          </div>
          {deployments.map((deployment, index) => (
            <FieldSet key={deployment.key} className="rounded-lg border p-4" disabled={saving}>
              <FieldLegend>Deployment {index + 1}</FieldLegend>
              <FieldGroup className="grid gap-5 md:grid-cols-2">
                <Field data-invalid={error !== null && !deployment.server_id}>
                  <FieldLabel htmlFor={`deployment-${deployment.key}-server`}>Server</FieldLabel>
                  <Select
                    value={deployment.server_id}
                    onValueChange={(server_id) => updateDeployment(deployment.key, { server_id })}
                    disabled={saving}
                  >
                    <SelectTrigger
                      id={`deployment-${deployment.key}-server`}
                      aria-invalid={error !== null && !deployment.server_id}
                    >
                      <SelectValue placeholder="Choose a server" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectGroup>
                        {servers.map((server) => (
                          <SelectItem key={server.id} value={server.id}>
                            {server.name} · {server.hostname}
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    </SelectContent>
                  </Select>
                  {servers.length === 0 && (
                    <FieldDescription>Use Register server to add the first host.</FieldDescription>
                  )}
                </Field>
                <Field data-invalid={error !== null && !deployment.environment_id}>
                  <FieldLabel htmlFor={`deployment-${deployment.key}-environment`}>Environment</FieldLabel>
                  <Select
                    value={deployment.environment_id}
                    disabled={saving || environments.length === 0}
                    onValueChange={(environment_id) => updateDeployment(deployment.key, { environment_id })}
                  >
                    <SelectTrigger
                      id={`deployment-${deployment.key}-environment`}
                      aria-invalid={error !== null && !deployment.environment_id}
                    >
                      <SelectValue placeholder="Choose an environment" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectGroup>
                        {environments.map((environment) => (
                          <SelectItem key={environment.id} value={environment.id}>
                            {environment.name}
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    </SelectContent>
                  </Select>
                  {environments.length === 0 && (
                    <FieldDescription>
                      Create an environment on the{" "}
                      <Link to="/environments" className="underline">
                        Environments page
                      </Link>{" "}
                      first.
                    </FieldDescription>
                  )}
                </Field>
                <Field>
                  <FieldLabel htmlFor={`deployment-${deployment.key}-runtime`}>
                    Runtime or manager (optional)
                  </FieldLabel>
                  <Input
                    id={`deployment-${deployment.key}-runtime`}
                    maxLength={80}
                    value={deployment.runtime}
                    onChange={(event) => updateDeployment(deployment.key, { runtime: event.target.value })}
                    placeholder="Docker, Compose, systemd, Kubernetes…"
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor={`deployment-${deployment.key}-name`}>
                    Container, unit, or workload name (optional)
                  </FieldLabel>
                  <Input
                    id={`deployment-${deployment.key}-name`}
                    maxLength={200}
                    value={deployment.runtime_name}
                    onChange={(event) => updateDeployment(deployment.key, { runtime_name: event.target.value })}
                    placeholder="payments-api.service"
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor={`deployment-${deployment.key}-port`}>Application port (optional)</FieldLabel>
                  <Input
                    id={`deployment-${deployment.key}-port`}
                    type="number"
                    min={1}
                    max={65535}
                    step={1}
                    value={deployment.port ?? ""}
                    onChange={(event) =>
                      updateDeployment(deployment.key, {
                        port: event.target.value === "" ? null : Number(event.target.value),
                      })
                    }
                    placeholder="8080"
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor={`deployment-${deployment.key}-version`}>Deployed version (optional)</FieldLabel>
                  <Input
                    id={`deployment-${deployment.key}-version`}
                    maxLength={200}
                    value={deployment.deployed_version}
                    onChange={(event) => updateDeployment(deployment.key, { deployed_version: event.target.value })}
                    placeholder="Image tag, release, or deployed commit"
                  />
                </Field>
                <Field className="md:col-span-2">
                  <FieldLabel htmlFor={`deployment-${deployment.key}-logs`}>Log location (optional)</FieldLabel>
                  <Input
                    id={`deployment-${deployment.key}-logs`}
                    maxLength={1000}
                    value={deployment.log_location}
                    onChange={(event) => updateDeployment(deployment.key, { log_location: event.target.value })}
                    placeholder="/var/log/payments/app.log or container stdout"
                  />
                </Field>
              </FieldGroup>
              <div>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={saving}
                  onClick={() => setDeployments((rows) => rows.filter((row) => row.key !== deployment.key))}
                >
                  <Trash2 data-icon="inline-start" />
                  Remove deployment {index + 1}
                </Button>
              </div>
            </FieldSet>
          ))}
        </FieldSet>
        <div className="flex gap-2">
          <Button type="submit" disabled={saving}>
            {saving && <Spinner data-icon="inline-start" />}
            {service ? "Save changes" : "Save service"}
          </Button>
          <Button type="button" variant="outline" disabled={saving} onClick={onClose}>
            Cancel
          </Button>
        </div>
      </form>
      {serverForm && (
        <ServerForm
          onClose={() => setServerForm(false)}
          onSaved={(server) => {
            setServers((rows) => [...rows, server]);
            const unassigned = deployments.find((deployment) => !deployment.server_id);
            if (unassigned) updateDeployment(unassigned.key, { server_id: server.id });
            else addDeployment(server.id);
            setServerForm(false);
          }}
        />
      )}
    </>
  );
}
