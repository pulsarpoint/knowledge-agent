import { useEffect, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { agentPath } from "~/features/agents/api";
import { Link, useSearchParams } from "react-router";
import { Copy, Inbox, Plus, RefreshCw } from "lucide-react";
import { ApiError } from "~/lib/api";
import {
  DEFAULT_SORT,
  EVENTS_PAGE,
  NO_FILTERS,
  SOURCE_LABELS,
  STATUS_LABELS,
  TASK_STATUS_LABELS,
  TYPE_LABELS,
  describeChanges,
  getEvent,
  getEventFacets,
  getEvents,
  type EventDetail,
  type EventFacets,
  type EventFilters,
  type EventRow,
  type EventSort,
  type EventStatus,
  type SortKey,
} from "./api";
import { statusLabels } from "~/features/sessions/api";
import NewEventSheet, { type Prefill } from "./NewEventSheet";
import DataTable, { sortHeader, TableEmpty, type Sort } from "~/components/DataTable";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";

const DEBOUNCE_MS = 300;
const ALL = "all";
const when = (value: string) => new Date(value).toLocaleString();
const text = (reason: unknown, fallback: string) => (reason instanceof ApiError ? reason.message : fallback);
const typeLabel = (type: string) => TYPE_LABELS[type] ?? type;
const sourceLabel = (source: string) => SOURCE_LABELS[source] ?? source;
/** "—", "1 handler" or "3 handlers": how many handlers ran on the event. */
export const handlersLabel = (count = 0) => (count === 0 ? "—" : count === 1 ? "1 handler" : `${count} handlers`);

export function StatusBadge({ status }: { status: EventStatus }) {
  return (
    <Badge variant={status === "failed" ? "destructive" : status === "pending" ? "outline" : "secondary"}>
      {STATUS_LABELS[status] ?? status}
    </Badge>
  );
}

/** A toolbar filter: an unlabelled Select named by its `label`, "All …" first. */
function FilterSelect({
  label,
  value,
  all,
  options,
  onChange,
  className,
}: {
  label: string;
  value: string;
  all: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
  className: string;
}) {
  return (
    <Select value={value || ALL} onValueChange={(next) => onChange(next === ALL ? "" : next)}>
      <SelectTrigger aria-label={label} className={className}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          <SelectItem value={ALL}>{all}</SelectItem>
          {options.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  );
}

export default function EventsView() {
  const [params, setParams] = useSearchParams();
  const linked = params.get("event");
  const [filters, setFilters] = useState<EventFilters>(NO_FILTERS);
  const [sort, setSort] = useState<EventSort>(DEFAULT_SORT);
  const [query, setQuery] = useState("");
  const [events, setEvents] = useState<EventRow[] | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(EVENTS_PAGE);
  const [facets, setFacets] = useState<EventFacets>({
    sources: [],
    types: [],
    statuses: [],
    handlers: [],
    schedules: [],
  });
  const [selected, setSelected] = useState<EventDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  /** The New event form: null when closed, else what it starts with (a copy, or nothing). */
  const [form, setForm] = useState<{ key: number; prefill?: Prefill } | null>(null);

  useEffect(() => {
    // Only a changed search starts again at page 1 (not the first render: a page picked meanwhile stays).
    if (filters.q === query) return;
    const timer = setTimeout(() => {
      setQuery(filters.q);
      setPage(1);
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [filters.q]);

  const active: EventFilters = { ...filters, q: query };

  useEffect(() => {
    const controller = new AbortController();
    getEventFacets(controller.signal)
      .then(setFacets)
      .catch((reason) => {
        if (!controller.signal.aborted) setError(text(reason, "Could not load the filters."));
      });
    return () => controller.abort();
  }, [reload]);

  useEffect(() => {
    const controller = new AbortController();
    setEvents(null);
    getEvents(active, sort, (page - 1) * pageSize, controller.signal, pageSize)
      .then((result) => {
        if (controller.signal.aborted) return; // a page answered after the filters changed
        setEvents(result.events);
        setTotal(result.total ?? result.events.length);
        setError(null);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setEvents([]);
          setTotal(0);
          setError(text(reason, "Could not load events."));
        }
      });
    return () => controller.abort();
  }, [
    filters.source,
    filters.type,
    filters.status,
    filters.handled,
    filters.handler,
    filters.schedule,
    query,
    sort.key,
    sort.order,
    page,
    pageSize,
    reload,
  ]); // eslint-disable-line react-hooks/exhaustive-deps

  async function open(id: number) {
    try {
      setSelected((await getEvent(id)).event);
    } catch (reason) {
      setError(text(reason, "Could not load the event."));
    }
  }
  useEffect(() => {
    if (linked && /^[1-9]\d*$/.test(linked)) void open(Number(linked));
  }, [linked]); // eslint-disable-line react-hooks/exhaustive-deps
  function close() {
    setSelected(null);
    if (params.has("event"))
      setParams(
        (current) => {
          current.delete("event");
          return current;
        },
        { replace: true },
      );
  }
  /** A new column sorts newest/last first; the same column again flips the order. A new order starts at page 1. */
  const sortBy = (key: SortKey) => {
    setSort((current) =>
      current.key === key ? { key, order: current.order === "desc" ? "asc" : "desc" } : { key, order: "desc" },
    );
    setPage(1);
  };
  const shownSort: Sort<SortKey> = { key: sort.key, descending: sort.order === "desc" };
  const header = (label: string, key: SortKey) => sortHeader(label, key, shownSort, (next) => sortBy(next.key));
  /** A filter change starts at page 1 (the search waits for typing to pause first). */
  const set = (field: keyof EventFilters) => (value: string) => {
    setFilters((current) => ({ ...current, [field]: value }));
    if (field !== "q") setPage(1);
  };

  function created(event: EventDetail) {
    setForm(null);
    setReload((count) => count + 1);
    setSelected(event);
  }
  /** Closes the event's sheet first, so the two sheets do not stack. */
  function duplicate(event: EventDetail) {
    close();
    setForm({
      key: Date.now(),
      prefill: {
        source: event.source === "jira" ? "jira" : "manual",
        type: event.type,
        subject: event.subject,
        title: event.title,
        payload: event.payload,
      },
    });
  }

  const filtered = Boolean(
    filters.source ||
    filters.type ||
    filters.status ||
    filters.handled ||
    filters.handler ||
    filters.schedule ||
    filters.q.trim(),
  );
  const changes = selected ? describeChanges(selected.payload.changes) : [];
  /** The API caps the chain, so the open event may be missing from it. */
  const chain = selected
    ? selected.chain.some((node) => node.id === selected.id)
      ? selected.chain
      : [selected, ...selected.chain]
    : [];
  const url =
    selected && typeof selected.payload.url === "string" && /^https?:\/\//i.test(selected.payload.url)
      ? selected.payload.url
      : null;

  const columns: ColumnDef<EventRow, unknown>[] = [
    {
      id: "time",
      header: () => header("Time", "time"),
      cell: ({ row: { original: row } }) => <span className="whitespace-nowrap">{when(row.occurred_at)}</span>,
    },
    {
      id: "source",
      header: () => header("Source", "source"),
      cell: ({ row: { original: row } }) => <span className="whitespace-nowrap">{sourceLabel(row.source)}</span>,
    },
    {
      id: "type",
      header: () => header("Type", "type"),
      cell: ({ row: { original: row } }) => <span className="whitespace-nowrap">{typeLabel(row.type)}</span>,
    },
    {
      id: "subject",
      header: () => header("Subject", "subject"),
      cell: ({ row: { original: row } }) => <span className="font-medium">{row.subject}</span>,
    },
    { id: "title", header: "Title", cell: ({ row: { original: row } }) => row.title },
    {
      id: "status",
      header: () => header("Status", "status"),
      cell: ({ row: { original: row } }) => <StatusBadge status={row.status} />,
    },
    {
      id: "handlers",
      header: "Handlers",
      cell: ({ row: { original: row } }) => (
        <span className="whitespace-nowrap text-muted-foreground">{handlersLabel(row.handlers)}</span>
      ),
    },
  ];
  const newEvent = (
    <Button onClick={() => setForm({ key: Date.now() })}>
      <Plus data-icon="inline-start" />
      New event
    </Button>
  );

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="outline" onClick={() => setReload((count) => count + 1)}>
          <RefreshCw data-icon="inline-start" />
          Refresh
        </Button>
        {newEvent}
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertTitle>Events</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <DataTable
        columns={columns}
        data={events ?? []}
        loading={events === null}
        rowId={(row) => String(row.id)}
        onRowClick={(row) => void open(row.id)}
        sort={shownSort}
        total={total}
        page={page}
        pageSize={pageSize}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPageSize(size);
          setPage(1);
        }}
        search={{ plural: "events", value: filters.q, onChange: set("q") }}
        toolbar={
          <>
            <FilterSelect
              label="Source"
              all="All sources"
              className="w-full sm:w-40"
              value={filters.source}
              onChange={set("source")}
              options={facets.sources.map((source) => ({ value: source, label: sourceLabel(source) }))}
            />
            <FilterSelect
              label="Type"
              all="All types"
              className="w-full sm:w-52"
              value={filters.type}
              onChange={set("type")}
              options={facets.types.map((type) => ({ value: type, label: typeLabel(type) }))}
            />
            <FilterSelect
              label="Status"
              all="All statuses"
              className="w-full sm:w-40"
              value={filters.status}
              onChange={set("status")}
              options={facets.statuses.map((status) => ({ value: status, label: STATUS_LABELS[status] ?? status }))}
            />
            <FilterSelect
              label="Handled"
              all="Handled or not"
              className="w-full sm:w-40"
              value={filters.handled}
              onChange={set("handled")}
              options={[
                { value: "yes", label: "Handled" },
                { value: "no", label: "Not handled" },
              ]}
            />
            <FilterSelect
              label="Emitted by"
              all="All handlers"
              className="w-full sm:w-44"
              value={filters.handler}
              onChange={set("handler")}
              options={facets.handlers.map((handler) => ({ value: handler.id, label: handler.name }))}
            />
            <FilterSelect
              label="Schedule"
              all="All schedules"
              className="w-full sm:w-44"
              value={filters.schedule}
              onChange={set("schedule")}
              options={facets.schedules.map((name) => ({ value: name, label: name }))}
            />
          </>
        }
        empty={
          error ? (
            "Could not load events."
          ) : filtered ? (
            "No events match these filters."
          ) : (
            <TableEmpty
              icon={Inbox}
              title="No events yet"
              description="Add a Jira tickets schedule in Administration → Schedules to record ticket changes, or make one by hand."
            >
              {newEvent}
            </TableEmpty>
          )
        }
      />
      {form && (
        <NewEventSheet
          key={form.key}
          prefill={form.prefill}
          types={facets.types}
          onClose={() => setForm(null)}
          onCreated={created}
        />
      )}
      <Sheet
        open={selected !== null}
        onOpenChange={(next) => {
          if (!next) close();
        }}
      >
        <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-lg">
          {selected && (
            <>
              <SheetHeader>
                <SheetTitle>
                  {selected.subject} · {typeLabel(selected.type)}
                </SheetTitle>
                <SheetDescription>{selected.title}</SheetDescription>
              </SheetHeader>
              <div className="flex flex-col gap-4 px-4 pb-4 text-sm">
                <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
                  <dt className="text-muted-foreground">Happened</dt>
                  <dd>{when(selected.occurred_at)}</dd>
                  <dt className="text-muted-foreground">Recorded</dt>
                  <dd>{when(selected.created_at)}</dd>
                  <dt className="text-muted-foreground">Source</dt>
                  <dd>{sourceLabel(selected.source)}</dd>
                  <dt className="text-muted-foreground">Status</dt>
                  <dd>
                    <StatusBadge status={selected.status} />
                  </dd>
                  {selected.created_manually && (
                    <>
                      <dt className="text-muted-foreground">Origin</dt>
                      <dd>Created by hand</dd>
                    </>
                  )}
                  {selected.parent && (
                    <>
                      <dt className="text-muted-foreground">Parent</dt>
                      <dd>
                        <Button variant="link" className="h-auto p-0" onClick={() => void open(selected.parent!.id)}>
                          {selected.parent.subject} · {typeLabel(selected.parent.type)}
                        </Button>
                      </dd>
                    </>
                  )}
                </dl>
                <Button variant="outline" size="sm" className="self-start" onClick={() => duplicate(selected)}>
                  <Copy data-icon="inline-start" />
                  Duplicate
                </Button>
                {url && (
                  <a href={url} target="_blank" rel="noreferrer" className="text-primary underline underline-offset-4">
                    Open in Jira
                  </a>
                )}
                {chain.length > 1 && (
                  <div>
                    <h3 className="mb-1 font-medium">Chain</h3>
                    <ul aria-label="Chain" className="flex flex-col gap-1">
                      {chain.map((node) => (
                        <li key={node.id} style={{ paddingLeft: `${node.depth}rem` }}>
                          {node.id === selected.id ? (
                            <span aria-current="true" className="font-medium">
                              {node.subject} · {typeLabel(node.type)}
                            </span>
                          ) : (
                            <Button variant="link" className="h-auto p-0" onClick={() => void open(node.id)}>
                              {node.subject} · {typeLabel(node.type)}
                            </Button>
                          )}{" "}
                          <StatusBadge status={node.status} />
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {selected.runs.length > 0 && (
                  <div>
                    <h3 className="mb-1 font-medium">Handler runs</h3>
                    <ul className="flex flex-col gap-2">
                      {selected.runs.map((run, index) => (
                        <li key={index} className="flex flex-col gap-1">
                          <div className="flex items-center gap-2">
                            <span className="font-medium">{run.handler_name}</span>
                            <Badge variant={run.status === "failed" ? "destructive" : "secondary"}>
                              {run.status === "failed" ? "Failed" : "Succeeded"}
                            </Badge>
                          </div>
                          {run.message && <span className="text-muted-foreground">{run.message}</span>}
                          {run.emitted_event_id !== null && (
                            <Button
                              variant="link"
                              className="h-auto self-start p-0"
                              onClick={() => void open(run.emitted_event_id!)}
                            >
                              Open event #{run.emitted_event_id}
                            </Button>
                          )}
                          {run.run && (
                            <div className="flex flex-wrap items-center gap-2">
                              <Badge variant={run.run.status === "failed" ? "destructive" : "outline"}>
                                {statusLabels[run.run.status]}
                              </Badge>
                              <Link
                                className="text-primary underline underline-offset-4"
                                to={agentPath(run.run.agent_id, "chat", run.run.id)}
                              >
                                Open run
                              </Link>
                              {run.run.notification_id != null && (
                                <Link
                                  className="text-primary underline underline-offset-4"
                                  to={`/notifications?id=${run.run.notification_id}`}
                                >
                                  Open notification
                                </Link>
                              )}
                            </div>
                          )}
                          {!run.run && run.agent_session_id && run.agent_id && (
                            <Link
                              className="text-primary underline underline-offset-4"
                              to={agentPath(run.agent_id, "chat", run.agent_session_id)}
                            >
                              Open agent session
                            </Link>
                          )}
                          {run.agent_task && (
                            <div className="flex flex-wrap items-center gap-2">
                              <Badge
                                variant={
                                  run.agent_task.status === "failed"
                                    ? "destructive"
                                    : run.agent_task.status === "succeeded"
                                      ? "secondary"
                                      : "outline"
                                }
                              >
                                {TASK_STATUS_LABELS[run.agent_task.status]}
                                {run.agent_task.attempts > 1 ? ` · ${run.agent_task.attempts} attempts` : ""}
                              </Badge>
                              {run.agent_task.notification_id !== null && (
                                <Link
                                  className="text-primary underline underline-offset-4"
                                  to={`/notifications?id=${run.agent_task.notification_id}`}
                                >
                                  Open notification
                                </Link>
                              )}
                            </div>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {changes.length > 0 && (
                  <div>
                    <h3 className="mb-1 font-medium">Changes</h3>
                    <ul className="flex flex-col gap-1">
                      {changes.map((line) => (
                        <li key={line}>{line}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <details>
                  <summary className="cursor-pointer">Raw payload</summary>
                  <pre className="mt-2 max-h-96 overflow-auto rounded-md bg-muted p-3 text-xs">
                    {JSON.stringify(selected.payload, null, 2)}
                  </pre>
                </details>
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}
