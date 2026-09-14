import { useEffect, useState } from "react";
import { Layers, Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";
import { ApiError, deleteService, getEnvironments, getRepositories, getServers, getServices, type EnvironmentAsset, type Repository, type ServerAsset, type ServiceAsset } from "../lib/api";
import ServiceForm from "./ServiceForm";
import ServiceDetails from "./ServiceDetails";
import { serviceTypes, serviceRoles } from "../lib/serviceClassification";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "~/components/ui/alert-dialog";
import { Badge } from "~/components/ui/badge";
import { Button, buttonVariants } from "~/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "~/components/ui/empty";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";
import { Spinner } from "~/components/ui/spinner";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

export default function ServicesView() {
  const [data, setData] = useState<{ services: ServiceAsset[]; repositories: Repository[]; servers: ServerAsset[]; environments: EnvironmentAsset[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [form, setForm] = useState<{ service: ServiceAsset | null } | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ServiceAsset | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    Promise.all([getServices(controller.signal), getRepositories(controller.signal), getServers(controller.signal), getEnvironments(controller.signal)])
      .then(([services, repositories, servers, environments]) => {
        if (!controller.signal.aborted) { setData({ ...services, ...repositories, ...servers, ...environments }); setError(null); }
      }).catch((error) => {
        if (!controller.signal.aborted) setError(error instanceof ApiError ? error.message : "Could not load service inventory. Try refreshing.");
      }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [refresh]);

  function openForm(service: ServiceAsset | null) { setForm({ service }); setSelected(null); setNotice(null); }
  async function confirmDelete() {
    if (deleteTarget === null || deleting) return;
    const target = deleteTarget;
    setDeleting(true); setDeleteError(null);
    try {
      await deleteService(target.id, target.revision);
      setData((current) => current ? { ...current, services: current.services.filter((service) => service.id !== target.id) } : null);
      setSelected((current) => current === target.id ? null : current);
      setDeleteTarget(null); setNotice(`Service “${target.name}” deleted.`);
      setRefresh((value) => value + 1);
    } catch (error) {
      setDeleteError(error instanceof ApiError ? error.message : "Could not delete the service. Try again.");
    } finally { setDeleting(false); }
  }
  const matching = data?.services.filter((service) => [service.name, service.description, service.owner,
    serviceTypes[service.service_type], serviceRoles[service.service_role].label,
    ...service.environments.map(environment => environment.name),
    ...service.repositories.map((repo) => repo.name), ...service.deployments.flatMap((deployment) => [deployment.server.name, deployment.server.hostname, deployment.environment]),
  ].some((value) => value.toLowerCase().includes(query.trim().toLowerCase()))) ?? [];
  const detail = data?.services.find((service) => service.id === selected);

  return <div className="flex flex-col gap-6">
    <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-sm text-muted-foreground">{data ? `${data.services.length} ${data.services.length === 1 ? "service" : "services"}` : "Loading inventory…"} · Application assets</p>
      <div className="flex gap-2"><Button variant="outline" disabled={loading || form !== null} onClick={() => setRefresh((value) => value + 1)}><RefreshCw data-icon="inline-start" />Refresh</Button>
        <Button disabled={loading || error !== null || data === null || form !== null} onClick={() => openForm(null)}><Plus data-icon="inline-start" />Add service</Button></div></div>
    {error && <Alert variant="destructive"><AlertTitle>Service inventory unavailable</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
    {notice && <Alert><AlertDescription role="status">{notice}</AlertDescription></Alert>}
    {form && data && <ServiceForm key={form.service?.id ?? "new"} service={form.service} services={data.services} repositories={data.repositories} servers={data.servers} environments={data.environments}
      onClose={() => { setForm(null); setRefresh((value) => value + 1); }}
      onSaved={(id) => { setForm(null); setSelected(id); setNotice("Service saved. Its context is available for agent investigations."); setRefresh((value) => value + 1); }} />}
    {data === null && loading && <Skeleton className="h-48 w-full" />}
    {data !== null && !form && <>
      {data.services.length === 0 ? <Empty className="mt-4 border"><EmptyHeader><EmptyMedia variant="icon"><Layers /></EmptyMedia><EmptyTitle>Build your service inventory</EmptyTitle><EmptyDescription>Describe an application, link its code projects, and record where it runs.</EmptyDescription></EmptyHeader><EmptyContent><Button variant="outline" disabled={loading || error !== null} onClick={() => openForm(null)}><Plus data-icon="inline-start" />Add your first service</Button></EmptyContent></Empty> : <>
      <Input aria-label="Filter services" placeholder="Filter by service, repository, server, or environment…" value={query} onChange={(event) => setQuery(event.target.value)} />
      <div className="rounded-lg border"><Table><TableHeader><TableRow><TableHead>Service</TableHead><TableHead>Source</TableHead><TableHead>Environments</TableHead><TableHead>Deployments</TableHead><TableHead className="text-right">Actions</TableHead></TableRow></TableHeader>
        <TableBody>{matching.map((service) => <TableRow key={service.id}>
          <TableCell className="max-w-xs whitespace-normal align-top"><p className="font-medium">{service.name}</p><div className="mt-2 flex flex-wrap gap-2"><Badge variant="secondary">{serviceTypes[service.service_type]}</Badge><Badge variant="outline">{serviceRoles[service.service_role].label}</Badge></div><p className="mt-1 text-sm text-muted-foreground">{service.description || "No description yet"}</p>{service.owner && <p className="mt-2 text-xs text-muted-foreground">{service.owner}</p>}</TableCell>
          <TableCell className="whitespace-normal align-top"><div className="flex flex-col gap-2">{service.repositories.map((repo) => <span key={repo.id} className="text-sm">{repo.name}</span>)}{service.repositories.length === 0 && <span className="text-sm text-muted-foreground">Not linked</span>}</div></TableCell>
          <TableCell className="whitespace-normal align-top"><div className="flex flex-wrap gap-2">{service.environments.map(environment => <Badge key={environment.id} variant="outline">{environment.name}</Badge>)}{service.environments.length === 0 && <span className="text-sm text-muted-foreground">Not assigned</span>}</div></TableCell>
          <TableCell className="whitespace-normal align-top"><div className="flex flex-col items-start gap-2">{service.deployments.map((deployment) => <div key={deployment.id} className="flex flex-col gap-1"><Badge variant="secondary">{deployment.environment}</Badge><span className="text-xs text-muted-foreground">{deployment.server.name}</span></div>)}{service.deployments.length === 0 && <span className="text-sm text-muted-foreground">Not recorded</span>}</div></TableCell>
          <TableCell className="align-top"><div className="flex flex-wrap justify-end gap-2"><Button variant="outline" size="sm" aria-label={`View ${service.name}`} onClick={() => setSelected(service.id)}>Details</Button><Button variant="outline" size="sm" disabled={loading || error !== null} aria-label={`Edit ${service.name}`} onClick={() => openForm(service)}><Pencil data-icon="inline-start" />Edit</Button>
            <Button variant="destructive" size="sm" disabled={loading || error !== null || deleting} aria-label={`Delete ${service.name}`} onClick={() => { setDeleteTarget(service); setDeleteError(null); setNotice(null); }}><Trash2 data-icon="inline-start" />Delete</Button>
          </div></TableCell>
        </TableRow>)}{matching.length === 0 && <TableRow><TableCell colSpan={5} className="text-center">No services match this filter.</TableCell></TableRow>}</TableBody>
      </Table></div>
      </>}
    </>}
    {detail && !form && <ServiceDetails service={detail} onClose={() => setSelected(null)} />}
    <AlertDialog open={deleteTarget !== null} onOpenChange={(open) => { if (!open && !deleting) setDeleteTarget(null); }}>
      <AlertDialogContent aria-busy={deleting}>
        <AlertDialogHeader><AlertDialogTitle className="break-words">Delete “{deleteTarget?.name}”?</AlertDialogTitle>
          <AlertDialogDescription>This permanently deletes the service record, its saved deployment records, and its links to code projects and agents. Running applications, servers, and code files are not changed. This cannot be undone.</AlertDialogDescription>
        </AlertDialogHeader>
        {deleteError && <Alert variant="destructive"><AlertDescription>{deleteError}</AlertDescription></Alert>}
        <AlertDialogFooter><AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
          <AlertDialogAction className={buttonVariants({ variant: "destructive" })} disabled={deleting} onClick={(event) => { event.preventDefault(); confirmDelete(); }}>
            {deleting && <Spinner data-icon="inline-start" />}Delete service
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  </div>;
}
