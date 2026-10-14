import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router";
import { Bot } from "lucide-react";
import { ApiError } from "~/lib/api";
import { getServerPage, runPagePath, type ServerPageData } from "./discovery";
import { useAgentSettings } from "~/features/sessions/agentSettings";
import { getAttachments } from "./inventoryMap";
import { useWidePage } from "~/lib/widePage";
import AgentChat from "~/features/sessions/AgentChat";
import EntrySheet from "./EntrySheet";
import RunProposals from "~/features/sessions/RunProposals";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

type Attachment = Awaited<ReturnType<typeof getAttachments>>["documents"][number];

const PANE_KEY = "server-agent-pane";
function readPane() { try { return localStorage.getItem(PANE_KEY); } catch { return null; } }
function writePane(value: string) { try { localStorage.setItem(PANE_KEY, value); } catch { /* not remembered */ } }

/** One server: its details and SSH state, the services deployed on it and its documents. Ask agent toggles the shared
 * chat beside it (discovery runs: the latest open one, New chat, Earlier runs); the page then takes the full width, the
 * server 2/3 and the chat 1/3. The open run's proposals show above the details. A run link (servers/:id/discovery/:runId,
 * a child route) opens the chat on that run, and the URL follows the chat's run. */
export default function ServerPage({ serverId }: { serverId: string }) {
  const { runId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [data, setData] = useState<ServerPageData | null>(null);
  const [documents, setDocuments] = useState<Attachment[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [version, setVersion] = useState(0);
  const [agentOpen, setAgentOpen] = useState(() => runId !== undefined || readPane() === "open");
  const [shown, setShown] = useState<{ runId: string | null; version: string }>({ runId: null, version: "" });  // the chat's run
  const name = data?.server.name ?? null;
  const settings = useAgentSettings(agentOpen && name !== null ? { kind: "server", id: serverId, name } : null);
  const split = agentOpen && data !== null;
  useWidePage(split);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([getServerPage(serverId, controller.signal), getAttachments({ kind: "server", id: serverId })]).then(([page, attached]) => {
      if (controller.signal.aborted) return;
      setData(page); setDocuments(attached.documents); setError(null);
    }).catch(e => { if (!controller.signal.aborted) setError(e instanceof ApiError ? e.message : "Could not load the server."); });
    return () => controller.abort();
  }, [serverId, version]);
  // A run link followed on this page opens the chat on that run.
  useEffect(() => { if (runId !== undefined) setAgentOpen(true); }, [runId]);
  // The URL follows the chat's run (a reload reopens it).
  const followRun = (id: string | null) => {
    const pathname = id ? runPagePath(serverId, id) : `/servers/${encodeURIComponent(serverId)}`;
    if (pathname !== location.pathname) navigate({ pathname, search: location.search }, { replace: true });
  };
  const toggleAgent = () => {
    const next = !agentOpen;
    setAgentOpen(next); writePane(next ? "open" : "closed");
    if (!next) { setShown({ runId: null, version: "" }); if (runId !== undefined) followRun(null); }  // a closed chat keeps no run in the URL
  };

  if (!data) return error
    ? <div className="flex flex-col gap-3"><Link className="self-start text-sm underline" to="/servers">Servers</Link>
        <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert></div>
    : <Skeleton className="h-60" />;
  const { server, environments, deployments, discovery } = data;
  const blocked = discovery.ready ? null : discovery.reason ?? "This server is not ready for discovery.";
  return <div data-testid="server-split" className={split ? "grid min-w-0 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] lg:items-start" : "min-w-0"}>
  <div className="flex min-w-0 flex-col gap-6">
    {agentOpen && shown.runId !== null && <RunProposals runId={shown.runId} version={shown.version} onChanged={() => setVersion(value => value + 1)} />}
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
        <Button type="button" variant="outline" aria-pressed={agentOpen} onClick={toggleAgent}><Bot data-icon="inline-start" />Ask agent</Button>
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
    {editing && <EntrySheet entry={{ kind: "server", id: serverId, name: server.name }} onClose={() => setEditing(false)}
      onChanged={() => setVersion(value => value + 1)} />}
  </div>
  {/* The chat lives beside the details: it keeps its conversation across their reload and remounts only per server. */}
  {split && <div className="min-w-0 lg:sticky lg:top-4">
    <AgentChat key={serverId} scope={{ kind: "server", id: serverId, name: server.name }} settings={settings} initialRunId={runId ?? null}
      blockedReason={blocked} onRunChange={followRun}
      onSessionChange={(id, value) => setShown(current => current.runId === id && current.version === value ? current : { runId: id, version: value })} />
  </div>}
  </div>;
}
