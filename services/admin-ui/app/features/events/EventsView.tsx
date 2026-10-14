import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { ArrowDown, ArrowUp, ArrowUpDown, Copy, Inbox, Plus, RefreshCw } from "lucide-react";
import { ApiError } from "~/lib/api";
import { DEFAULT_SORT, NO_FILTERS, SOURCE_LABELS, STATUS_LABELS, TASK_STATUS_LABELS, TYPE_LABELS, describeChanges, getEvent, getEventFacets, getEvents,
  type EventDetail, type EventFacets, type EventFilters, type EventRow, type EventSort, type EventStatus, type SortKey } from "./api";
import { statusLabels } from "~/lib/sessions";
import NewEventSheet, { type Prefill } from "./NewEventSheet";
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
const text = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback;
const typeLabel = (type: string) => TYPE_LABELS[type] ?? type;
const sourceLabel = (source: string) => SOURCE_LABELS[source] ?? source;
const COLUMNS: { key: SortKey | null; label: string }[] = [
  { key: "time", label: "Time" }, { key: "source", label: "Source" }, { key: "type", label: "Type" },
  { key: "subject", label: "Subject" }, { key: null, label: "Title" }, { key: "status", label: "Status" }];

export function StatusBadge({ status }: { status: EventStatus }) {
  return <Badge variant={status === "failed" ? "destructive" : status === "pending" ? "outline" : "secondary"}>{STATUS_LABELS[status] ?? status}</Badge>;
}

