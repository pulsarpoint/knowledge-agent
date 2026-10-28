import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Link, useSearchParams } from "react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { FileText, Plus, Upload } from "lucide-react";
import { getDocuments, getDocumentResources, type DocumentResources, type DocumentSummary } from "./api";
import { discardDraft, draftPath, getDrafts, getNewDrafts, promoteDraft, type DraftSummary } from "./drafts";
import { DraftAuthor } from "./DraftView";
import TagLinks from "./TagLinks";
import { kindsOf } from "~/lib/resources";
import { Alert, AlertDescription } from "~/components/ui/alert";
import ConfirmRemove from "~/components/ConfirmRemove";
import DataTable, { TableEmpty } from "~/components/DataTable";
import RowMenu, { deleteMenuItem } from "~/components/RowMenu";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import { Input } from "~/components/ui/input";
import { Label } from "~/components/ui/label";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "~/components/ui/tabs";

/** The resource filters above the list, and the link counts each document shows. */
const FILTERS = kindsOf(["code_projects", "services", "servers", "environments"]);
const COUNTED = kindsOf(["code_projects", "services", "servers"]);
/** The API's page size: a page of it leaves `limit` out of the URL. */
const DOCUMENTS_PAGE = 50;
const draftsLabel = (count: number) => (count === 1 ? "1 draft" : `${count} drafts`);
/** The parameters with the tag trimmed; a blank tag is no tag filter. */
function trimmedTag(params: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams(params);
  const tag = next.get("tag")?.trim();
  tag ? next.set("tag", tag) : next.delete("tag");
  return next;
}
/** The filters both tabs share: the resource filters' query parameters and the tag, passed to GET /drafts as they are. */
function draftFilter(params: URLSearchParams): Record<string, string> {
  const filter: Record<string, string> = {};
  for (const key of [...FILTERS.map(({ field }) => field.slice(0, -1)), "tag"]) {
    const value = key === "tag" ? params.get(key)?.trim() : params.get(key);
    if (value) filter[key] = value;
  }
  return filter;
}

/** Documents: the live documents in a table paged by the server (?offset=, ?limit=) with search, resource filters, tag
 * and Has drafts, and the new documents' drafts above them,
 * or, on the Drafts tab (?view=drafts), every draft (new documents and drafts of documents) with the same resource and
 * tag filters. Every tag badge links to the list filtered by that tag (?tag=). */
