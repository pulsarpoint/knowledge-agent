import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { Bell, RefreshCw } from "lucide-react";
import { ApiError } from "~/lib/api";
import {
  NOTIFICATION_STATUS_LABELS,
  NO_NOTIFICATION_FILTERS,
  getNotification,
  getNotifications,
  type NotificationDetail,
  type NotificationFilters,
  type NotificationRow,
  type NotificationStatus,
} from "./notifications";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "~/components/ui/empty";
import { Field, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Skeleton } from "~/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

const DEBOUNCE_MS = 300;
const ALL = "all";
const when = (value: string) => new Date(value).toLocaleString();
const text = (reason: unknown, fallback: string) => (reason instanceof ApiError ? reason.message : fallback);
const firstLine = (value: string) => value.split("\n")[0];

function StatusBadge({ status }: { status: NotificationStatus }) {
  return (
    <Badge variant={status === "failed" ? "destructive" : "secondary"}>
      {NOTIFICATION_STATUS_LABELS[status] ?? status}
    </Badge>
  );
}

/** Administration → Notifications: the answers agents gave to events (or why a call failed), newest first. */
export default function NotificationsView() {
  const [params, setParams] = useSearchParams();
  const linked = params.get("id");
  const [filters, setFilters] = useState<NotificationFilters>(NO_NOTIFICATION_FILTERS);
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<NotificationRow[] | null>(null);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [selected, setSelected] = useState<NotificationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [reload, setReload] = useState(0);
  /** Bumped whenever the first page is reloaded, so a Load more that started earlier is ignored. */
  const generation = useRef(0);

  useEffect(() => {
    const timer = setTimeout(() => setQuery(filters.q), DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [filters.q]);

  useEffect(() => {
    const controller = new AbortController();
    generation.current += 1;
    setRows(null);
    setNextOffset(null);
    setLoadingMore(false);
    getNotifications({ status: filters.status, q: query }, 0, controller.signal)
      .then((result) => {
        setRows(result.notifications);
        setNextOffset(result.next_offset);
        setError(null);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setRows([]);
          setNextOffset(null);
          setError(text(reason, "Could not load notifications."));
        }
      });
    return () => controller.abort();
  }, [filters.status, query, reload]);

  useEffect(() => {
    if (linked && /^\d+$/.test(linked)) void open(Number(linked));
  }, [linked]); // eslint-disable-line react-hooks/exhaustive-deps

  async function loadMore() {
    if (nextOffset === null) return;
    const started = generation.current;
    setLoadingMore(true);
    try {
      const result = await getNotifications({ status: filters.status, q: query }, nextOffset);
      if (generation.current !== started) return;
      setRows((current) =>
        current
          ? [...current, ...result.notifications.filter((item) => !current.some((existing) => existing.id === item.id))]
          : current,
      );
      setNextOffset(result.next_offset);
    } catch (reason) {
      if (generation.current === started) setError(text(reason, "Could not load more notifications."));
    } finally {
      if (generation.current === started) setLoadingMore(false);
    }
  }
  async function open(id: number) {
    try {
      setSelected((await getNotification(id)).notification);
    } catch (reason) {
      setError(text(reason, "Could not load the notification."));
    }
  }
  function close() {
    setSelected(null);
    if (params.has("id"))
      setParams(
        (current) => {
          current.delete("id");
          return current;
        },
        { replace: true },
      );
  }

  const filtered = Boolean(filters.status || filters.q.trim());
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <Field className="w-full sm:w-40">
          <FieldLabel htmlFor="notifications-status">Status</FieldLabel>
          <Select
            value={filters.status || ALL}
            onValueChange={(next) => setFilters((current) => ({ ...current, status: next === ALL ? "" : next }))}
          >
            <SelectTrigger id="notifications-status" aria-label="Status" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                <SelectItem value={ALL}>All statuses</SelectItem>
                <SelectItem value="succeeded">Succeeded</SelectItem>
                <SelectItem value="failed">Failed</SelectItem>
              </SelectGroup>
            </SelectContent>
          </Select>
        </Field>
        <Field className="w-full sm:w-64">
          <FieldLabel htmlFor="notifications-search">Search title</FieldLabel>
          <Input
            id="notifications-search"
            value={filters.q}
            onChange={(event) => setFilters((current) => ({ ...current, q: event.target.value }))}
          />
        </Field>
        <Button variant="outline" onClick={() => setReload((count) => count + 1)}>
          <RefreshCw data-icon="inline-start" />
          Refresh
        </Button>
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertTitle>Notifications</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {rows === null && !error && <Skeleton className="h-32 w-full" />}
      {rows?.length === 0 && !error && (
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <Bell />
            </EmptyMedia>
            <EmptyTitle>Notifications</EmptyTitle>
            <EmptyDescription>
              {filtered
                ? "No notifications match these filters."
                : "No notifications yet. A handler that sends events to an agent puts the agent's answers here."}
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      )}
      {rows && rows.length > 0 && (
        <div className="overflow-x-auto rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Time</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Title</TableHead>
                <TableHead>Preview</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((item) => (
                <TableRow
                  key={item.id}
                  tabIndex={0}
                  className="cursor-pointer"
                  onClick={() => void open(item.id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void open(item.id);
                  }}
                >
                  <TableCell className="whitespace-nowrap">{when(item.created_at)}</TableCell>
                  <TableCell>
                    <StatusBadge status={item.status} />
                  </TableCell>
                  <TableCell className="font-medium">{item.title}</TableCell>
                  <TableCell className="max-w-md truncate text-muted-foreground">{firstLine(item.preview)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      {nextOffset !== null && (
        <div className="flex justify-center">
          <Button variant="outline" disabled={loadingMore} onClick={() => void loadMore()}>
            Load more
          </Button>
        </div>
      )}
      <Sheet
        open={selected !== null}
        onOpenChange={(next) => {
          if (!next) close();
        }}
      >
        <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-xl">
          {selected && (
            <>
              <SheetHeader>
                <SheetTitle>{selected.title}</SheetTitle>
                <SheetDescription>{when(selected.created_at)}</SheetDescription>
              </SheetHeader>
              <div className="flex flex-col gap-4 px-4 pb-4 text-sm">
                <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
                  <dt className="text-muted-foreground">Status</dt>
                  <dd>
                    <StatusBadge status={selected.status} />
                  </dd>
                  <dt className="text-muted-foreground">Agent</dt>
                  <dd>{selected.agent_name ?? selected.agent_id ?? "—"}</dd>
                  {selected.task && (
                    <>
                      <dt className="text-muted-foreground">Attempts</dt>
                      <dd>{selected.task.attempts}</dd>
                    </>
                  )}
                  {selected.session_id && selected.agent_id && (
                    <>
                      <dt className="text-muted-foreground">Run</dt>
                      <dd>
                        <Link
                          className="text-primary underline underline-offset-4"
                          to={`/agents/${encodeURIComponent(selected.agent_id)}?view=test&session_id=${encodeURIComponent(selected.session_id)}`}
                        >
                          Open run
                        </Link>
                      </dd>
                    </>
                  )}
                  {selected.event && (
                    <>
                      <dt className="text-muted-foreground">Event</dt>
                      <dd>
                        <Link
                          className="text-primary underline underline-offset-4"
                          to={`/events?event=${selected.event.id}`}
                        >
                          Open event
                        </Link>{" "}
                        <span className="text-muted-foreground">
                          {selected.event.subject} · {selected.event.type}
                        </span>
                      </dd>
                    </>
                  )}
                  {selected.jira_status && (
                    <>
                      <dt className="text-muted-foreground">Jira</dt>
                      <dd>
                        {selected.jira_status === "posted" && selected.jira_base_url ? (
                          <a
                            className="text-primary underline underline-offset-4"
                            href={`${selected.jira_base_url}/browse/${encodeURIComponent(selected.jira_issue_key ?? "")}?focusedCommentId=${encodeURIComponent(selected.jira_comment_id ?? "")}`}
                            target="_blank"
                            rel="noreferrer"
                          >
                            Posted to {selected.jira_issue_key}
                          </a>
                        ) : selected.jira_status === "posted" ? (
                          `Posted to ${selected.jira_issue_key}`
                        ) : selected.jira_status === "pending" ? (
                          selected.jira_error ? (
                            `Posting to ${selected.jira_issue_key}… (attempt ${selected.jira_attempts} failed: ${selected.jira_error})`
                          ) : (
                            `Posting to ${selected.jira_issue_key}…`
                          )
                        ) : (
                          `Not posted to ${selected.jira_issue_key}: ${selected.jira_error}`
                        )}
                      </dd>
                    </>
                  )}
                </dl>
                <pre className="whitespace-pre-wrap break-words rounded-md bg-muted p-3 text-sm">{selected.body}</pre>
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}
