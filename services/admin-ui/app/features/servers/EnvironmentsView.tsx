import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Link } from "react-router";
import { FileText, Layers, Network, Pencil, Plus, RefreshCw } from "lucide-react";
import { ApiError } from "~/lib/api";
import { deleteEnvironment, getEnvironments, type EnvironmentAsset } from "./environments";
import { environmentMapPath } from "./inventoryMap";
import { deleteMenuItem } from "~/components/RowMenu";
import EnvironmentForm from "./EnvironmentForm";
import { DraftBadge, DraftRowMenu } from "./ConfirmDraftSheet";
import ConfirmRemove from "~/components/ConfirmRemove";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "~/components/ui/empty";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

/** The environments with what is deployed in them; each one's map is the System map rooted at it. A draft environment
 * shows a Draft badge with Confirm and Discard. */
export default function EnvironmentsView() {
  const [data, setData] = useState<{ environments: EnvironmentAsset[] } | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
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
  const matching =
    data?.environments.filter((environment) =>
      [
        environment.name,
        environment.description,
        ...environment.services.map((service) => service.name),
        ...environment.servers.flatMap((server) => [server.name, server.hostname]),
      ].some((value) => value.toLowerCase().includes(query.trim().toLowerCase())),
    ) ?? [];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          {data
            ? `${data.environments.length} ${data.environments.length === 1 ? "environment" : "environments"}`
            : "Loading environments…"}
        </p>
        <div className="flex gap-2">
          <Button
            variant="outline"
            disabled={loading || form !== null || deleting}
            onClick={() => setRefresh((value) => value + 1)}
          >
            <RefreshCw data-icon="inline-start" />
            Refresh
          </Button>
          <Button
            disabled={loading || error !== null || data === null || form !== null || deleting}
            onClick={() => openForm(null)}
          >
            <Plus data-icon="inline-start" />
            New environment
          </Button>
        </div>
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertTitle>Environments unavailable</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
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
      {data === null && loading && <Skeleton className="h-48 w-full" />}
      {data && !form && (
        <>
          {data.environments.length === 0 ? (
            <Empty className="border">
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <Layers />
                </EmptyMedia>
                <EmptyTitle>No environments yet</EmptyTitle>
                <EmptyDescription>
                  Add environments such as Development, Staging, or Production, then deploy services into them.
                </EmptyDescription>
              </EmptyHeader>
              <EmptyContent>
                <Button variant="outline" disabled={loading || error !== null} onClick={() => openForm(null)}>
                  Add your first environment
                </Button>
              </EmptyContent>
            </Empty>
          ) : (
            <div className="flex flex-col gap-4">
              <Input
                aria-label="Filter environments"
                placeholder="Filter by environment, service, server, or hostname…"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
              <div className="rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Environment</TableHead>
                      <TableHead>Services</TableHead>
                      <TableHead>Servers</TableHead>
                      <TableHead className="text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {matching.map((environment) => (
                      <TableRow key={environment.id}>
                        <TableCell className="max-w-sm whitespace-normal align-top">
                          <Link
                            className="font-medium underline"
                            to={`/environments/${encodeURIComponent(environment.id)}`}
                          >
                            {environment.name}
                          </Link>
                          {environment.status === "draft" && (
                            <span className="ml-2 inline-flex align-middle">
                              <DraftBadge />
                            </span>
                          )}
                          <p className="mt-1 text-sm text-muted-foreground">
                            {environment.description || "No description"}
                          </p>
                        </TableCell>
                        <TableCell className="align-top">{environment.services.length}</TableCell>
                        <TableCell className="align-top">{environment.servers.length}</TableCell>
                        <TableCell className="text-right align-top">
                          <DraftRowMenu
                            name={environment.name}
                            draft={
                              environment.status === "draft"
                                ? {
                                    confirm: {
                                      kind: "environment",
                                      id: environment.id,
                                      revision: environment.revision,
                                    },
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
                        </TableCell>
                      </TableRow>
                    ))}
                    {matching.length === 0 && (
                      <TableRow>
                        <TableCell colSpan={4} className="text-center">
                          No environments match this filter.
                        </TableCell>
                      </TableRow>
                    )}
                  </TableBody>
                </Table>
              </div>
            </div>
          )}
        </>
      )}
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
