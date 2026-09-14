import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { Layers, Network, Pencil, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { ApiError, deleteEnvironment, getEnvironments, getServices, getServers, type EnvironmentAsset, type ServiceAsset, type ServerAsset } from "../lib/api";
import EnvironmentForm from "./EnvironmentForm";
import ServiceDependencyGraph from "./ServiceDependencyGraph";
import ServiceDetails from "./ServiceDetails";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "~/components/ui/alert-dialog";
import { Badge } from "~/components/ui/badge";
import { Button, buttonVariants } from "~/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "~/components/ui/card";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "~/components/ui/empty";
import { Input } from "~/components/ui/input";
import { Field, FieldLabel } from "~/components/ui/field";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Skeleton } from "~/components/ui/skeleton";
import { Spinner } from "~/components/ui/spinner";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";

export default function EnvironmentsView() {
  const [params, setParams] = useSearchParams();
  const view = params.get("view") === "graph" ? "graph" : "list";
  const environmentId = params.get("environment_id");
  const [selectedService, setSelectedService] = useState<string | null>(null);
  const [data, setData] = useState<{ environments: EnvironmentAsset[]; services: ServiceAsset[]; servers: ServerAsset[] } | null>(null);
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
    Promise.all([getEnvironments(controller.signal), getServices(controller.signal), getServers(controller.signal)])
      .then(([environments, services, servers]) => {
        if (!controller.signal.aborted) { setData({ ...environments, ...services, ...servers }); setError(null); }
      }).catch(error => {
        if (!controller.signal.aborted) setError(error instanceof ApiError ? error.message : "Could not load environments. Try refreshing.");
      }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [refresh]);

  function openForm(environment: EnvironmentAsset | null) { setForm({ environment }); setSelected(null); setNotice(null); }
  function openGraph(environmentId: string) {
    setParams(previous => {
      const next = new URLSearchParams(previous);
      next.set("view", "graph"); next.set("environment_id", environmentId);
      return next;
    });
    setSelectedService(null); setSelected(null);
  }
  async function confirmDelete() {
    if (deleteTarget === null || deleting) return;
    const target = deleteTarget;
    setDeleting(true); setDeleteError(null);
    try {
      await deleteEnvironment(target.id, target.revision);
      setData(current => current ? { ...current, environments: current.environments.filter(environment => environment.id !== target.id) } : null);
      setSelected(current => current === target.id ? null : current);
      setDeleteTarget(null); setNotice(`Environment “${target.name}” deleted.`);
      setRefresh(value => value + 1);
    } catch (error) { setDeleteError(error instanceof ApiError ? error.message : "Could not delete the environment. Try again."); }
    finally { setDeleting(false); }
  }
  const matching = data?.environments.filter(environment => [environment.name, environment.description,
    ...environment.services.map(service => service.name), ...environment.servers.flatMap(server => [server.name, server.hostname]),
  ].some(value => value.toLowerCase().includes(query.trim().toLowerCase()))) ?? [];
  const detail = data?.environments.find(environment => environment.id === selected);
  const graphEnvironment = data?.environments.find(environment => environment.id === environmentId);
  const memberIds = new Set(graphEnvironment?.services.map(service => service.id));
  const graphServices = data?.services.filter(service => memberIds.has(service.id)) ?? [];
  const serviceDetail = graphServices.find(service => service.id === selectedService);

  return <div className="flex flex-col gap-6">
    <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-sm text-muted-foreground">{data ? `${data.environments.length} ${data.environments.length === 1 ? "environment" : "environments"}` : "Loading environments…"}</p>
      <div className="flex gap-2"><Button variant="outline" disabled={loading || form !== null || deleting} onClick={() => setRefresh(value => value + 1)}><RefreshCw data-icon="inline-start" />Refresh</Button>
        <Button disabled={loading || error !== null || data === null || form !== null || deleting} onClick={() => openForm(null)}><Plus data-icon="inline-start" />Add environment</Button></div></div>
    {error && <Alert variant="destructive"><AlertTitle>Environments unavailable</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
    {notice && <Alert><AlertDescription role="status">{notice}</AlertDescription></Alert>}
    {form && data && <EnvironmentForm key={form.environment?.id ?? "new"} environment={form.environment} services={data.services} servers={data.servers}
      onClose={() => { setForm(null); setRefresh(value => value + 1); }}
      onSaved={environment => {
        setData(current => current ? { ...current, environments: [...current.environments.filter(item => item.id !== environment.id), environment] } : null);
        setForm(null); if (view === "graph") openGraph(environment.id); else setSelected(environment.id);
        setNotice(`Environment “${environment.name}” saved.`); setRefresh(value => value + 1);
      }} />}
    {data === null && loading && <Skeleton className="h-48 w-full" />}
    {data && !form && <Tabs value={view} onValueChange={value => {
      setParams(previous => { const next = new URLSearchParams(previous); if (value === "graph") next.set("view", "graph"); else next.delete("view"); return next; });
      setSelectedService(null);
    }} className="min-w-0">
      <TabsList aria-label="Environment views"><TabsTrigger value="list">Environment list</TabsTrigger><TabsTrigger value="graph">Dependency graph</TabsTrigger></TabsList>
      {data.environments.length === 0 ? <Empty className="border"><EmptyHeader><EmptyMedia variant="icon"><Layers /></EmptyMedia><EmptyTitle>No environments yet</EmptyTitle><EmptyDescription>Group your services and servers into environments such as Development, Staging, or Production.</EmptyDescription></EmptyHeader><EmptyContent><Button variant="outline" disabled={loading || error !== null} onClick={() => openForm(null)}>Add your first environment</Button></EmptyContent></Empty> : <>
      <TabsContent value="list" className="flex flex-col gap-4 data-[state=inactive]:hidden">
      <Input aria-label="Filter environments" placeholder="Filter by environment, service, server, or hostname…" value={query} onChange={event => setQuery(event.target.value)} />
      <div className="rounded-lg border"><Table><TableHeader><TableRow><TableHead>Environment</TableHead><TableHead>Services</TableHead><TableHead>Servers</TableHead><TableHead className="text-right">Actions</TableHead></TableRow></TableHeader>
        <TableBody>{matching.map(environment => <TableRow key={environment.id}>
          <TableCell className="max-w-sm whitespace-normal align-top"><Button variant="link" className="h-auto whitespace-normal p-0 text-left" aria-label={`View ${environment.name}`} onClick={() => setSelected(environment.id)}>{environment.name}</Button><p className="mt-1 text-sm text-muted-foreground">{environment.description || "No description"}</p></TableCell>
          <TableCell className="align-top">{environment.services.length}</TableCell><TableCell className="align-top">{environment.servers.length}</TableCell>
          <TableCell className="align-top"><div className="flex flex-wrap justify-end gap-2"><Button variant="outline" size="sm" aria-label={`View ${environment.name} graph`} onClick={() => openGraph(environment.id)}><Network data-icon="inline-start" />Graph</Button><Button asChild variant="outline" size="sm"><Link to={`/documents?environment_id=${environment.id}`}>Documents</Link></Button><Button variant="outline" size="sm" disabled={loading || error !== null || deleting} aria-label={`Edit ${environment.name}`} onClick={() => openForm(environment)}><Pencil data-icon="inline-start" />Edit</Button>
            <Button variant="destructive" size="sm" disabled={loading || error !== null || deleting} aria-label={`Delete ${environment.name}`} onClick={() => { setDeleteTarget(environment); setDeleteError(null); setNotice(null); }}><Trash2 data-icon="inline-start" />Delete</Button></div></TableCell>
        </TableRow>)}{matching.length === 0 && <TableRow><TableCell colSpan={4} className="text-center">No environments match this filter.</TableCell></TableRow>}</TableBody>
      </Table></div>
      </TabsContent>
      <TabsContent value="graph" className="flex flex-col gap-6">
        <Field className="max-w-sm"><FieldLabel htmlFor="graph-environment">Environment</FieldLabel>
          <Select value={graphEnvironment?.id ?? ""} onValueChange={openGraph} disabled={loading || error !== null}>
            <SelectTrigger id="graph-environment"><SelectValue placeholder="Choose an environment" /></SelectTrigger>
            <SelectContent><SelectGroup>{data.environments.map(environment => <SelectItem key={environment.id} value={environment.id}>{environment.name}</SelectItem>)}</SelectGroup></SelectContent>
          </Select>
        </Field>
        {graphEnvironment ? <>
          <p className="text-sm text-muted-foreground">Saved service dependencies between members of {graphEnvironment.name}.</p>
          {graphServices.length > 0 ? <ServiceDependencyGraph key={graphEnvironment.id} services={graphServices} onSelect={setSelectedService} /> :
            <Empty className="border"><EmptyHeader><EmptyTitle>No services in this environment</EmptyTitle><EmptyDescription>Select services in the environment configuration to show their dependencies.</EmptyDescription></EmptyHeader><EmptyContent><Button variant="outline" disabled={loading || error !== null} onClick={() => openForm(graphEnvironment)}>Select services</Button></EmptyContent></Empty>}
          {serviceDetail && <ServiceDetails service={serviceDetail} onClose={() => setSelectedService(null)} />}
        </> : <Empty className="border"><EmptyHeader><EmptyTitle>{environmentId ? "Environment not available" : "Choose an environment"}</EmptyTitle><EmptyDescription>Select an environment above to view dependencies between its services.</EmptyDescription></EmptyHeader></Empty>}
      </TabsContent>
    </>}
    </Tabs>}
    {detail && !form && view === "list" && <Card aria-label="Environment details"><CardHeader><div className="flex items-start justify-between gap-3"><CardTitle>{detail.name}</CardTitle><Button variant="ghost" size="icon" aria-label="Close environment details" onClick={() => setSelected(null)}><X /></Button></div><CardDescription className="whitespace-pre-wrap">{detail.description || "No description"}</CardDescription></CardHeader>
      <CardContent className="grid gap-6 md:grid-cols-2"><section aria-label="Environment services" className="flex flex-col gap-3"><h3 className="font-semibold">Services</h3>
        {detail.services.length ? <ul className="flex flex-wrap gap-2">{detail.services.map(service => <li key={service.id}><Badge variant="secondary">{service.name}</Badge></li>)}</ul> : <p className="text-sm text-muted-foreground">No services selected.</p>}
        <Link to="/services" className="text-sm underline">Manage services</Link></section>
        <section aria-label="Environment servers" className="flex flex-col gap-3"><h3 className="font-semibold">Servers</h3>
          {detail.servers.length ? <ul className="flex flex-col gap-2">{detail.servers.map(server => <li key={server.id} className="text-sm"><span className="font-medium">{server.name}</span><span className="text-muted-foreground"> · {server.hostname}</span></li>)}</ul> : <p className="text-sm text-muted-foreground">No servers selected.</p>}
          <Link to="/servers" className="text-sm underline">Manage servers</Link></section>
      </CardContent></Card>}
    <AlertDialog open={deleteTarget !== null} onOpenChange={open => { if (!open && !deleting) setDeleteTarget(null); }}>
      <AlertDialogContent aria-busy={deleting}><AlertDialogHeader><AlertDialogTitle className="break-words">Delete “{deleteTarget?.name}”?</AlertDialogTitle><AlertDialogDescription>This permanently deletes the environment and its membership links. Services, servers, and their deployment records are preserved. This cannot be undone.</AlertDialogDescription></AlertDialogHeader>
        {deleteError && <Alert variant="destructive"><AlertDescription>{deleteError}</AlertDescription></Alert>}
        <AlertDialogFooter><AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel><AlertDialogAction className={buttonVariants({ variant: "destructive" })} disabled={deleting} onClick={event => { event.preventDefault(); confirmDelete(); }}>{deleting && <Spinner data-icon="inline-start" />}Delete environment</AlertDialogAction></AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  </div>;
}
