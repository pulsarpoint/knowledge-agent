import { useEffect, useRef, useState, type FormEvent } from "react";
import { Brain, Plus, RefreshCw, X } from "lucide-react";
import { ApiError } from "~/lib/api";
import {
  NO_MEMORY_FILTERS,
  getMemories,
  getMemoryAgents,
  reembedMemories,
  savedBy,
  type MemoryEntry,
  type MemoryFilters,
} from "./api";
import MemorySheet from "./MemorySheet";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "~/components/ui/empty";
import { Field, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Skeleton } from "~/components/ui/skeleton";
import { Spinner } from "~/components/ui/spinner";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

const ALL = "all";
const when = (value: string) => new Date(value).toLocaleString();
const text = (reason: unknown, fallback: string) => (reason instanceof ApiError ? reason.message : fallback);
const pair = (key: string, value: string) => `${key} = ${value}`;

/** Administration → Memory: what agents saved, shared by all agents. The page title comes from AdminLayout. */
export default function MemoryView() {
  const [filters, setFilters] = useState<MemoryFilters>(NO_MEMORY_FILTERS);
  const [search, setSearch] = useState("");
  const [agents, setAgents] = useState<{ id: string; name: string }[]>([]);
  const [rows, setRows] = useState<MemoryEntry[] | null>(null);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [notEmbedded, setNotEmbedded] = useState(0);
  const [embeddingModel, setEmbeddingModel] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [reembedding, setReembedding] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [openId, setOpenId] = useState<string | null>(null);
  /** Bumped whenever the first page is reloaded, so a Load more that started earlier is ignored. */
  const generation = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    getMemoryAgents(controller.signal)
      .then((result) => setAgents(result.agents))
      .catch(() => setAgents([]));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    generation.current += 1;
    setRows(null);
    setNextOffset(null);
    setLoadingMore(false);
    getMemories(filters, 0, controller.signal)
      .then((result) => {
        setRows(result.memories);
        setNextOffset(result.next_offset);
        setNotEmbedded(result.not_embedded);
        setEmbeddingModel(result.embedding_model);
        setError(null);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setRows([]);
          setNextOffset(null);
          setError(text(reason, "Could not load memory."));
        }
      });
    return () => controller.abort();
  }, [filters, reload]);

  const refresh = () => setReload((count) => count + 1);
  const addFilter = (key: string, value: string) =>
    setFilters((current) => ({ ...current, metadata: { ...current.metadata, [key]: value } }));
  const removeFilter = (key: string) =>
    setFilters((current) => {
      const { [key]: _removed, ...metadata } = current.metadata;
      return { ...current, metadata };
    });
  function submit(event: FormEvent) {
    event.preventDefault();
    setFilters((current) => ({ ...current, query: search }));
  }

  async function loadMore() {
    if (nextOffset === null) return;
    const started = generation.current;
    setLoadingMore(true);
    try {
      const result = await getMemories(filters, nextOffset);
      if (generation.current !== started) return;
      setRows((current) =>
        current
          ? [...current, ...result.memories.filter((item) => !current.some((existing) => existing.id === item.id))]
          : current,
      );
      setNextOffset(result.next_offset);
    } catch (reason) {
      if (generation.current === started) setError(text(reason, "Could not load more memory."));
    } finally {
      if (generation.current === started) setLoadingMore(false);
    }
  }
  async function reembed() {
    setReembedding(true);
    setNotice(null);
    try {
      const result = await reembedMemories();
      setNotice(result.failed ? `${result.failed} entries could not be embedded.` : null);
      refresh();
    } catch (reason) {
      setError(text(reason, "Could not re-embed the entries."));
    } finally {
      setReembedding(false);
    }
  }

  const filtered = Boolean(filters.query.trim() || filters.agent || Object.keys(filters.metadata).length);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <form onSubmit={submit} className="w-full sm:w-72">
          <Field>
            <FieldLabel htmlFor="memory-search">Search</FieldLabel>
            <Input
              id="memory-search"
              type="search"
              aria-label="Search memory"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </Field>
        </form>
        <Field className="w-full sm:w-48">
          <FieldLabel htmlFor="memory-agent">Saved by agent</FieldLabel>
          <Select
            value={filters.agent || ALL}
            onValueChange={(next) => setFilters((current) => ({ ...current, agent: next === ALL ? "" : next }))}
          >
            <SelectTrigger id="memory-agent" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                <SelectItem value={ALL}>All agents</SelectItem>
                {agents.map((agent) => (
                  <SelectItem key={agent.id} value={agent.id}>
                    {agent.name}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
        </Field>
        <Button variant="outline" onClick={refresh}>
          <RefreshCw data-icon="inline-start" />
          Refresh
        </Button>
        {notEmbedded > 0 && embeddingModel && (
          <Button variant="outline" disabled={reembedding} onClick={() => void reembed()}>
            {reembedding && <Spinner data-icon="inline-start" />}
            Re-embed ({notEmbedded})
          </Button>
        )}
        <Button className="sm:ml-auto" onClick={() => setOpenId("new")}>
          <Plus data-icon="inline-start" />
          New entry
        </Button>
      </div>
      {Object.keys(filters.metadata).length > 0 && (
        <div className="flex flex-wrap gap-2">
          {Object.entries(filters.metadata).map(([key, value]) => (
            <Badge key={key} variant="secondary" className="gap-1">
              {pair(key, value)}
              <button type="button" aria-label={`Remove filter ${pair(key, value)}`} onClick={() => removeFilter(key)}>
                <X className="size-3" />
              </button>
            </Badge>
          ))}
        </div>
      )}
      {!embeddingModel && (
        <p className="text-sm text-muted-foreground">
          No embedding model in RAG Settings: entries are found by words only.
        </p>
      )}
      {notice && (
        <Alert variant="destructive">
          <AlertTitle>Re-embed</AlertTitle>
          <AlertDescription>{notice}</AlertDescription>
        </Alert>
      )}
      {error && (
        <Alert variant="destructive">
          <AlertTitle>Memory</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {rows === null && !error && <Skeleton className="h-32 w-full" />}
      {rows?.length === 0 && !error && (
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <Brain />
            </EmptyMedia>
            <EmptyTitle>Memory</EmptyTitle>
            <EmptyDescription>
              {filtered
                ? "No entries match these filters."
                : "No memory entries yet. Agents with the Memory tools save them; you can add one."}
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      )}
      {rows && rows.length > 0 && (
        <div className="overflow-x-auto rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Metadata</TableHead>
                <TableHead>Saved by</TableHead>
                <TableHead>Updated</TableHead>
                <TableHead>Revision</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((item) => (
                <TableRow key={item.id}>
                  <TableCell className="font-medium">
                    <button type="button" className="text-left hover:underline" onClick={() => setOpenId(item.id)}>
                      {item.name}
                    </button>
                    {item.embedding_profile === null && (
                      <Badge variant="outline" className="ml-2">
                        Not embedded
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {Object.entries(item.metadata).map(([key, value]) => (
                        <button
                          type="button"
                          key={key}
                          aria-label={pair(key, value)}
                          onClick={() => addFilter(key, value)}
                        >
                          <Badge variant="secondary">{pair(key, value)}</Badge>
                        </button>
                      ))}
                    </div>
                  </TableCell>
                  <TableCell>{savedBy(item)}</TableCell>
                  <TableCell className="whitespace-nowrap">{when(item.updated_at)}</TableCell>
                  <TableCell>{item.revision}</TableCell>
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
      <MemorySheet id={openId} onClose={() => setOpenId(null)} onChanged={refresh} />
    </div>
  );
}