export default function DocumentsView() {
  const [params, setParams] = useSearchParams();
  const [rows, setRows] = useState<DocumentSummary[]>([]);
  const [resources, setResources] = useState<DocumentResources | null>(null);
  const [newDrafts, setNewDrafts] = useState<DraftSummary[]>([]);
  const [discarding, setDiscarding] = useState<DraftSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [discardError, setDiscardError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [drafts, setDrafts] = useState<DraftSummary[]>([]);
  const offset = Number(params.get("offset") || 0);
  const pageSize = Number(params.get("limit")) || DOCUMENTS_PAGE;
  const page = Math.floor(offset / pageSize) + 1;
  const draftsView = params.get("view") === "drafts";
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    const listed = draftsView
      ? getDrafts(draftFilter(params), controller.signal).then((value) => () => setDrafts(value.drafts))
      : Promise.all([getDocuments(trimmedTag(params), controller.signal), getNewDrafts(controller.signal)]).then(
          ([data, created]) =>
            () => {
              setRows(data.documents);
              setTotal(data.total);
              setNewDrafts(created.drafts);
            },
        );
    Promise.all([listed, getDocumentResources(controller.signal)])
      .then(([show, assets]) => {
        if (!controller.signal.aborted) {
          show();
          setResources(assets);
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Could not load documents");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [params, refresh]);
  function filter(key: string, value: string) {
    setParams(
      (old) => {
        const next = new URLSearchParams(old);
        value && value !== "all" ? next.set(key, value) : next.delete(key);
        if (key !== "offset") next.delete("offset");
        if (key === "limit" && value === String(DOCUMENTS_PAGE)) next.delete("limit");
        return next;
      },
      { replace: true },
    );
  }
  async function discard() {
    if (!discarding) return;
    setBusy(true);
    setDiscardError("");
    try {
      await discardDraft(discarding.id, discarding.revision);
      setDiscarding(null);
      setRefresh((n) => n + 1);
    } catch (e) {
      setDiscarding(null);
      setDiscardError(e instanceof Error ? e.message : "Could not discard the draft");
      setRefresh((n) => n + 1);
    } finally {
      setBusy(false);
    }
  }
  /** Publish a brand-new draft from the list: it becomes the live document (version 1, same id). */
  async function publish(draft: DraftSummary) {
    setBusy(true);
    setDiscardError("");
    try {
      await promoteDraft(draft.id, draft.revision, false);
      toast.success(`Published “${draft.title}”.`);
    } catch (e) {
      setDiscardError(e instanceof Error ? e.message : "Could not publish the draft");
    } finally {
      setBusy(false);
      setRefresh((n) => n + 1);
    }
  }
  const draftMenu = (draft: DraftSummary) => (
    <RowMenu name={draft.title} items={[deleteMenuItem(() => setDiscarding(draft), "Discard")]} />
  );
  const newDocument = (
    <Button asChild>
      <Link to="/documents/new">
        <Plus data-icon="inline-start" />
        New document
      </Link>
    </Button>
  );
  const draftTitle = (draft: DraftSummary) => (
    <Link className="font-medium underline-offset-4 hover:underline" to={draftPath(draft)}>
      {draft.title}
    </Link>
  );
  const draftColumns: ColumnDef<DraftSummary, unknown>[] = [
    {
      id: "draft",
      header: "Draft",
      cell: ({ row: { original: draft } }) => (
        <div className="max-w-md">
          {draftTitle(draft)}
          {draft.stale && (
            <Badge variant="destructive" className="ml-2">
              stale
            </Badge>
          )}
          {!draft.document_id && (
            <Badge variant="outline" className="ml-2">
              Not published
            </Badge>
          )}
          <TagLinks tags={draft.tags} className="ml-2" />
        </div>
      ),
    },
    {
      id: "document",
      header: "Document",
      cell: ({ row: { original: draft } }) =>
        draft.document_id ? (
          <Link className="underline-offset-4 hover:underline" to={`/documents/${draft.document_id}`}>
            {draft.document_title ?? "Open document"}
          </Link>
        ) : (
          <span className="text-muted-foreground">New document</span>
        ),
    },
    { id: "author", header: "Written by", cell: ({ row: { original: draft } }) => <DraftAuthor draft={draft} /> },
    {
      id: "created",
      header: "Created",
      cell: ({ row: { original: draft } }) => new Date(draft.created_at).toLocaleString(),
    },
    {
      id: "actions",
      header: () => <span className="sr-only">Actions</span>,
      cell: ({ row: { original: draft } }) => (
        <div className="flex items-center justify-end gap-1">
          {!draft.document_id && (
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              aria-label={`Publish ${draft.title}`}
              onClick={() => void publish(draft)}
            >
              <Upload data-icon="inline-start" />
              Publish
            </Button>
          )}
          {draftMenu(draft)}
        </div>
      ),
    },
  ];
  const newDraftColumns: ColumnDef<DraftSummary, unknown>[] = [
    {
      id: "title",
      header: "Title",
      cell: ({ row: { original: draft } }) => (
        <>
          {draftTitle(draft)}
          <TagLinks tags={draft.tags} className="ml-2" />
        </>
      ),
    },
    { id: "author", header: "Written by", cell: ({ row: { original: draft } }) => <DraftAuthor draft={draft} /> },
    {
      id: "updated",
      header: "Updated",
      cell: ({ row: { original: draft } }) => new Date(draft.updated_at).toLocaleString(),
    },
    {
      id: "actions",
      header: () => <span className="sr-only">Actions</span>,
      cell: ({ row: { original: draft } }) => <div className="flex justify-end">{draftMenu(draft)}</div>,
    },
  ];
  const documentColumns: ColumnDef<DocumentSummary, unknown>[] = [
    {
      id: "document",
      header: "Document",
      cell: ({ row: { original: doc } }) => (
        <div className="max-w-md">
          <Link className="font-medium underline-offset-4 hover:underline" to={`/documents/${doc.id}`}>
            {doc.title}
          </Link>
          <TagLinks tags={doc.tags} className="ml-2" />
          <p className="mt-1 text-sm text-muted-foreground">{doc.description}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {COUNTED.map((kind) => `${doc[kind.field].length} ${kind.plural.toLowerCase()}`).join(" · ")}
          </p>
        </div>
      ),
    },
    {
      id: "purpose",
      header: "Purpose / audience",
      cell: ({ row: { original: doc } }) => (
        <>
          <p>{doc.purpose}</p>
          <span className="text-sm text-muted-foreground">
            {doc.audience === "both" ? "People and agents" : doc.audience === "people" ? "People" : "Agents"}
          </span>
        </>
      ),
    },
    {
      id: "version",
      header: "Version",
      cell: ({ row: { original: doc } }) => <Badge variant="secondary">v{doc.version}</Badge>,
    },
    {
      id: "drafts",
      header: "Drafts",
      cell: ({ row: { original: doc } }) =>
        (doc.draft_count ?? 0) > 0 ? (
          <Link className="underline-offset-4 hover:underline" to={`/documents/${doc.id}?tab=drafts`}>
            {draftsLabel(doc.draft_count!)}
          </Link>
        ) : (
          <span className="text-muted-foreground">—</span>
        ),
    },
    {
      id: "updated",
      header: "Updated",
      cell: ({ row: { original: doc } }) => new Date(doc.updated_at).toLocaleString(),
    },
  ];
  /** The filters both tabs share (tag and resources); the documents list adds Has drafts. */
  const filters = (
    <>
      <Input
        aria-label="Tag"
        placeholder="One tag, e.g. inventory"
        className="w-full sm:w-48"
        value={params.get("tag") || ""}
        onChange={(e) => filter("tag", e.target.value)}
      />
      {FILTERS.map(({ key: kind, field, label, plural }) => {
        const key = field.slice(0, -1); // the filter's query parameter: code_project_id, service_id, ...
        return (
          <Select key={kind} value={params.get(key) || "all"} onValueChange={(v) => filter(key, v)}>
            <SelectTrigger aria-label={label} className="w-full sm:w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                <SelectItem value="all">All {plural.toLowerCase()}</SelectItem>
                {resources?.[kind]?.map((item) => (
                  <SelectItem key={item.id} value={item.id}>
                    {item.name}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
        );
      })}
      {!draftsView && (
        <div className="flex items-center gap-2">
          <Checkbox
            id="filter-has-drafts"
            checked={params.get("has_drafts") === "true"}
            onCheckedChange={(on) => filter("has_drafts", on === true ? "true" : "")}
          />
          <Label htmlFor="filter-has-drafts">Has drafts</Label>
        </div>
      )}
    </>
  );
  const filtered = [...params.keys()].some((key) => !["view", "offset", "limit"].includes(key));
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Tabs
          value={draftsView ? "drafts" : "documents"}
          onValueChange={(v) =>
            // The resource and tag filters carry over; search, Has drafts and paging belong to the documents list.
            setParams({ ...draftFilter(params), ...(v === "drafts" ? { view: "drafts" } : {}) }, { replace: true })
          }
        >
          <TabsList>
            <TabsTrigger value="documents">Documents</TabsTrigger>
            <TabsTrigger value="drafts">Drafts</TabsTrigger>
          </TabsList>
        </Tabs>
        {newDocument}
      </div>
      {(error || discardError) && (
        <Alert variant="destructive">
          <AlertDescription>{error || discardError}</AlertDescription>
        </Alert>
      )}
      {draftsView ? (
        <section aria-label="Drafts">
          <DataTable
            columns={draftColumns}
            data={error ? [] : drafts}
            loading={loading}
            rowId={(draft) => draft.id}
            resetKey={JSON.stringify(draftFilter(params))}
            toolbar={filters}
            empty={
              error ? (
                "Could not load drafts."
              ) : filtered ? (
                "No drafts match these filters."
              ) : (
                <TableEmpty
                  icon={FileText}
                  title="No drafts"
                  description="Drafts of new documents and of documents wait here until they are published or discarded."
                />
              )
            }
          />
        </section>
      ) : (
        <>
          {newDrafts.length > 0 && (
            <section aria-label="New drafts" className="flex flex-col gap-2">
              <h2 className="text-sm font-semibold">New drafts</h2>
              <DataTable
                columns={newDraftColumns}
                data={newDrafts}
                rowId={(draft) => draft.id}
                empty="No new drafts."
              />
            </section>
          )}
          <DataTable
            columns={documentColumns}
            data={error ? [] : rows}
            loading={loading}
            rowId={(doc) => doc.id}
            total={error ? 0 : total}
            page={page}
            pageSize={pageSize}
            onPageChange={(next) => filter("offset", String((next - 1) * pageSize))}
            onPageSizeChange={(size) => filter("limit", String(size))}
            search={{ plural: "documents", value: params.get("q") || "", onChange: (q) => filter("q", q) }}
            toolbar={filters}
            empty={
              error ? (
                "Could not load documents."
              ) : filtered ? (
                "No documents match these filters."
              ) : (
                <TableEmpty
                  icon={FileText}
                  title="No documents yet"
                  description="Create a Markdown document and connect it to the projects, services, and servers it describes."
                >
                  {newDocument}
                </TableEmpty>
              )
            }
          />
        </>
      )}
      <ConfirmRemove
        removal={
          discarding && {
            title: `Discard “${discarding.title}”?`,
            description: discarding.document_id
              ? "The draft is deleted (the audit history keeps a record). The live document does not change."
              : "The draft is deleted (the audit history keeps a record). No document is created.",
            action: "Discard draft",
            run: discard,
          }
        }
        onClose={() => setDiscarding(null)}
        busy={busy}
      />
    </div>
  );
}
