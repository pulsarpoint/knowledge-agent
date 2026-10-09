import { useEffect, useState } from "react";
import { Inbox, RefreshCw } from "lucide-react";
import { ApiError } from "../lib/api";
import { SOURCE_LABELS, TYPE_LABELS, describeChanges, getEvent, getEventFacets, getEvents, type EventDetail, type EventFilters, type EventRow } from "../lib/events";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
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

export default function EventsView() {
  const [filters, setFilters] = useState<EventFilters>({ source: "", type: "", q: "" });
  const [query, setQuery] = useState("");
  const [events, setEvents] = useState<EventRow[] | null>(null);
  const [nextBefore, setNextBefore] = useState<number | null>(null);
  const [facets, setFacets] = useState<{ sources: string[]; types: string[] }>({ sources: [], types: [] });
  const [selected, setSelected] = useState<EventDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const timer = setTimeout(() => setQuery(filters.q), DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [filters.q]);

  const active: EventFilters = { source: filters.source, type: filters.type, q: query };

  useEffect(() => {
    const controller = new AbortController();
    getEventFacets(controller.signal).then(setFacets).catch(reason => { if (!controller.signal.aborted) setError(text(reason, "Could not load the filters.")); });
    return () => controller.abort();
  }, [reload]);

  useEffect(() => {
    const controller = new AbortController();
    setEvents(null);
    getEvents({ source: filters.source, type: filters.type, q: query }, undefined, controller.signal)
      .then(result => { setEvents(result.events); setNextBefore(result.next_before); setError(null); })
      .catch(reason => { if (!controller.signal.aborted) { setEvents([]); setNextBefore(null); setError(text(reason, "Could not load events.")); } });
    return () => controller.abort();
  }, [filters.source, filters.type, query, reload]);

  async function loadMore() {
    if (nextBefore === null) return;
    const sent = active;
    setLoadingMore(true);
    try {
      const result = await getEvents(sent, nextBefore);
      setEvents(current => current && sent.source === filters.source && sent.type === filters.type && sent.q === query ? [...current, ...result.events] : current);
      setNextBefore(result.next_before);
    } catch (reason) { setError(text(reason, "Could not load more events.")); }
    finally { setLoadingMore(false); }
  }
  async function open(row: EventRow) {
    try { setSelected((await getEvent(row.id)).event); }
    catch (reason) { setError(text(reason, "Could not load the event.")); }
  }

  const filtered = Boolean(filters.source || filters.type || filters.q.trim());
  const changes = selected ? describeChanges(selected.payload.changes) : [];
  const url = selected && typeof selected.payload.url === "string" ? selected.payload.url : null;

  return <div className="flex flex-col gap-4">
    <div className="flex flex-wrap items-end gap-3">
      <Field className="w-full sm:w-44"><FieldLabel htmlFor="events-source">Source</FieldLabel>
        <Select value={filters.source || ALL} onValueChange={value => setFilters(current => ({ ...current, source: value === ALL ? "" : value }))}>
          <SelectTrigger id="events-source" aria-label="Source" className="w-full"><SelectValue /></SelectTrigger>
          <SelectContent><SelectGroup><SelectItem value={ALL}>All sources</SelectItem>
            {facets.sources.map(source => <SelectItem key={source} value={source}>{sourceLabel(source)}</SelectItem>)}</SelectGroup></SelectContent>
        </Select></Field>
      <Field className="w-full sm:w-52"><FieldLabel htmlFor="events-type">Type</FieldLabel>
        <Select value={filters.type || ALL} onValueChange={value => setFilters(current => ({ ...current, type: value === ALL ? "" : value }))}>
          <SelectTrigger id="events-type" aria-label="Type" className="w-full"><SelectValue /></SelectTrigger>
          <SelectContent><SelectGroup><SelectItem value={ALL}>All types</SelectItem>
            {facets.types.map(type => <SelectItem key={type} value={type}>{typeLabel(type)}</SelectItem>)}</SelectGroup></SelectContent>
        </Select></Field>
      <Field className="w-full sm:w-64"><FieldLabel htmlFor="events-search">Ticket or title</FieldLabel>
        <Input id="events-search" value={filters.q} onChange={event => { const q = event.target.value; setFilters(current => ({ ...current, q })); }} /></Field>
      <Button variant="outline" onClick={() => setReload(count => count + 1)}><RefreshCw data-icon="inline-start" />Refresh</Button>
    </div>
    {error && <Alert variant="destructive"><AlertTitle>Events</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
    {events === null && !error && <Skeleton className="h-32 w-full" />}
    {events?.length === 0 && !error && <Empty className="border"><EmptyHeader><EmptyMedia variant="icon"><Inbox /></EmptyMedia><EmptyTitle>Events</EmptyTitle>
      <EmptyDescription>{filtered ? "No events match these filters." : "No events yet. Turn on Jira → Auto pull to record ticket changes."}</EmptyDescription></EmptyHeader></Empty>}
    {events && events.length > 0 && <div className="overflow-x-auto rounded-md border">
      <Table>
        <TableHeader><TableRow><TableHead>Time</TableHead><TableHead>Type</TableHead><TableHead>Subject</TableHead><TableHead>Title</TableHead></TableRow></TableHeader>
        <TableBody>{events.map(row => <TableRow key={row.id} tabIndex={0} className="cursor-pointer" onClick={() => void open(row)}
          onKeyDown={event => { if (event.key === "Enter") void open(row); }}>
          <TableCell className="whitespace-nowrap">{when(row.occurred_at)}</TableCell>
          <TableCell className="whitespace-nowrap">{typeLabel(row.type)}</TableCell>
          <TableCell className="font-medium">{row.subject}</TableCell>
          <TableCell>{row.title}</TableCell>
        </TableRow>)}</TableBody>
      </Table>
    </div>}
    {nextBefore !== null && <div className="flex justify-center"><Button variant="outline" disabled={loadingMore} onClick={() => void loadMore()}>Load more</Button></div>}
    <Sheet open={selected !== null} onOpenChange={next => { if (!next) setSelected(null); }}>
      <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-lg">
        {selected && <>
          <SheetHeader><SheetTitle>{selected.subject} · {typeLabel(selected.type)}</SheetTitle><SheetDescription>{selected.title}</SheetDescription></SheetHeader>
          <div className="flex flex-col gap-4 px-4 pb-4 text-sm">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
              <dt className="text-muted-foreground">Happened</dt><dd>{when(selected.occurred_at)}</dd>
              <dt className="text-muted-foreground">Recorded</dt><dd>{when(selected.created_at)}</dd>
              <dt className="text-muted-foreground">Source</dt><dd>{sourceLabel(selected.source)}</dd>
            </dl>
            {url && <a href={url} target="_blank" rel="noreferrer" className="text-primary underline underline-offset-4">Open in Jira</a>}
            {changes.length > 0 && <div><h3 className="mb-1 font-medium">Changes</h3><ul className="flex flex-col gap-1">{changes.map(line => <li key={line}>{line}</li>)}</ul></div>}
            <details><summary className="cursor-pointer">Raw payload</summary>
              <pre className="mt-2 max-h-96 overflow-auto rounded-md bg-muted p-3 text-xs">{JSON.stringify(selected.payload, null, 2)}</pre></details>
          </div>
        </>}
      </SheetContent>
    </Sheet>
  </div>;
}
