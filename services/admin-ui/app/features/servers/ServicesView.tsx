import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Link } from "react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { FileText, Layers, Pencil, Plus } from "lucide-react";
import { ApiError } from "~/lib/api";
import { deleteService, getServices, type ServiceAsset } from "./services";
import RowMenu, { deleteMenuItem } from "~/components/RowMenu";
import { servicePath } from "./ServicePanel";
import { serviceTypes, serviceRoles } from "./serviceClassification";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import ConfirmRemove from "~/components/ConfirmRemove";
import DataTable, { sortHeader, sortRows, TableEmpty, type Sort } from "~/components/DataTable";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";

type ServiceSort = "name" | "deployments";
const byName = (a: ServiceAsset, b: ServiceAsset) => a.name.localeCompare(b.name);
const SERVICE_ORDER: Record<ServiceSort, (a: ServiceAsset, b: ServiceAsset) => number> = {
  name: byName,
  deployments: (a, b) => a.deployments.length - b.deployments.length || byName(a, b),
};

/** Services: a table (service with its type, role, description and owner, code projects, environments, deployments;
 * Documents, Edit and Delete in the row menu) with search and sortable headers. */
export default function ServicesView() {
  const [data, setData] = useState<{ services: ServiceAsset[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort<ServiceSort>>({ key: "name", descending: false });
  const [deleteTarget, setDeleteTarget] = useState<ServiceAsset | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    getServices(controller.signal)
      .then((services) => {
        if (!controller.signal.aborted) {
          setData(services);
          setError(null);
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setError(error instanceof ApiError ? error.message : "Could not load service inventory. Try refreshing.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [refresh]);

  async function confirmDelete() {
    if (deleteTarget === null || deleting) return;
    const target = deleteTarget;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteService(target.id, target.revision);
      setData((current) =>
        current ? { ...current, services: current.services.filter((service) => service.id !== target.id) } : null,
      );
      setDeleteTarget(null);
      toast.success(`Service “${target.name}” deleted.`);
      setRefresh((value) => value + 1);
    } catch (error) {
      setDeleteError(error instanceof ApiError ? error.message : "Could not delete the service. Try again.");
      throw error; // the dialog stays open with the reason
    } finally {
      setDeleting(false);
    }
  }
  const text = query.trim().toLowerCase();
  const shown = sortRows(
    data?.services.filter((service) =>
      [
        service.name,
        service.description,
        service.owner,
        serviceTypes[service.service_type],
        serviceRoles[service.service_role].label,
        ...service.environments.map((environment) => environment.name),
        ...service.repositories.map((repo) => repo.name),
        ...service.deployments.flatMap((deployment) => [
          deployment.server.name,
          deployment.server.hostname,
          deployment.environment,
        ]),
      ].some((value) => value.toLowerCase().includes(text)),
    ) ?? [],
    sort,
    SERVICE_ORDER,
  );
  const newService = (
    <Button asChild>
      <Link to="/services/new">
        <Plus data-icon="inline-start" />
        New service
      </Link>
    </Button>
  );
  const columns: ColumnDef<ServiceAsset, unknown>[] = [
    {
      id: "name",
      header: () => sortHeader("Service", "name", sort, setSort),
      cell: ({ row: { original: service } }) => (
        <div className="max-w-xs">
          <Link className="font-medium underline" to={servicePath(service.id)}>
            {service.name}
          </Link>
          <div className="mt-2 flex flex-wrap gap-2">
            <Badge variant="secondary">{serviceTypes[service.service_type]}</Badge>
            <Badge variant="outline">{serviceRoles[service.service_role].label}</Badge>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">{service.description || "No description yet"}</p>
          {service.owner && <p className="mt-2 text-xs text-muted-foreground">{service.owner}</p>}
        </div>
      ),
    },
    {
      id: "code",
      header: "Code projects",
      cell: ({ row: { original: service } }) => (
        <div className="flex flex-col gap-2">
          {service.repositories.map((repo) => (
            <span key={repo.id} className="text-sm">
              {repo.name}
            </span>
          ))}
          {service.repositories.length === 0 && <span className="text-sm text-muted-foreground">Not linked</span>}
        </div>
      ),
    },
    {
      id: "environments",
      header: "Environments",
      cell: ({ row: { original: service } }) => (
        <div className="flex flex-wrap gap-2">
          {service.environments.map((environment) => (
            <Badge key={environment.id} variant="outline">
              {environment.name}
            </Badge>
          ))}
          {service.environments.length === 0 && <span className="text-sm text-muted-foreground">Not assigned</span>}
        </div>
      ),
    },
    {
      id: "deployments",
      header: () => sortHeader("Deployments", "deployments", sort, setSort),
      cell: ({ row: { original: service } }) => (
        <div className="flex flex-col items-start gap-2">
          {service.deployments.map((deployment) => (
            <div key={deployment.id} className="flex flex-col gap-1">
              <Badge variant="secondary">{deployment.environment}</Badge>
              <span className="text-xs text-muted-foreground">{deployment.server.name}</span>
            </div>
          ))}
          {service.deployments.length === 0 && <span className="text-sm text-muted-foreground">Not recorded</span>}
        </div>
      ),
    },
    {
      id: "actions",
      header: () => <span className="sr-only">Actions</span>,
      cell: ({ row: { original: service } }) => (
        <div className="flex justify-end">
          <RowMenu
            name={service.name}
            items={[
              { label: "Documents", icon: FileText, to: `/documents?service_id=${service.id}` },
              { label: "Edit", icon: Pencil, to: servicePath(service.id, "edit") },
              {
                ...deleteMenuItem(() => {
                  setDeleteTarget(service);
                  setDeleteError(null);
                }),
                disabled: loading || error !== null || deleting,
              },
            ]}
          />
        </div>
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-6">
      {error && (
        <Alert variant="destructive">
          <AlertTitle>Service inventory unavailable</AlertTitle>
          <AlertDescription>
            {error}
            <Button variant="outline" size="sm" disabled={loading} onClick={() => setRefresh((value) => value + 1)}>
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      )}
      <DataTable
        columns={columns}
        data={shown}
        loading={data === null && loading}
        rowId={(service) => service.id}
        search={{ plural: "services", value: query, onChange: setQuery }}
        actions={newService}
        sort={sort}
        resetKey={`${sort.key}\n${sort.descending}`}
        empty={
          data?.services.length === 0 ? (
            <TableEmpty
              icon={Layers}
              title="Build your service inventory"
              description="Describe an application, link its code projects, and record where it runs."
            >
              {newService}
            </TableEmpty>
          ) : data ? (
            "No services match."
          ) : (
            "Could not load services."
          )
        }
      />
      <ConfirmRemove
        removal={
          deleteTarget && {
            title: `Delete “${deleteTarget.name}”?`,
            description:
              "This permanently deletes the service record, its saved deployment records, and its links to code projects and agents. Running applications, servers, and code files are not changed. This cannot be undone.",
            action: "Delete service",
            run: confirmDelete,
          }
        }
        onClose={() => {
          setDeleteTarget(null);
          setDeleteError(null);
        }}
        busy={deleting}
        error={deleteError}
      />
    </div>
  );
}
