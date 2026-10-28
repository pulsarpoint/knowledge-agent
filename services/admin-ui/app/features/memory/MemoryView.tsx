import { useEffect, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { useSearchParams } from "react-router";
import { toast } from "sonner";
import { Brain, Plus, RefreshCw, X } from "lucide-react";
import { ApiError } from "~/lib/api";
import {
  MEMORY_PAGE,
  NO_MEMORY_FILTERS,
  getMemories,
  getMemoryAgents,
  reembedMemories,
  isEmbedded,
  savedBy,
  type MemoryEntry,
  type MemoryFilters,
} from "./api";
import EmbeddingModelSelect from "./EmbeddingModelSelect";
import MemorySheet from "./MemorySheet";
import DataTable, { TableEmpty } from "~/components/DataTable";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Spinner } from "~/components/ui/spinner";

const ALL = "all";
const DEBOUNCE_MS = 400;
const when = (value: string) => new Date(value).toLocaleString();
const text = (reason: unknown, fallback: string) => (reason instanceof ApiError ? reason.message : fallback);
const pair = (key: string, value: string) => `${key} = ${value}`;

/** Administration → Memory: what agents saved, shared by all agents. The page title comes from AdminLayout. */
export default function MemoryView() {
  const [filters, setFilters] = useState<MemoryFilters>(NO_MEMORY_FILTERS);
  const [search, setSearch] = useState("");
  const [agents, setAgents] = useState<{ id: string; name: string }[]>([]);
  const [rows, setRows] = useState<MemoryEntry[] | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(MEMORY_PAGE);
  const [notEmbedded, setNotEmbedded] = useState(0);
  const [embeddingModel, setEmbeddingModel] = useState(true);
  const [currentProfile, setCurrentProfile] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reembedding, setReembedding] = useState(false);
  const [embedFailure, setEmbedFailure] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [params] = useSearchParams();
  const [openId, setOpenId] = useState<string | null>(params.get("entry"));

  useEffect(() => {
    const controller = new AbortController();
    getMemoryAgents(controller.signal)
      .then((result) => setAgents(result.agents))
      .catch(() => setAgents([]));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setRows(null);
    getMemories(filters, (page - 1) * pageSize, controller.signal, pageSize)
      .then((result) => {
        if (controller.signal.aborted) return; // a page answered after the filters changed
        setRows(result.memories);
        setTotal(result.total ?? result.memories.length);
        setNotEmbedded(result.not_embedded);
        setEmbeddingModel(result.embedding_model);
        setCurrentProfile(result.current_profile);
        setError(null);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setRows([]);
          setTotal(0);
          setError(text(reason, "Could not load memory."));
        }
      });
    return () => controller.abort();
  }, [filters, page, pageSize, reload]);

  // A search (it is embedded) waits for typing to pause; it starts at page 1.
  useEffect(() => {
    const timer = setTimeout(() => change({ query: search }), DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [search]); // eslint-disable-line react-hooks/exhaustive-deps

  const refresh = () => setReload((count) => count + 1);
  /** A filter change starts at page 1; an unchanged search keeps the page. */
  function change(next: Partial<MemoryFilters>) {
    setFilters((current) => {
      const updated = { ...current, ...next };
      return JSON.stringify(updated) === JSON.stringify(current) ? current : updated;
    });
    if (next.query === undefined || next.query.trim() !== filters.query.trim()) setPage(1);
  }
  const addFilter = (key: string, value: string) => change({ metadata: { ...filters.metadata, [key]: value } });
  const removeFilter = (key: string) => {
    const { [key]: _removed, ...metadata } = filters.metadata;
    change({ metadata });
  };

  async function reembed() {
    setReembedding(true);
    setEmbedFailure(null);
    try {
      const result = await reembedMemories();
      if (result.failed) setEmbedFailure(`${result.failed} entries could not be embedded.`);
      else toast.success(`${result.embedded} entries re-embedded.`);
      refresh();
    } catch (reason) {
      setError(text(reason, "Could not re-embed the entries."));
    } finally {
      setReembedding(false);
    }
  }

  const filtered = Boolean(filters.query.trim() || filters.agent || Object.keys(filters.metadata).length);
  const newEntry = (
    <Button onClick={() => setOpenId("new")}>
      <Plus data-icon="inline-start" />
      New entry
    </Button>
  );
  const columns: ColumnDef<MemoryEntry, unknown>[] = [
    {
      id: "name",
      header: "Name",
      cell: ({ row: { original: item } }) => (
        <span className="font-medium">
          <button type="button" className="text-left hover:underline" onClick={() => setOpenId(item.id)}>
            {item.name}
          </button>
          {!isEmbedded(item, currentProfile) && (
            <Badge variant="outline" className="ml-2">
              Not embedded
            </Badge>
          )}
        </span>
      ),
    },
    {
      id: "metadata",
      header: "Metadata",
      cell: ({ row: { original: item } }) => (
        <div className="flex flex-wrap gap-1">
          {Object.entries(item.metadata).map(([key, value]) => (
            <button type="button" key={key} aria-label={pair(key, value)} onClick={() => addFilter(key, value)}>
              <Badge variant="secondary">{pair(key, value)}</Badge>
            </button>
          ))}
        </div>
      ),
    },
    { id: "saved", header: "Saved by", cell: ({ row: { original: item } }) => savedBy(item) },
    {
      id: "updated",
      header: "Updated",
      cell: ({ row: { original: item } }) => <span className="whitespace-nowrap">{when(item.updated_at)}</span>,
    },
    { id: "revision", header: "Revision", cell: ({ row: { original: item } }) => item.revision },
  ];
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-end gap-2">
        <EmbeddingModelSelect onChange={refresh} onError={setError} />
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
        {newEntry}
      </div>
      {!embeddingModel && (
        <p className="text-sm text-muted-foreground">No embedding model selected: entries are found by words only.</p>
      )}
      {embedFailure && (
        <Alert variant="destructive">
          <AlertTitle>Re-embed</AlertTitle>
          <AlertDescription>{embedFailure}</AlertDescription>
        </Alert>
      )}
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <DataTable
        columns={columns}
        data={rows ?? []}
        loading={rows === null}
        rowId={(item) => item.id}
        total={total}
        page={page}
        pageSize={pageSize}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPageSize(size);
          setPage(1);
        }}
        search={{ plural: "memory", value: search, onChange: setSearch }}
        toolbar={
          <>
            <Select value={filters.agent || ALL} onValueChange={(next) => change({ agent: next === ALL ? "" : next })}>
              <SelectTrigger aria-label="Saved by agent" className="w-full sm:w-48">
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
            {Object.entries(filters.metadata).map(([key, value]) => (
              <Badge key={key} variant="secondary" className="gap-1">
                {pair(key, value)}
                <button
                  type="button"
                  aria-label={`Remove filter ${pair(key, value)}`}
                  onClick={() => removeFilter(key)}
                >
                  <X className="size-3" />
                </button>
              </Badge>
            ))}
          </>
        }
        empty={
          error ? (
            "Could not load memory."
          ) : filtered ? (
            "No entries match these filters."
          ) : (
            <TableEmpty
              icon={Brain}
              title="No memory entries yet"
              description="Agents with the Memory tools save them; you can add one."
            >
              {newEntry}
            </TableEmpty>
          )
        }
      />
      <MemorySheet id={openId} onClose={() => setOpenId(null)} onChanged={refresh} />
    </div>
  );
}