function FilterSelect({ id, label, value, all, options, onChange, className }: { id: string; label: string; value: string; all: string;
  options: { value: string; label: string }[]; onChange: (value: string) => void; className: string }) {
  return <Field className={className}><FieldLabel htmlFor={id}>{label}</FieldLabel>
    <Select value={value || ALL} onValueChange={next => onChange(next === ALL ? "" : next)}>
      <SelectTrigger id={id} aria-label={label} className="w-full"><SelectValue /></SelectTrigger>
      <SelectContent><SelectGroup><SelectItem value={ALL}>{all}</SelectItem>
        {options.map(option => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}</SelectGroup></SelectContent>
    </Select></Field>;
}

export default function EventsView() {
  const [params, setParams] = useSearchParams();
  const linked = params.get("event");
  const [filters, setFilters] = useState<EventFilters>(NO_FILTERS);
  const [sort, setSort] = useState<EventSort>(DEFAULT_SORT);
  const [query, setQuery] = useState("");
  const [events, setEvents] = useState<EventRow[] | null>(null);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [facets, setFacets] = useState<EventFacets>({ sources: [], types: [], statuses: [], handlers: [] });
  const [selected, setSelected] = useState<EventDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [reload, setReload] = useState(0);
  /** The New event form: null when closed, else what it starts with (a copy, or nothing). */
  const [form, setForm] = useState<{ key: number; prefill?: Prefill } | null>(null);
  /** Bumped whenever the first page is reloaded, so a Load more that started earlier is ignored. */
  const generation = useRef(0);

  useEffect(() => {
    const timer = setTimeout(() => setQuery(filters.q), DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [filters.q]);

  const active: EventFilters = { ...filters, q: query };

  useEffect(() => {
    const controller = new AbortController();
    getEventFacets(controller.signal).then(setFacets).catch(reason => { if (!controller.signal.aborted) setError(text(reason, "Could not load the filters.")); });
    return () => controller.abort();
  }, [reload]);

  useEffect(() => {
    const controller = new AbortController();
    generation.current += 1;
    setEvents(null); setNextOffset(null); setLoadingMore(false);
    getEvents(active, sort, 0, controller.signal)
      .then(result => { setEvents(result.events); setNextOffset(result.next_offset); setError(null); })
      .catch(reason => { if (!controller.signal.aborted) { setEvents([]); setNextOffset(null); setError(text(reason, "Could not load events.")); } });
    return () => controller.abort();
  }, [filters.source, filters.type, filters.status, filters.handler, query, sort.key, sort.order, reload]);  // eslint-disable-line react-hooks/exhaustive-deps

  async function loadMore() {
    if (nextOffset === null) return;
    const started = generation.current;
    setLoadingMore(true);
    try {
      const result = await getEvents(active, sort, nextOffset);
      if (generation.current !== started) return;
      setEvents(current => current ? [...current, ...result.events.filter(row => !current.some(existing => existing.id === row.id))] : current);
      setNextOffset(result.next_offset);
    } catch (reason) { if (generation.current === started) setError(text(reason, "Could not load more events.")); }
    finally { if (generation.current === started) setLoadingMore(false); }
  }
  async function open(id: number) {
    try { setSelected((await getEvent(id)).event); }
    catch (reason) { setError(text(reason, "Could not load the event.")); }
  }
  useEffect(() => {
    if (linked && /^[1-9]\d*$/.test(linked)) void open(Number(linked));
  }, [linked]);  // eslint-disable-line react-hooks/exhaustive-deps
  function close() {
    setSelected(null);
    if (params.has("event")) setParams(current => { current.delete("event"); return current; }, { replace: true });
  }
  /** A new column sorts newest/last first; the same column again flips the order. */
  const sortBy = (key: SortKey) => setSort(current => current.key === key ? { key, order: current.order === "desc" ? "asc" : "desc" } : { key, order: "desc" });
  const set = (field: keyof EventFilters) => (value: string) => setFilters(current => ({ ...current, [field]: value }));

  function created(event: EventDetail) {
    setForm(null);
    setReload(count => count + 1);
    setSelected(event);
  }
  /** Closes the event's sheet first, so the two sheets do not stack. */
  function duplicate(event: EventDetail) {
    close();
    setForm({ key: Date.now(), prefill: {
      source: event.source === "jira" ? "jira" : "manual", type: event.type, subject: event.subject, title: event.title, payload: event.payload } });
  }

  const filtered = Boolean(filters.source || filters.type || filters.status || filters.handler || filters.q.trim());
  const changes = selected ? describeChanges(selected.payload.changes) : [];
  /** The API caps the chain, so the open event may be missing from it. */
  const chain = selected ? (selected.chain.some(node => node.id === selected.id) ? selected.chain : [selected, ...selected.chain]) : [];
  const url = selected && typeof selected.payload.url === "string" && /^https?:\/\//i.test(selected.payload.url) ? selected.payload.url : null;

  return <div className="flex flex-col gap-4">
    <div className="flex flex-wrap items-end gap-3">
      <FilterSelect id="events-source" label="Source" all="All sources" className="w-full sm:w-40" value={filters.source} onChange={set("source")}
        options={facets.sources.map(source => ({ value: source, label: sourceLabel(source) }))} />
      <FilterSelect id="events-type" label="Type" all="All types" className="w-full sm:w-52" value={filters.type} onChange={set("type")}
        options={facets.types.map(type => ({ value: type, label: typeLabel(type) }))} />
      <FilterSelect id="events-status" label="Status" all="All statuses" className="w-full sm:w-40" value={filters.status} onChange={set("status")}
        options={facets.statuses.map(status => ({ value: status, label: STATUS_LABELS[status] ?? status }))} />
      <FilterSelect id="events-handler" label="Emitted by" all="All handlers" className="w-full sm:w-44" value={filters.handler} onChange={set("handler")}
        options={facets.handlers.map(handler => ({ value: handler.id, label: handler.name }))} />
      <Field className="w-full sm:w-56"><FieldLabel htmlFor="events-search">Ticket or title</FieldLabel>
        <Input id="events-search" value={filters.q} onChange={event => set("q")(event.target.value)} /></Field>
      <Button variant="outline" onClick={() => setReload(count => count + 1)}><RefreshCw data-icon="inline-start" />Refresh</Button>
      <Button onClick={() => setForm({ key: Date.now() })}><Plus data-icon="inline-start" />New event</Button>
    </div>
    {error && <Alert variant="destructive"><AlertTitle>Events</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
    {events === null && !error && <Skeleton className="h-32 w-full" />}
    {events?.length === 0 && !error && <Empty className="border"><EmptyHeader><EmptyMedia variant="icon"><Inbox /></EmptyMedia><EmptyTitle>Events</EmptyTitle>
      <EmptyDescription>{filtered ? "No events match these filters." : "No events yet. Turn on Jira → Auto pull to record ticket changes."}</EmptyDescription></EmptyHeader></Empty>}
    {events && events.length > 0 && <div className="overflow-x-auto rounded-md border">
      <Table>
        <TableHeader><TableRow>{COLUMNS.map(column => {
          const sorted = column.key !== null && sort.key === column.key;
          const Arrow = !sorted ? ArrowUpDown : sort.order === "asc" ? ArrowUp : ArrowDown;
          return <TableHead key={column.label} aria-sort={sorted ? (sort.order === "asc" ? "ascending" : "descending") : undefined}>
            {column.key === null ? column.label
              : <Button variant="ghost" size="sm" className="-ml-2" onClick={() => sortBy(column.key as SortKey)}>{column.label}<Arrow data-icon="inline-end" aria-hidden="true" /></Button>}
          </TableHead>;
        })}</TableRow></TableHeader>
        <TableBody>{events.map(row => <TableRow key={row.id} tabIndex={0} className="cursor-pointer" onClick={() => void open(row.id)}
          onKeyDown={event => { if (event.key === "Enter") void open(row.id); }}>
          <TableCell className="whitespace-nowrap">{when(row.occurred_at)}</TableCell>
          <TableCell className="whitespace-nowrap">{sourceLabel(row.source)}</TableCell>
          <TableCell className="whitespace-nowrap">{typeLabel(row.type)}</TableCell>
          <TableCell className="font-medium">{row.subject}</TableCell>
          <TableCell>{row.title}</TableCell>
          <TableCell><StatusBadge status={row.status} /></TableCell>
        </TableRow>)}</TableBody>
      </Table>
    </div>}
    {nextOffset !== null && <div className="flex justify-center"><Button variant="outline" disabled={loadingMore} onClick={() => void loadMore()}>Load more</Button></div>}
    {form && <NewEventSheet key={form.key} prefill={form.prefill} types={facets.types} onClose={() => setForm(null)} onCreated={created} />}
    <Sheet open={selected !== null} onOpenChange={next => { if (!next) close(); }}>
      <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-lg">
        {selected && <>
          <SheetHeader><SheetTitle>{selected.subject} · {typeLabel(selected.type)}</SheetTitle><SheetDescription>{selected.title}</SheetDescription></SheetHeader>
          <div className="flex flex-col gap-4 px-4 pb-4 text-sm">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
              <dt className="text-muted-foreground">Happened</dt><dd>{when(selected.occurred_at)}</dd>
              <dt className="text-muted-foreground">Recorded</dt><dd>{when(selected.created_at)}</dd>
              <dt className="text-muted-foreground">Source</dt><dd>{sourceLabel(selected.source)}</dd>
              <dt className="text-muted-foreground">Status</dt><dd><StatusBadge status={selected.status} /></dd>
              {selected.created_manually && <><dt className="text-muted-foreground">Origin</dt><dd>Created by hand</dd></>}
              {selected.parent && <><dt className="text-muted-foreground">Parent</dt>
                <dd><Button variant="link" className="h-auto p-0" onClick={() => void open(selected.parent!.id)}>{selected.parent.subject} · {typeLabel(selected.parent.type)}</Button></dd></>}
            </dl>
            <Button variant="outline" size="sm" className="self-start" onClick={() => duplicate(selected)}><Copy data-icon="inline-start" />Duplicate</Button>
            {url && <a href={url} target="_blank" rel="noreferrer" className="text-primary underline underline-offset-4">Open in Jira</a>}
            {chain.length > 1 && <div><h3 className="mb-1 font-medium">Chain</h3>
              <ul aria-label="Chain" className="flex flex-col gap-1">{chain.map(node => <li key={node.id} style={{ paddingLeft: `${node.depth}rem` }}>
                {node.id === selected.id
                  ? <span aria-current="true" className="font-medium">{node.subject} · {typeLabel(node.type)}</span>
                  : <Button variant="link" className="h-auto p-0" onClick={() => void open(node.id)}>{node.subject} · {typeLabel(node.type)}</Button>}
                {" "}<StatusBadge status={node.status} /></li>)}</ul></div>}
            {selected.runs.length > 0 && <div><h3 className="mb-1 font-medium">Handler runs</h3>
              <ul className="flex flex-col gap-2">{selected.runs.map((run, index) => <li key={index} className="flex flex-col gap-1">
                <div className="flex items-center gap-2"><span className="font-medium">{run.handler_name}</span>
                  <Badge variant={run.status === "failed" ? "destructive" : "secondary"}>{run.status === "failed" ? "Failed" : "Succeeded"}</Badge></div>
                {run.message && <span className="text-muted-foreground">{run.message}</span>}
                {run.emitted_event_id !== null && <Button variant="link" className="h-auto self-start p-0" onClick={() => void open(run.emitted_event_id!)}>Open event #{run.emitted_event_id}</Button>}
                {run.run && <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={run.run.status === "failed" ? "destructive" : "outline"}>{statusLabels[run.run.status]}</Badge>
                  <Link className="text-primary underline underline-offset-4"
                    to={`/agents/${encodeURIComponent(run.run.agent_id)}?view=test&session_id=${encodeURIComponent(run.run.id)}`}>Open run</Link>
                  {run.run.notification_id != null && <Link className="text-primary underline underline-offset-4"
                    to={`/notifications?id=${run.run.notification_id}`}>Open notification</Link>}</div>}
                {!run.run && run.agent_session_id && run.agent_id && <Link className="text-primary underline underline-offset-4"
                  to={`/agents/${encodeURIComponent(run.agent_id)}?view=test&session_id=${encodeURIComponent(run.agent_session_id)}`}>Open agent session</Link>}
                {run.agent_task && <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={run.agent_task.status === "failed" ? "destructive" : run.agent_task.status === "succeeded" ? "secondary" : "outline"}>
                    {TASK_STATUS_LABELS[run.agent_task.status]}{run.agent_task.attempts > 1 ? ` · ${run.agent_task.attempts} attempts` : ""}</Badge>
                  {run.agent_task.notification_id !== null && <Link className="text-primary underline underline-offset-4"
                    to={`/notifications?id=${run.agent_task.notification_id}`}>Open notification</Link>}</div>}
              </li>)}</ul></div>}
            {changes.length > 0 && <div><h3 className="mb-1 font-medium">Changes</h3><ul className="flex flex-col gap-1">{changes.map(line => <li key={line}>{line}</li>)}</ul></div>}
            <details><summary className="cursor-pointer">Raw payload</summary>
              <pre className="mt-2 max-h-96 overflow-auto rounded-md bg-muted p-3 text-xs">{JSON.stringify(selected.payload, null, 2)}</pre></details>
          </div>
        </>}
      </SheetContent>
    </Sheet>
  </div>;
}
