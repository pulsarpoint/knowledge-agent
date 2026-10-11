import { useEffect, useState } from "react";
import { Link } from "react-router";
import { ApiError } from "~/lib/api";
import { getDiscoveryRuns, getServerPage, runPagePath, type DiscoveryRun, type ServerPageData } from "~/lib/discovery";
import { statusLabels } from "~/lib/generations";
import { getAttachments } from "~/lib/inventoryMap";
import DiscoverySheet from "./DiscoverySheet";
import EntrySheet from "./EntrySheet";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

type Attachment = Awaited<ReturnType<typeof getAttachments>>["documents"][number];

/** One server: its details and SSH state, the services deployed on it, its documents, and its discovery runs. */
export default function ServerPage({ serverId }: { serverId: string }) {
  const [data, setData] = useState<ServerPageData | null>(null);
  const [runs, setRuns] = useState<DiscoveryRun[]>([]);
  const [documents, setDocuments] = useState<Attachment[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [editing, setEditing] = useState(false);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([getServerPage(serverId, controller.signal), getDiscoveryRuns(serverId, controller.signal),
                 getAttachments({ kind: "server", id: serverId })]).then(([page, found, attached]) => {
      if (controller.signal.aborted) return;
      setData(page); setRuns(found.runs); setDocuments(attached.documents); setError(null);
    }).catch(e => { if (!controller.signal.aborted) setError(e instanceof ApiError ? e.message : "Could not load the server."); });
    return () => controller.abort();
  }, [serverId, version]);

  if (!data) return error
    ? <div className="flex flex-col gap-3"><Link className="self-start text-sm underline" to="/servers">Servers</Link>
        <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert></div>
    : <Skeleton className="h-60" />;
  const { server, environments, deployments, discovery } = data;
  return <div className="flex flex-col gap-6">
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="flex flex-col gap-1"><Link className="self-start text-sm underline" to="/servers">Servers</Link>
        <h1 className="break-words text-2xl font-semibold">{server.name}</h1>
        <p className="font-mono text-sm">{server.hostname}</p>
        {server.description && <p className="text-sm text-muted-foreground">{server.description}</p>}
        <div className="flex flex-wrap gap-1">{environments.map(item => <Badge key={item.id} variant="secondary">{item.name}</Badge>)}
          <Badge variant={data.ssh_ready ? "secondary" : "outline"}>{data.ssh_ready ? "SSH set up" : "SSH not set up"}</Badge>
          <Badge variant={server.ssh_allowed ? "secondary" : "outline"}>{server.ssh_allowed ? "Agent SSH allowed" : "Agent SSH off"}</Badge></div></div>
      <div className="flex flex-col items-end gap-1">
        <Button disabled={!discovery.ready} onClick={() => setAsking(true)}>Ask agent to collect info</Button>
        {discovery.reason && <p className="text-sm text-muted-foreground">{discovery.reason}</p>}</div>
    </div>
    <section aria-label="Services" className="flex flex-col gap-2"><h2 className="text-lg font-semibold">Services</h2>
      {deployments.length === 0 ? <p className="text-sm text-muted-foreground">No services are recorded on this server.</p> :
        <div className="rounded-lg border"><Table><TableHeader><TableRow><TableHead>Service</TableHead><TableHead>Environment</TableHead><TableHead>Runtime</TableHead><TableHead>Port</TableHead><TableHead>Version</TableHead></TableRow></TableHeader>
          <TableBody>{deployments.map(item => <TableRow key={item.id}>
            <TableCell><Link className="underline" to="/services">{item.service_name}</Link></TableCell><TableCell>{item.environment}</TableCell>
            <TableCell>{[item.runtime, item.runtime_name].filter(Boolean).join(" · ") || "—"}</TableCell><TableCell>{item.port ?? "—"}</TableCell>
            <TableCell>{item.deployed_version || "—"}</TableCell></TableRow>)}</TableBody></Table></div>}
    </section>
    <section aria-label="Documents" className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2"><h2 className="text-lg font-semibold">Documents</h2>
        <Button variant="outline" size="sm" onClick={() => setEditing(true)}>Connections and documents</Button></div>
      {documents.length === 0 ? <p className="text-sm text-muted-foreground">No documents are attached to this server.</p> :
        <ul className="flex flex-col gap-1">{documents.map(item => <li key={item.id}><Link className="underline" to={`/documents/${item.document_id}`}>{item.title}</Link></li>)}</ul>}
    </section>
    <section aria-label="Discovery" className="flex flex-col gap-2"><h2 className="text-lg font-semibold">Discovery</h2>
      {runs.length === 0 ? <p className="text-sm text-muted-foreground">No discovery runs yet.</p> :
        <ul className="flex flex-col gap-2">{runs.map(run => <li key={run.id} className="flex flex-wrap items-center gap-2">
          <Link className="underline" to={runPagePath(serverId, run.id)}>{run.title} · {new Date(run.created_at).toLocaleString()}</Link>
          <Badge variant="outline">{statusLabels[run.status]}</Badge>
          <span className="text-sm text-muted-foreground">{`${run.pending} pending · ${run.accepted} accepted · ${run.rejected} rejected`}</span></li>)}</ul>}
    </section>
    {asking && <DiscoverySheet serverId={serverId} serverName={server.name} onClose={() => setAsking(false)} />}
    {editing && <EntrySheet entry={{ kind: "server", id: serverId, name: server.name }} onClose={() => setEditing(false)}
      onChanged={() => setVersion(value => value + 1)} />}
  </div>;
}
