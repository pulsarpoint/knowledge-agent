import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { Download, Ticket } from "lucide-react";
import { ApiError } from "~/lib/api";
import { fetchJiraTickets, getJiraTickets, type JiraTickets } from "./api";
import JiraPullStatus from "./JiraPullStatus";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "~/components/ui/empty";
import { Field, FieldGroup, FieldLabel } from "~/components/ui/field";
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
  const busy = loading || fetching;
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    getJiraTickets(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) {
          setData(result);
          setError(null);
          setLimit(String(result.connection?.last_fetch_limit ?? 25));
          setJql(result.connection?.jql ?? DEFAULT_JQL);
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setError(error instanceof ApiError ? error.message : "Could not load saved Jira tickets.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [refresh]);
  async function fetchTickets(event: FormEvent) {
    event.preventDefault();
    setFetching(true);
    setError(null);
    try {
      setData(await fetchJiraTickets(Number(limit), jql.trim()));
      setQuery("");
      setProject("all");
      setStatus("all");
    } catch (error) {
      setError(
        error instanceof ApiError ? error.message : "Could not fetch Jira tickets. The previous list is preserved.",
      );
    } finally {
      setFetching(false);
    }
  }
  const tickets = data?.tickets ?? [];
  const filtered = tickets.filter(
    (ticket) =>
      (project === "all" || ticket.jira_project_key === project) &&
      (status === "all" || (ticket.status_name || "Unknown") === status) &&
      [ticket.issue_key, ticket.summary, ticket.assignee_name, ...(ticket.labels ?? [])]
        .join(" ")
        .toLowerCase()
        .includes(query.trim().toLowerCase()),
  );
  return (
    <div className="flex flex-col gap-6">
      {data?.connection?.has_token && <JiraPullStatus refreshKey={data.connection.last_successful_sync_at} />}
      <form onSubmit={fetchTickets} className="flex flex-col gap-3">
        <div className="flex flex-wrap items-end gap-3">
          <FieldGroup className="min-w-64 flex-1">
            <Field>
              <FieldLabel htmlFor="jira-jql">Jira query (JQL)</FieldLabel>
              <Input
                id="jira-jql"
                required
                maxLength={4000}
                value={jql}
                disabled={busy}
                onChange={(event) => setJql(event.target.value)}
              />
            </Field>
          </FieldGroup>
          <FieldGroup className="w-36">
            <Field>
              <FieldLabel htmlFor="jira-limit">Number of tickets</FieldLabel>
              <Input
                id="jira-limit"
                type="number"
                required
                min={1}
                max={200}
                step={1}
                value={limit}
                disabled={busy}
                onChange={(event) => setLimit(event.target.value)}
              />
            </Field>
          </FieldGroup>
          <Button disabled={busy || !data?.connection?.has_token}>
            {fetching ? <Spinner data-icon="inline-start" /> : <Download data-icon="inline-start" />}Fetch tickets
          </Button>
          <Button asChild variant="outline">
            <Link to="/jira/settings">Jira settings</Link>
          </Button>
        </div>
        <p className="text-sm text-muted-foreground">
          Filter in Jira with JQL, for example <code>project = DEMO AND status = "Open" ORDER BY updated DESC</code>.
          Fetch up to 200 matching tickets; the last successful list and query are saved locally.
        </p>
      </form>
      {error && (
        <Alert variant="destructive">
          <AlertTitle>Jira tickets unavailable</AlertTitle>
          <AlertDescription>
            {error}
            {data?.connection?.last_successful_sync_at && " Showing the previous saved list."}
            <Button type="button" variant="link" disabled={busy} onClick={() => setRefresh((value) => value + 1)}>
              Reload saved list
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {loading && data === null && <Skeleton className="h-48 w-full" />}
      {data !== null && !data.connection?.has_token && (
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <Ticket />
            </EmptyMedia>
            <EmptyTitle>Connect your Jira server</EmptyTitle>
            <EmptyDescription>
              Save your server URL, username and personal access token, then test the connection.
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Button asChild>
              <Link to="/jira/settings">Open Jira settings</Link>
            </Button>
          </EmptyContent>
        </Empty>
      )}
      {data?.connection?.has_token && data.connection.last_successful_sync_at === null && (
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <Ticket />
            </EmptyMedia>
            <EmptyTitle>Ready to fetch tickets</EmptyTitle>
            <EmptyDescription>Choose a query and ticket limit, then select Fetch tickets.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      )}
      {data?.connection?.last_successful_sync_at && (
        <>
          <div className="flex flex-col gap-3">
            <FieldGroup className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_10rem_10rem]">
              <Field>
                <FieldLabel htmlFor="jira-filter">Filter saved tickets</FieldLabel>
                <Input
                  id="jira-filter"
                  placeholder="Key, summary, assignee or label"
                  value={query}
                  disabled={busy}
                  onChange={(event) => {
                    setQuery(event.target.value);
                  }}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="jira-project">Project</FieldLabel>
                <Select
                  value={project}
                  disabled={busy}
                  onValueChange={(value) => {
                    setProject(value);
                  }}
                >
                  <SelectTrigger id="jira-project" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      <SelectItem value="all">All projects</SelectItem>
                      {[...new Set(tickets.map((ticket) => ticket.jira_project_key))].sort().map((key) => (
                        <SelectItem key={key} value={key}>
                          {key}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </Field>
              <Field>
                <FieldLabel htmlFor="jira-status">Status</FieldLabel>
                <Select
                  value={status}
                  disabled={busy}
                  onValueChange={(value) => {
                    setStatus(value);
                  }}
                >
                  <SelectTrigger id="jira-status" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      <SelectItem value="all">All statuses</SelectItem>
                      {[...new Set(tickets.map((ticket) => ticket.status_name || "Unknown"))].sort().map((name) => (
                        <SelectItem key={name} value={name}>
                          {name}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </Field>
            </FieldGroup>
            <p role="status" className="text-sm text-muted-foreground">
              Showing {filtered.length} of {tickets.length} saved tickets · {data.connection.last_fetch_total} Jira
              matches · Fetched {new Date(data.connection.last_successful_sync_at).toLocaleString()}
            </p>
          </div>
          {filtered.length === 0 ? (
            <Empty className="border">
              <EmptyHeader>
                <EmptyTitle>No matching tickets</EmptyTitle>
                <EmptyDescription>Adjust the saved-list filters or fetch a different Jira query.</EmptyDescription>
              </EmptyHeader>
            </Empty>
          ) : (
            <div className="rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Ticket</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Priority</TableHead>
                    <TableHead>Assignee</TableHead>
                    <TableHead>Updated</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filtered.map((ticket) => (
                    <TableRow key={ticket.id}>
                      <TableCell className="max-w-md whitespace-normal align-top">
                        <a
                          href={`${data.connection!.base_url}/browse/${encodeURIComponent(ticket.issue_key)}`}
                          target="_blank"
                          rel="noreferrer"
                          className="font-medium underline underline-offset-4"
                        >
                          {ticket.issue_key}
                        </a>
                        <p className="mt-1 break-words text-sm">{ticket.summary}</p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {ticket.jira_project_key}
                          {ticket.issue_type && ` · ${ticket.issue_type}`}
                        </p>
                      </TableCell>
                      <TableCell className="align-top">
                        <Badge variant="secondary">{ticket.status_name || "Unknown"}</Badge>
                      </TableCell>
                      <TableCell className="align-top">{ticket.priority || "—"}</TableCell>
                      <TableCell className="whitespace-normal align-top">
                        {ticket.assignee_name || "Unassigned"}
                      </TableCell>
                      <TableCell className="whitespace-normal align-top">
                        {new Date(ticket.source_updated_at).toLocaleString()}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
