import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { Download, FolderOutput, Ticket } from "lucide-react";
import { ApiError, exportJiraTicket, fetchJiraTickets, getJiraTickets, type JiraExportResult, type JiraTickets } from "../lib/api";
import { getCollections } from "../lib/rag";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "~/components/ui/empty";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Skeleton } from "~/components/ui/skeleton";
import { Spinner } from "~/components/ui/spinner";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

const DEFAULT_JQL = "ORDER BY updated DESC, key DESC";

export default function JiraTicketsView() {
  const [data, setData] = useState<JiraTickets | null>(null);
  const [limit, setLimit] = useState("25");
  const [jql, setJql] = useState(DEFAULT_JQL);
  const [loading, setLoading] = useState(true);
  const [fetching, setFetching] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [project, setProject] = useState("all");
  const [status, setStatus] = useState("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [collection, setCollection] = useState("");
  const [writable, setWritable] = useState<string[]>([]);
  const [exportProgress, setExportProgress] = useState<{ done: number; total: number } | null>(null);
  const [exportSummary, setExportSummary] = useState<{ collection: string; results: JiraExportResult[]; failures: string[] } | null>(null);
  const exportController = useRef<AbortController | null>(null);
  const busy = loading || fetching || exportProgress !== null;
  useEffect(() => {
    const controller = new AbortController();
    getCollections(controller.signal).then(({ collections }) => {
      const names = (collections ?? []).filter((row) => row.writable).map((row) => row.name);
      if (!controller.signal.aborted) { setWritable(names); setCollection((current) => names.includes(current) ? current : names.includes("jira") ? "jira" : names[0] ?? ""); }
    }).catch(() => { if (!controller.signal.aborted) setWritable([]); });
    return () => controller.abort();
  }, []);
  useEffect(() => () => exportController.current?.abort(), []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    getJiraTickets(controller.signal).then((result) => {
      if (!controller.signal.aborted) {
        setData(result); setError(null); setLimit(String(result.connection?.last_fetch_limit ?? 25));
        setJql(result.connection?.jql ?? DEFAULT_JQL); setSelected(new Set());
      }
    }).catch((error) => { if (!controller.signal.aborted) setError(error instanceof ApiError ? error.message : "Could not load saved Jira tickets."); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [refresh]);
  async function fetchTickets(event: FormEvent) {
    event.preventDefault(); setFetching(true); setError(null);
    try {
      setData(await fetchJiraTickets(Number(limit), jql.trim())); setSelected(new Set());
      setQuery(""); setProject("all"); setStatus("all"); setExportSummary(null);
    } catch (error) { setError(error instanceof ApiError ? error.message : "Could not fetch Jira tickets. The previous list is preserved."); }
    finally { setFetching(false); }
  }
  const tickets = data?.tickets ?? [];
  const filtered = tickets.filter((ticket) =>
    (project === "all" || ticket.jira_project_key === project) &&
    (status === "all" || (ticket.status_name || "Unknown") === status) &&
    [ticket.issue_key, ticket.summary, ticket.assignee_name, ...(ticket.labels ?? [])].join(" ").toLowerCase().includes(query.trim().toLowerCase()));
  const selectedTickets = filtered.filter((ticket) => selected.has(ticket.id));
  async function exportTickets(event: FormEvent) {
    event.preventDefault();
    if (busy || !data?.connection || selectedTickets.length === 0) return;
    const controller = new AbortController(); exportController.current = controller;
    const destination = collection;
    const results: JiraExportResult[] = [], failures: string[] = [], failedIds: string[] = [];
    setExportSummary(null); setExportProgress({ done: 0, total: selectedTickets.length });
    for (const [index, ticket] of selectedTickets.entries()) {
      if (controller.signal.aborted) return;
      try { results.push(await exportJiraTicket(ticket.id, destination, data.connection.revision, controller.signal)); }
      catch (error) {
        if (controller.signal.aborted) return;
        failures.push(`${ticket.issue_key}: ${error instanceof ApiError ? error.message : "Export could not be confirmed. Retry to check the file."}`);
        failedIds.push(ticket.id);
      }
      if (controller.signal.aborted) return;
      setExportProgress({ done: index + 1, total: selectedTickets.length });
    }
    setSelected(new Set(failedIds)); setExportProgress(null); setExportSummary({ collection: destination, results, failures });
  }
  return <div className="flex flex-col gap-6">
    <form onSubmit={fetchTickets} className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end gap-3">
        <FieldGroup className="min-w-64 flex-1"><Field><FieldLabel htmlFor="jira-jql">Jira query (JQL)</FieldLabel><Input id="jira-jql" required maxLength={4000} value={jql} disabled={busy} onChange={(event) => setJql(event.target.value)} /></Field></FieldGroup>
        <FieldGroup className="w-36"><Field><FieldLabel htmlFor="jira-limit">Number of tickets</FieldLabel><Input id="jira-limit" type="number" required min={1} max={200} step={1} value={limit} disabled={busy} onChange={(event) => setLimit(event.target.value)} /></Field></FieldGroup>
        <Button disabled={busy || !data?.connection?.has_token}>{fetching ? <Spinner data-icon="inline-start" /> : <Download data-icon="inline-start" />}Fetch tickets</Button>
        <Button asChild variant="outline"><Link to="/jira/settings">Jira settings</Link></Button>
      </div>
      <p className="text-sm text-muted-foreground">Filter in Jira with JQL, for example <code>project = DEMO AND status = "Open" ORDER BY updated DESC</code>. Fetch up to 200 matching tickets; the last successful list and query are saved locally.</p>
    </form>
    {error && <Alert variant="destructive"><AlertTitle>Jira tickets unavailable</AlertTitle><AlertDescription>{error}{data?.connection?.last_successful_sync_at && " Showing the previous saved list."}<Button type="button" variant="link" disabled={busy} onClick={() => setRefresh((value) => value + 1)}>Reload saved list</Button></AlertDescription></Alert>}
    {loading && data === null && <Skeleton className="h-48 w-full" />}
    {data !== null && !data.connection?.has_token && <Empty className="border"><EmptyHeader><EmptyMedia variant="icon"><Ticket /></EmptyMedia><EmptyTitle>Connect your Jira server</EmptyTitle><EmptyDescription>Save your server URL, username and personal access token, then test the connection.</EmptyDescription></EmptyHeader><EmptyContent><Button asChild><Link to="/jira/settings">Open Jira settings</Link></Button></EmptyContent></Empty>}
    {data?.connection?.has_token && data.connection.last_successful_sync_at === null && <Empty className="border"><EmptyHeader><EmptyMedia variant="icon"><Ticket /></EmptyMedia><EmptyTitle>Ready to fetch tickets</EmptyTitle><EmptyDescription>Choose a query and ticket limit, then select Fetch tickets.</EmptyDescription></EmptyHeader></Empty>}
    {data?.connection?.last_successful_sync_at && <>
      <div className="flex flex-col gap-3">
        <FieldGroup className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_10rem_10rem]">
          <Field><FieldLabel htmlFor="jira-filter">Filter saved tickets</FieldLabel><Input id="jira-filter" placeholder="Key, summary, assignee or label" value={query} disabled={busy} onChange={(event) => { setQuery(event.target.value); setSelected(new Set()); }} /></Field>
          <Field><FieldLabel htmlFor="jira-project">Project</FieldLabel><Select value={project} disabled={busy} onValueChange={(value) => { setProject(value); setSelected(new Set()); }}><SelectTrigger id="jira-project" className="w-full"><SelectValue /></SelectTrigger><SelectContent><SelectGroup><SelectItem value="all">All projects</SelectItem>{[...new Set(tickets.map((ticket) => ticket.jira_project_key))].sort().map((key) => <SelectItem key={key} value={key}>{key}</SelectItem>)}</SelectGroup></SelectContent></Select></Field>
          <Field><FieldLabel htmlFor="jira-status">Status</FieldLabel><Select value={status} disabled={busy} onValueChange={(value) => { setStatus(value); setSelected(new Set()); }}><SelectTrigger id="jira-status" className="w-full"><SelectValue /></SelectTrigger><SelectContent><SelectGroup><SelectItem value="all">All statuses</SelectItem>{[...new Set(tickets.map((ticket) => ticket.status_name || "Unknown"))].sort().map((name) => <SelectItem key={name} value={name}>{name}</SelectItem>)}</SelectGroup></SelectContent></Select></Field>
        </FieldGroup>
        <p role="status" className="text-sm text-muted-foreground">Showing {filtered.length} of {tickets.length} saved tickets · {data.connection.last_fetch_total} Jira matches · Fetched {new Date(data.connection.last_successful_sync_at).toLocaleString()}</p>
      </div>
      <form onSubmit={exportTickets} className="flex flex-col gap-3 border-t pt-5">
        <div className="flex flex-wrap items-end gap-3">
          <FieldGroup className="max-w-md flex-1"><Field><FieldLabel htmlFor="jira-collection">Target collection</FieldLabel><Select value={collection} disabled={busy || writable.length === 0} onValueChange={setCollection}><SelectTrigger id="jira-collection" className="w-full"><SelectValue placeholder="No writable collection" /></SelectTrigger><SelectContent><SelectGroup>{writable.map((name) => <SelectItem key={name} value={name}>{name}</SelectItem>)}</SelectGroup></SelectContent></Select></Field></FieldGroup>
          <Button disabled={busy || selectedTickets.length === 0 || collection === ""}><FolderOutput data-icon="inline-start" />Export selected ({selectedTickets.length})</Button>
          <Button asChild variant="outline"><Link to="/rag/collections">Open RAG collections</Link></Button>
        </div>
        <FieldDescription>Only collections under rag-data accept files; create one in RAG Collections if none is listed. Each selected ticket becomes a Markdown file with its description and all comments visible to the Jira user. Re-exporting refreshes that ticket’s file. Then refresh the collection in RAG Collections to update search.</FieldDescription>
      </form>
      {exportProgress && <p role="status" className="flex items-center gap-2 text-sm"><Spinner />Exporting {exportProgress.done} of {exportProgress.total} tickets. Keep this page open.</p>}
      {exportSummary && <Alert variant={exportSummary.failures.length > 0 ? "destructive" : "default"}>
        <AlertTitle>{exportSummary.failures.length > 0 ? "Export finished with errors" : "Export complete"}</AlertTitle>
        <AlertDescription>
          <p>{exportSummary.results.filter((result) => result.status !== "unchanged").length} files saved, {exportSummary.results.filter((result) => result.status === "unchanged").length} unchanged in collection {exportSummary.collection}. {exportSummary.results.reduce((count, result) => count + result.comments, 0)} comments included.</p>
          {exportSummary.failures.length > 0 && <><p>Failed tickets remain selected for retry.</p><ul className="list-disc pl-5">{exportSummary.failures.map((failure) => <li key={failure}>{failure}</li>)}</ul></>}
          {exportSummary.results.length > 0 && <p>Refresh the collection to update search in <Link className="underline underline-offset-4" to="/rag/collections">RAG Collections</Link>.</p>}
        </AlertDescription>
      </Alert>}
      {filtered.length === 0 ? <Empty className="border"><EmptyHeader><EmptyTitle>No matching tickets</EmptyTitle><EmptyDescription>Adjust the saved-list filters or fetch a different Jira query.</EmptyDescription></EmptyHeader></Empty> :
        <div className="rounded-lg border"><Table><TableHeader><TableRow>
          <TableHead className="w-10"><Checkbox aria-label="Select all filtered tickets" disabled={busy} checked={selectedTickets.length === filtered.length ? true : selectedTickets.length > 0 ? "indeterminate" : false} onCheckedChange={(checked) => setSelected(new Set(checked === true ? filtered.map((ticket) => ticket.id) : []))} /></TableHead>
          <TableHead>Ticket</TableHead><TableHead>Status</TableHead><TableHead>Priority</TableHead><TableHead>Assignee</TableHead><TableHead>Updated</TableHead></TableRow></TableHeader><TableBody>
          {filtered.map((ticket) => <TableRow key={ticket.id} data-state={selected.has(ticket.id) ? "selected" : undefined}>
            <TableCell className="align-top"><Checkbox aria-label={`Select ${ticket.issue_key}`} disabled={busy} checked={selected.has(ticket.id)} onCheckedChange={(checked) => setSelected((previous) => { const next = new Set(previous); if (checked === true) next.add(ticket.id); else next.delete(ticket.id); return next; })} /></TableCell>
            <TableCell className="max-w-md whitespace-normal align-top"><a href={`${data.connection!.base_url}/browse/${encodeURIComponent(ticket.issue_key)}`} target="_blank" rel="noreferrer" className="font-medium underline underline-offset-4">{ticket.issue_key}</a><p className="mt-1 break-words text-sm">{ticket.summary}</p><p className="mt-1 text-xs text-muted-foreground">{ticket.jira_project_key}{ticket.issue_type && ` · ${ticket.issue_type}`}</p></TableCell>
            <TableCell className="align-top"><Badge variant="secondary">{ticket.status_name || "Unknown"}</Badge></TableCell>
            <TableCell className="align-top">{ticket.priority || "—"}</TableCell><TableCell className="whitespace-normal align-top">{ticket.assignee_name || "Unassigned"}</TableCell>
            <TableCell className="whitespace-normal align-top">{new Date(ticket.source_updated_at).toLocaleString()}</TableCell>
          </TableRow>)}
        </TableBody></Table></div>}
    </>}
  </div>;
}
