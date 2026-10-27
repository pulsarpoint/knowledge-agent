import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Link, useSearchParams } from "react-router";
import { FileText, Plus, RefreshCw } from "lucide-react";
import { getDocuments, getDocumentResources, type DocumentResources, type DocumentSummary } from "./api";
import { discardDraft, draftPath, getDrafts, getNewDrafts, promoteDraft, type DraftSummary } from "./drafts";
import { DraftAuthor } from "./DraftView";
import TagLinks from "./TagLinks";
import { kindsOf } from "~/lib/resources";
import { Alert, AlertDescription } from "~/components/ui/alert";
import ConfirmRemove from "~/components/ConfirmRemove";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import { Empty, EmptyHeader, EmptyMedia, EmptyTitle, EmptyDescription } from "~/components/ui/empty";
import { Field, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Label } from "~/components/ui/label";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Skeleton } from "~/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "~/components/ui/tabs";

/** The resource filters above the list, and the link counts each document shows. */
const FILTERS = kindsOf(["code_projects", "services", "servers", "environments"]);
const COUNTED = kindsOf(["code_projects", "services", "servers"]);
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

/** Documents: the live documents (search, resource filters, tag, Has drafts) with the new documents' drafts above them,
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
  const discardButton = (draft: DraftSummary) => (
    <Button size="sm" variant="outline" aria-label={`Discard ${draft.title}`} onClick={() => setDiscarding(draft)}>
      Discard
    </Button>
  );
  return (
    <div className="flex flex-col gap-6">
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
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          {draftsView ? (loading ? "Drafts" : draftsLabel(drafts.length)) : `${total} documents`}
        </p>
        <div className="flex gap-2">
          <Button variant="outline" disabled={loading} onClick={() => setRefresh((n) => n + 1)}>
            <RefreshCw data-icon="inline-start" />
            Refresh
          </Button>
          <Button asChild>
            <Link to="/documents/new">
              <Plus data-icon="inline-start" />
              New document
            </Link>
          </Button>
        </div>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {!draftsView && (
          <Field>
            <FieldLabel htmlFor="document-query">Search documents</FieldLabel>
            <Input
              id="document-query"
              placeholder="Title, slug or description"
              value={params.get("q") || ""}
              onChange={(e) => filter("q", e.target.value)}
            />
          </Field>
        )}
        <Field>
          <FieldLabel htmlFor="filter-tag">Tag</FieldLabel>
          <Input
            id="filter-tag"
            placeholder="One tag, e.g. inventory"
            value={params.get("tag") || ""}
            onChange={(e) => filter("tag", e.target.value)}
          />
        </Field>
        {FILTERS.map(({ key: kind, field, label, plural }) => {
          const key = field.slice(0, -1); // the filter's query parameter: code_project_id, service_id, ...
          return (
            <Field key={kind}>
              <FieldLabel htmlFor={`filter-${kind}`}>{label}</FieldLabel>
              <Select value={params.get(key) || "all"} onValueChange={(v) => filter(key, v)}>
                <SelectTrigger id={`filter-${kind}`}>
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
            </Field>
          );
        })}
        {!draftsView && (
          <div className="flex items-center gap-2 self-end pb-2">
            <Checkbox
              id="filter-has-drafts"
              checked={params.get("has_drafts") === "true"}
              onCheckedChange={(on) => filter("has_drafts", on === true ? "true" : "")}
            />
            <Label htmlFor="filter-has-drafts">Has drafts</Label>
          </div>
        )}
      </div>
      {(error || discardError) && (
        <Alert variant="destructive">
          <AlertDescription>{error || discardError}</AlertDescription>
        </Alert>
      )}
      {draftsView &&
        (loading ? (
          <Skeleton className="h-48 w-full" />
        ) : (
          !error &&
          (drafts.length === 0 ? (
            <p className="text-sm text-muted-foreground">No matching drafts.</p>
          ) : (
            <section aria-label="Drafts" className="rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Draft</TableHead>
                    <TableHead>Document</TableHead>
                    <TableHead>Written by</TableHead>
                    <TableHead>Created</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {drafts.map((draft) => (
                    <TableRow key={draft.id}>
                      <TableCell className="max-w-md whitespace-normal">
                        <Link className="font-medium underline-offset-4 hover:underline" to={draftPath(draft)}>
                          {draft.title}
                        </Link>
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
                      </TableCell>
                      <TableCell className="whitespace-normal">
                        {draft.document_id ? (
                          <Link className="underline-offset-4 hover:underline" to={`/documents/${draft.document_id}`}>
                            {draft.document_title ?? "Open document"}
                          </Link>
                        ) : (
                          <span className="text-muted-foreground">New document</span>
                        )}
                      </TableCell>
                      <TableCell>
                        <DraftAuthor draft={draft} />
                      </TableCell>
                      <TableCell>{new Date(draft.created_at).toLocaleString()}</TableCell>
                      <TableCell>
                        <div className="flex justify-end gap-2">
                          <Button asChild size="sm" variant="outline">
                            <Link to={draftPath(draft)} aria-label={`Open ${draft.title}`}>
                              Open
                            </Link>
                          </Button>
                          {!draft.document_id && (
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={busy}
                              aria-label={`Publish ${draft.title}`}
                              onClick={() => void publish(draft)}
                            >
                              Publish
                            </Button>
                          )}
                          {discardButton(draft)}
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </section>
          ))
        ))}
      {!draftsView && newDrafts.length > 0 && (
        <section aria-label="New drafts" className="flex flex-col gap-2">
          <h2 className="text-sm font-semibold">New drafts</h2>
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Title</TableHead>
                  <TableHead>Written by</TableHead>
                  <TableHead>Updated</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {newDrafts.map((draft) => (
                  <TableRow key={draft.id}>
                    <TableCell>
                      <Link className="font-medium underline-offset-4 hover:underline" to={draftPath(draft)}>
                        {draft.title}
                      </Link>
                      <TagLinks tags={draft.tags} className="ml-2" />
                    </TableCell>
                    <TableCell>
                      <DraftAuthor draft={draft} />
                    </TableCell>
                    <TableCell>{new Date(draft.updated_at).toLocaleString()}</TableCell>
                    <TableCell>
                      <div className="flex justify-end gap-2">
                        <Button asChild size="sm" variant="outline">
                          <Link to={draftPath(draft)} aria-label={`Open ${draft.title}`}>
                            Open
                          </Link>
                        </Button>
                        {discardButton(draft)}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </section>
      )}
      {draftsView ? null : loading ? (
        <Skeleton className="h-48 w-full" />
      ) : (
        !error &&
        (rows.length === 0 ? (
          <Empty className="border">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <FileText />
              </EmptyMedia>
              <EmptyTitle>{total === 0 && params.size === 0 ? "No documents yet" : "No matching documents"}</EmptyTitle>
              <EmptyDescription>
                Create a Markdown document and connect it to the projects, services, and servers it describes.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <>
            <div className="rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Document</TableHead>
                    <TableHead>Purpose / audience</TableHead>
                    <TableHead>Version</TableHead>
                    <TableHead>Drafts</TableHead>
                    <TableHead>Updated</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((doc) => (
                    <TableRow key={doc.id}>
                      <TableCell className="max-w-md whitespace-normal">
                        <Link className="font-medium underline-offset-4 hover:underline" to={`/documents/${doc.id}`}>
                          {doc.title}
                        </Link>
                        <TagLinks tags={doc.tags} className="ml-2" />
                        <p className="mt-1 text-sm text-muted-foreground">{doc.description}</p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {COUNTED.map((kind) => `${doc[kind.field].length} ${kind.plural.toLowerCase()}`).join(" · ")}
                        </p>
                      </TableCell>
                      <TableCell>
                        <p>{doc.purpose}</p>
                        <span className="text-sm text-muted-foreground">
                          {doc.audience === "both"
                            ? "People and agents"
                            : doc.audience === "people"
                              ? "People"
                              : "Agents"}
                        </span>
                      </TableCell>
                      <TableCell>
                        <Badge variant="secondary">v{doc.version}</Badge>
                      </TableCell>
                      <TableCell>
                        {(doc.draft_count ?? 0) > 0 ? (
                          <Link className="underline-offset-4 hover:underline" to={`/documents/${doc.id}?tab=drafts`}>
                            {draftsLabel(doc.draft_count!)}
                          </Link>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell>{new Date(doc.updated_at).toLocaleString()}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </>
        ))
      )}
      {!draftsView && total > 50 && (
        <div className="flex items-center justify-between gap-3">
          <span className="text-sm text-muted-foreground">
            {offset + 1}–{Math.min(offset + 50, total)} of {total}
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              disabled={loading || offset === 0}
              onClick={() => filter("offset", String(Math.max(0, offset - 50)))}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              disabled={loading || offset + 50 >= total}
              onClick={() => filter("offset", String(offset + 50))}
            >
              Next
            </Button>
          </div>
        </div>
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
