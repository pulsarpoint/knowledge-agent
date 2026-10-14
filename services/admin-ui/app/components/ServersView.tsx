import { useEffect, useState } from "react";
import { Link } from "react-router";
import { Pencil, Plug, Plus, RefreshCw, Server, Trash2 } from "lucide-react";
import { ApiError } from "../lib/api";
import { deleteServer, getServers, type ServerAsset } from "../lib/servers";
import { getSSH, testSSH } from "../lib/operations";
import ServerForm from "./ServerForm";
import ServerSSHForm from "./ServerSSHForm";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "~/components/ui/alert-dialog";
import { Badge } from "~/components/ui/badge";
import { Button, buttonVariants } from "~/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "~/components/ui/empty";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";
import { Spinner } from "~/components/ui/spinner";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

export default function ServersView() {
  const [servers, setServers] = useState<ServerAsset[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [form, setForm] = useState<{ server: ServerAsset | null } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ServerAsset | null>(null);
  const [sshServer, setSshServer] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ name: string; ok: boolean; message: string } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    getServers(controller.signal).then((data) => {
      if (!controller.signal.aborted) { setServers(data.servers); setError(null); }
    }).catch((error) => {
      if (!controller.signal.aborted) setError(error instanceof ApiError ? error.message : "Could not load servers. Try refreshing.");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [refresh]);

  function openForm(server: ServerAsset | null) { setForm({ server }); setNotice(null); }
  async function confirmDelete() {
    if (deleteTarget === null || deleting) return;
    const target = deleteTarget;
    setDeleting(true); setDeleteError(null);
    try {
      await deleteServer(target.id, target.revision);
      setServers((current) => current?.filter((server) => server.id !== target.id) ?? null);
      setDeleteTarget(null); setNotice(`Server “${target.name}” deleted.`);
      setRefresh((value) => value + 1);
    } catch (error) {
      setDeleteError(error instanceof ApiError ? error.message : "Could not delete the server. Try again.");
    } finally { setDeleting(false); }
  }
  async function testConnection(server: ServerAsset) {
    setTesting(server.id); setTestResult(null); setNotice(null);
    try {
      const saved = await getSSH(server.id);
      const result = await testSSH(server.id, { revision: saved.revision, username: saved.username, port: saved.port, allow_sudo: saved.allow_sudo, known_hosts: saved.known_hosts });
      setTestResult({ name: server.name, ok: result.ok, message: result.message });
    } catch (error) {
      setTestResult({ name: server.name, ok: false, message: error instanceof ApiError ? error.message : "Could not test SSH." });
    } finally { setTesting(null); }
  }
  const matching = servers?.filter((server) => [server.name, server.hostname, server.description, ...server.tags]
    .some((value) => value.toLowerCase().includes(query.trim().toLowerCase()))) ?? [];

  return <div className="flex flex-col gap-6">
    <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-sm text-muted-foreground">{servers ? `${servers.length} ${servers.length === 1 ? "server" : "servers"}` : "Loading inventory…"} · Application infrastructure</p>
      <div className="flex gap-2"><Button variant="outline" disabled={loading || form !== null} onClick={() => setRefresh((value) => value + 1)}><RefreshCw data-icon="inline-start" />Refresh</Button>
        <Button disabled={loading || error !== null || servers === null || form !== null} onClick={() => openForm(null)}><Plus data-icon="inline-start" />Add server</Button></div></div>
    {error && <Alert variant="destructive"><AlertTitle>Server inventory unavailable</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
    {notice && <Alert><AlertDescription role="status">{notice}</AlertDescription></Alert>}
    {testResult && <Alert variant={testResult.ok ? "default" : "destructive"}><AlertTitle className="break-words">{testResult.name}: SSH test {testResult.ok ? "passed" : "failed"}</AlertTitle><AlertDescription role="status">{testResult.message}{!testResult.ok && " Open SSH settings to fix the configuration."}</AlertDescription></Alert>}
    {sshServer && <ServerSSHForm id={sshServer} onClose={() => { setSshServer(null); setRefresh(value => value + 1); }} />}
    {form && <ServerForm key={form.server?.id ?? "new"} server={form.server}
      onClose={() => { setForm(null); setRefresh((value) => value + 1); }}
      onSaved={() => { setForm(null); setQuery(""); setNotice("Server saved. Linked services now use its updated details."); setRefresh((value) => value + 1); }} />}
    {servers === null && loading && <Skeleton className="h-48 w-full" />}
    {servers?.length === 0 && <Empty className="border"><EmptyHeader><EmptyMedia variant="icon"><Server /></EmptyMedia><EmptyTitle>Build your server inventory</EmptyTitle><EmptyDescription>Register hosts with descriptions and tags, then connect them to services for agent investigations.</EmptyDescription></EmptyHeader><EmptyContent><Button variant="outline" disabled={loading || error !== null || form !== null} onClick={() => openForm(null)}><Plus data-icon="inline-start" />Add your first server</Button></EmptyContent></Empty>}
    {servers !== null && servers.length > 0 && <>
      <Input aria-label="Filter servers" placeholder="Filter by name, hostname, description, or tag…" value={query} onChange={(event) => setQuery(event.target.value)} />
      <div className="rounded-lg border"><Table><TableHeader><TableRow><TableHead>Server</TableHead><TableHead>Hostname / IP</TableHead><TableHead className="min-w-40">Tags</TableHead><TableHead>SSH allowed</TableHead><TableHead className="text-right">Actions</TableHead></TableRow></TableHeader>
        <TableBody>{matching.map((server) => <TableRow key={server.id}>
          <TableCell className="max-w-sm whitespace-normal align-top"><p className="break-words font-medium"><Link className="underline-offset-4 hover:underline" to={`/servers/${server.id}`}>{server.name}</Link></p><p className="mt-1 whitespace-pre-wrap break-words text-sm text-muted-foreground">{server.description || "No description yet"}</p></TableCell>
          <TableCell className="max-w-xs whitespace-normal align-top"><span className="break-all font-mono text-sm">{server.hostname}</span></TableCell>
          <TableCell className="max-w-xs whitespace-normal align-top"><div className="flex flex-wrap gap-1">{server.tags.map((tag) => <Badge key={tag} variant="secondary" className="max-w-full whitespace-normal break-all">{tag}</Badge>)}{server.tags.length === 0 && <span className="text-sm text-muted-foreground">No tags</span>}</div></TableCell>
          <TableCell className="align-top"><Badge variant={server.ssh_allowed ? "secondary" : "outline"}>{server.ssh_allowed ? "On" : "Off"}</Badge></TableCell>
          <TableCell className="align-top"><div className="flex flex-wrap justify-end gap-2"><Button variant="outline" size="sm" disabled={loading || form !== null || sshServer !== null} onClick={() => { setTestResult(null); setSshServer(server.id); }}>SSH settings<span className="sr-only"> {server.name}</span></Button><Button variant="outline" size="sm" disabled={!server.ssh_allowed || testing !== null || sshServer !== null} title={server.ssh_allowed ? undefined : "Enable SSH allowed to test"} aria-label={`Test SSH ${server.name}`} onClick={() => void testConnection(server)}>{testing === server.id ? <Spinner data-icon="inline-start" /> : <Plug data-icon="inline-start" />}Test SSH</Button><Button variant="outline" size="sm" disabled={loading || error !== null || form !== null} aria-label={`Edit ${server.name}`} onClick={() => openForm(server)}><Pencil data-icon="inline-start" />Edit</Button>
            <Button variant="destructive" size="sm" disabled={loading || error !== null || form !== null} aria-label={`Delete ${server.name}`} onClick={() => { setDeleteTarget(server); setDeleteError(null); setNotice(null); }}><Trash2 data-icon="inline-start" />Delete</Button>
          </div></TableCell>
        </TableRow>)}{matching.length === 0 && <TableRow><TableCell colSpan={5} className="text-center">No servers match this filter.</TableCell></TableRow>}</TableBody>
      </Table></div>
    </>}
    <AlertDialog open={deleteTarget !== null} onOpenChange={(open) => { if (!open && !deleting) setDeleteTarget(null); }}>
      <AlertDialogContent aria-busy={deleting}>
        <AlertDialogHeader><AlertDialogTitle className="break-words">Delete “{deleteTarget?.name}”?</AlertDialogTitle>
          <AlertDialogDescription className="break-words">This permanently removes the server record for {deleteTarget?.hostname} from the inventory. Linked service deployments must be removed first. This cannot be undone.</AlertDialogDescription>
        </AlertDialogHeader>
        {deleteError && <Alert variant="destructive"><AlertDescription>{deleteError}</AlertDescription></Alert>}
        <AlertDialogFooter><AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
          <AlertDialogAction className={buttonVariants({ variant: "destructive" })} disabled={deleting} onClick={(event) => { event.preventDefault(); confirmDelete(); }}>
            {deleting && <Spinner data-icon="inline-start" />}Delete server
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  </div>;
}
