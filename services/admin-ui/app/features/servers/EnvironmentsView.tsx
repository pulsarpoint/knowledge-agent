import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Link } from "react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { FileText, Layers, Network, Pencil, Plus } from "lucide-react";
import { ApiError } from "~/lib/api";
import { deleteEnvironment, getEnvironments, type EnvironmentAsset } from "./environments";
import { environmentMapPath } from "./inventoryMap";
import { deleteMenuItem } from "~/components/RowMenu";
import EnvironmentForm from "./EnvironmentForm";
import { DraftBadge, DraftRowMenu } from "./ConfirmDraftSheet";
import ConfirmRemove from "~/components/ConfirmRemove";
import DataTable, { sortHeader, sortRows, TableEmpty, type Sort } from "~/components/DataTable";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";

type EnvironmentSort = "name" | "services" | "servers";
const byName = (a: EnvironmentAsset, b: EnvironmentAsset) => a.name.localeCompare(b.name);
const ENVIRONMENT_ORDER: Record<EnvironmentSort, (a: EnvironmentAsset, b: EnvironmentAsset) => number> = {
  name: byName,
  services: (a, b) => a.services.length - b.services.length || byName(a, b),
  servers: (a, b) => a.servers.length - b.servers.length || byName(a, b),
};

/** The environments with what is deployed in them, in a table with search and sortable headers; each one's map is the
 * System map rooted at it. A draft environment shows a Draft badge with Confirm and Discard. New environment and Edit
 * open a right Sheet. */
export default function EnvironmentsView() {
  const [data, setData] = useState<{ environments: EnvironmentAsset[] } | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort<EnvironmentSort>>({ key: "name", descending: false });
  const [form, setForm] = useState<{ environment: EnvironmentAsset | null } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<EnvironmentAsset | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    getEnvironments(controller.signal)
      .then((environments) => {
        if (!controller.signal.aborted) {
          setData(environments);
          setError(null);
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setError(error instanceof ApiError ? error.message : "Could not load environments. Try refreshing.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [refresh]);

  function openForm(environment: EnvironmentAsset | null) {
    setForm({ environment });
  }
  async function confirmDelete() {
    if (deleteTarget === null || deleting) return;
    const target = deleteTarget;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteEnvironment(target.id, target.revision);
      setData((current) =>
        current
          ? { ...current, environments: current.environments.filter((environment) => environment.id !== target.id) }
          : null,
      );
      setDeleteTarget(null);
      toast.success(`Environment “${target.name}” deleted.`);
      setRefresh((value) => value + 1);
    } catch (error) {
      setDeleteError(error instanceof ApiError ? error.message : "Could not delete the environment. Try again.");
      throw error; // the dialog stays open with the reason
    } finally {
      setDeleting(false);
    }
  }
  const text = query.trim().toLowerCase();
  const shown = sortRows(
    data?.environments.filter((environment) =>
      [
        environment.name,
        environment.description,
        ...environment.services.map((service) => service.name),
        ...environment.servers.flatMap((server) => [server.name, server.hostname]),
      ].some((value) => value.toLowerCase().includes(text)),
    ) ?? [],
    sort,
    ENVIRONMENT_ORDER,
  );
  const newEnvironment = (
    <Button
      disabled={loading || error !== null || data === null || form !== null || deleting}
      onClick={() => openForm(null)}
    >
      <Plus data-icon="inline-start" />
      New environment
    </Button>
  );
  const columns: ColumnDef<EnvironmentAsset, unknown>[] = [
    {
      id: "name",
      header: () => sortHeader("Environment", "name", sort, setSort),
      cell: ({ row: { original: environment } }) => (
        <div className="max-w-sm">
          <Link className="font-medium underline" to={`/environments/${encodeURIComponent(environment.id)}`}>
            {environment.name}
          </Link>
          {environment.status === "draft" && (
            <span className="ml-2 inline-flex align-middle">
              <DraftBadge />
            </span>
          )}
          <p className="mt-1 text-sm text-muted-foreground">{environment.description || "No description"}</p>
        </div>
      ),
    },
    {
      id: "services",
      header: () => sortHeader("Services", "services", sort, setSort),
      cell: ({ row: { original: environment } }) => environment.services.length,
    },
    {
      id: "servers",
      header: () => sortHeader("Servers", "servers", sort, setSort),
      cell: ({ row: { original: environment } }) => environment.servers.length,
    },
    {
      id: "actions",
      header: () => <span className="sr-only">Actions</span>,
      cell: ({ row: { original: environment } }) => (
        <div className="flex justify-end">
          <DraftRowMenu
            name={environment.name}
            draft={
              environment.status === "draft"
                ? {
                    confirm: { kind: "environment", id: environment.id, revision: environment.revision },
                    onDone: () => setRefresh((value) => value + 1),
                  }
                : undefined
            }
            items={[
              { label: "Map", icon: Network, to: environmentMapPath(environment.id) },
              { label: "Documents", icon: FileText, to: `/documents?environment_id=${environment.id}` },
              {
                label: "Edit",
                icon: Pencil,
                disabled: loading || error !== null || deleting,
                onSelect: () => openForm(environment),
              },
              {
                ...deleteMenuItem(() => {
                  setDeleteTarget(environment);
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
      <div className="flex justify-end gap-2">{newEnvironment}</div>
      {error && (
        <Alert variant="destructive">
          <AlertTitle>Environments unavailable</AlertTitle>
          <AlertDescription>
            {error}
            <Button variant="outline" size="sm" disabled={loading} onClick={() => setRefresh((value) => value + 1)}>
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {form && data && (
        <EnvironmentForm
          key={form.environment?.id ?? "new"}
          environment={form.environment}
          onClose={() => {
            setForm(null);
            setRefresh((value) => value + 1);
          }}
          onSaved={(environment) => {
            setForm(null);
            toast.success(`Environment “${environment.name}” saved.`);
            setRefresh((value) => value + 1);
          }}
        />
      )}
      <DataTable
        columns={columns}
        data={shown}
        loading={data === null && loading}
        rowId={(environment) => environment.id}
        search={{ plural: "environments", value: query, onChange: setQuery }}
        sort={sort}
        resetKey={`${sort.key}\n${sort.descending}`}
        empty={
          data?.environments.length === 0 ? (
            <TableEmpty
              icon={Layers}
              title="No environments yet"
              description="Add environments such as Development, Staging, or Production, then deploy services into them."
            >
              {newEnvironment}
            </TableEmpty>
          ) : data ? (
            "No environments match."
          ) : (
            "Could not load environments."
          )
        }
      />
      <ConfirmRemove
        removal={
          deleteTarget && {
            title: `Delete “${deleteTarget.name}”?`,
            description:
              "This permanently deletes the environment. An environment with deployments cannot be deleted: remove or move its deployments first. This cannot be undone.",
            action: "Delete environment",
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
