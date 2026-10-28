import { useEffect, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { agentPath } from "~/features/agents/api";
import { Link, useSearchParams } from "react-router";
import { Bell, RefreshCw } from "lucide-react";
import { ApiError } from "~/lib/api";
import {
  NOTIFICATION_STATUS_LABELS,
  NOTIFICATIONS_PAGE,
  NO_NOTIFICATION_FILTERS,
  getNotification,
  getNotifications,
  type NotificationDetail,
  type NotificationFilters,
  type NotificationRow,
  type NotificationStatus,
} from "./notifications";
import DataTable, { FILTER_WIDTH, TableEmpty } from "~/components/DataTable";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";

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

function giveBack(n: NotificationDetail): string {
  const name = n.give_back_name ?? "";
  if (n.give_back_status === "done") return `Assigned back to ${name}`;
  if (n.give_back_status === "failed") return `Not assigned back to ${name}: ${n.give_back_error ?? ""}`;
  if (n.give_back_status === "skipped") return n.give_back_error ?? "Not assigned back.";
  if (n.jira_status !== "posted") return `Assigns back to ${name} after the comment`;
  return n.give_back_error ? `Assigning back to ${name}… (failed: ${n.give_back_error})` : `Assigning back to ${name}…`;
}

/** Administration → Notifications: the answers agents gave to events (or why a call failed), newest first. */
export default function NotificationsView() {
  const [params, setParams] = useSearchParams();
  const linked = params.get("id");
  const [filters, setFilters] = useState<NotificationFilters>(NO_NOTIFICATION_FILTERS);
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<NotificationRow[] | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(NOTIFICATIONS_PAGE);
  const [selected, setSelected] = useState<NotificationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    // Only a changed search starts again at page 1 (not the first render: a page picked meanwhile stays).
    if (filters.q === query) return;
    const timer = setTimeout(() => {
      setQuery(filters.q);
      setPage(1);
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [filters.q]);

  useEffect(() => {
    const controller = new AbortController();
    setRows(null);
    getNotifications({ status: filters.status, q: query }, (page - 1) * pageSize, controller.signal, pageSize)
      .then((result) => {
        if (controller.signal.aborted) return; // a page answered after the filters changed
        setRows(result.notifications);
        setTotal(result.total ?? result.notifications.length);
        setError(null);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setRows([]);
          setTotal(0);
          setError(text(reason, "Could not load notifications."));
        }
      });
    return () => controller.abort();
  }, [filters.status, query, page, pageSize, reload]);

  useEffect(() => {
    if (linked && /^\d+$/.test(linked)) void open(Number(linked));
  }, [linked]); // eslint-disable-line react-hooks/exhaustive-deps

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
  const columns: ColumnDef<NotificationRow, unknown>[] = [
    {
      id: "time",
      header: "Time",
      cell: ({ row: { original: item } }) => <span className="whitespace-nowrap">{when(item.created_at)}</span>,
    },
    { id: "status", header: "Status", cell: ({ row: { original: item } }) => <StatusBadge status={item.status} /> },
    {
      id: "title",
      header: "Title",
      cell: ({ row: { original: item } }) => <span className="font-medium">{item.title}</span>,
    },
    {
      id: "preview",
      header: "Preview",
      cell: ({ row: { original: item } }) => (
        <span className="line-clamp-1 max-w-md text-muted-foreground">{firstLine(item.preview)}</span>
      ),
    },
  ];
  return (
    <div className="flex flex-col gap-4">
      {error && (
        <Alert variant="destructive">
          <AlertTitle>Notifications</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <DataTable
        columns={columns}
        data={rows ?? []}
        loading={rows === null}
        rowId={(item) => String(item.id)}
        onRowClick={(item) => void open(item.id)}
        total={total}
        page={page}
        pageSize={pageSize}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPageSize(size);
          setPage(1);
        }}
        search={{
          plural: "notifications",
          value: filters.q,
          onChange: (q) => setFilters((current) => ({ ...current, q })),
        }}
        toolbar={
          <Select
            value={filters.status || ALL}
            onValueChange={(next) => {
              setFilters((current) => ({ ...current, status: next === ALL ? "" : next }));
              setPage(1);
            }}
          >
            <SelectTrigger aria-label="Status" className={FILTER_WIDTH}>
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
        }
        actions={
          <Button variant="outline" onClick={() => setReload((count) => count + 1)}>
            <RefreshCw data-icon="inline-start" />
            Refresh
          </Button>
        }
        empty={
          error ? (
            "Could not load notifications."
          ) : filtered ? (
            "No notifications match these filters."
          ) : (
            <TableEmpty
              icon={Bell}
              title="No notifications yet"
              description="A handler that sends events to an agent puts the agent's answers here."
            />
          )
        }
      />
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
                          to={agentPath(selected.agent_id, "chat", selected.session_id)}
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
                          `Not posted to ${selected.jira_issue_key}: ${selected.jira_error ?? ""}`
                        )}
                      </dd>
                    </>
                  )}
                  {selected.give_back_status && (
                    <>
                      <dt className="text-muted-foreground">Hand-back</dt>
                      <dd>{giveBack(selected)}</dd>
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
