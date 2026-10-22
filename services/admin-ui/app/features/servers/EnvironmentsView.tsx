import { useEffect, useState } from "react";
import { Link } from "react-router";
import { Eye, FileText, Layers, Network, Pencil, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { ApiError } from "~/lib/api";
import { deleteEnvironment, getEnvironments, type EnvironmentAsset } from "./environments";
import { environmentMapPath } from "./inventoryMap";
import { RowAction, RowActions } from "./RowAction";
import EnvironmentForm from "./EnvironmentForm";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "~/components/ui/alert-dialog";
import { Badge } from "~/components/ui/badge";
import { Button, buttonVariants } from "~/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "~/components/ui/card";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "~/components/ui/empty";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";
import { Spinner } from "~/components/ui/spinner";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

/** The environments with what is deployed in them; each one's map is the System map rooted at it. */
export default function EnvironmentsView() {
  const [data, setData] = useState<{ environments: EnvironmentAsset[] } | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [form, setForm] = useState<{ environment: EnvironmentAsset | null } | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
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
    setSelected(null);
    setNotice(null);
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
      setSelected((current) => (current === target.id ? null : current));
      setDeleteTarget(null);
      setNotice(`Environment “${target.name}” deleted.`);
      setRefresh((value) => value + 1);
    } catch (error) {
      setDeleteError(error instanceof ApiError ? error.message : "Could not delete the environment. Try again.");
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
  const detail = data?.environments.find((environment) => environment.id === selected);

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
            Add environment
          </Button>
        </div>
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertTitle>Environments unavailable</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {notice && (
        <Alert>
          <AlertDescription role="status">{notice}</AlertDescription>
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
            setSelected(environment.id);
            setNotice(`Environment “${environment.name}” saved.`);
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
                          <p className="mt-1 text-sm text-muted-foreground">
                            {environment.description || "No description"}
                          </p>
                        </TableCell>
                        <TableCell className="align-top">{environment.services.length}</TableCell>
                        <TableCell className="align-top">{environment.servers.length}</TableCell>
                        <TableCell className="align-top">
                          <RowActions>
                            <RowAction
                              label="Details"
                              name={environment.name}
                              onClick={() => setSelected(environment.id)}
                            >
                              <Eye />
                            </RowAction>
                            <RowAction label="Map" name={environment.name} to={environmentMapPath(environment.id)}>
                              <Network />
                            </RowAction>
                            <RowAction
                              label="Documents"
                              name={environment.name}
                              to={`/documents?environment_id=${environment.id}`}
                            >
                              <FileText />
                            </RowAction>
                            <RowAction
                              label="Edit"
                              name={environment.name}
                              disabled={loading || error !== null || deleting}
                              onClick={() => openForm(environment)}
                            >
                              <Pencil />
                            </RowAction>
                            <RowAction
                              label="Delete"
                              name={environment.name}
                              destructive
                              disabled={loading || error !== null || deleting}
                              onClick={() => {
                                setDeleteTarget(environment);
                                setDeleteError(null);
                                setNotice(null);
                              }}
                            >
                              <Trash2 />
                            </RowAction>
                          </RowActions>
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
      {detail && !form && (
        <Card aria-label="Environment details">
          <CardHeader>
            <div className="flex items-start justify-between gap-3">
              <CardTitle>{detail.name}</CardTitle>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Close environment details"
                onClick={() => setSelected(null)}
              >
                <X />
              </Button>
            </div>
            <CardDescription className="whitespace-pre-wrap">{detail.description || "No description"}</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-6 md:grid-cols-2">
            <section aria-label="Environment services" className="flex flex-col gap-3">
              <h3 className="font-semibold">Services</h3>
              {detail.services.length ? (
                <ul className="flex flex-wrap gap-2">
                  {detail.services.map((service) => (
                    <li key={service.id}>
                      <Badge variant="secondary">{service.name}</Badge>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted-foreground">No services deployed here.</p>
              )}
              <Link to="/services" className="text-sm underline">
                Manage services
              </Link>
            </section>
            <section aria-label="Environment servers" className="flex flex-col gap-3">
              <h3 className="font-semibold">Servers</h3>
              {detail.servers.length ? (
                <ul className="flex flex-col gap-2">
                  {detail.servers.map((server) => (
                    <li key={server.id} className="text-sm">
                      <span className="font-medium">{server.name}</span>
                      <span className="text-muted-foreground"> · {server.hostname}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted-foreground">No servers deployed here.</p>
              )}
              <Link to="/servers" className="text-sm underline">
                Manage servers
              </Link>
            </section>
          </CardContent>
        </Card>
      )}
      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open && !deleting) setDeleteTarget(null);
        }}
      >
        <AlertDialogContent aria-busy={deleting}>
          <AlertDialogHeader>
            <AlertDialogTitle className="break-words">Delete “{deleteTarget?.name}”?</AlertDialogTitle>
            <AlertDialogDescription>
              This permanently deletes the environment. An environment with deployments cannot be deleted: remove or
              move its deployments first. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {deleteError && (
            <Alert variant="destructive">
              <AlertDescription>{deleteError}</AlertDescription>
            </Alert>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className={buttonVariants({ variant: "destructive" })}
              disabled={deleting}
              onClick={(event) => {
                event.preventDefault();
                confirmDelete();
              }}
            >
              {deleting && <Spinner data-icon="inline-start" />}Delete environment
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
