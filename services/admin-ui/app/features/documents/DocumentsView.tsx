import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { FileText, Plus, RefreshCw } from "lucide-react";
import { getDocuments, getDocumentResources, type DocumentResources, type DocumentSummary } from "./api";
import { discardDraft, draftPath, getDrafts, getNewDrafts, type DraftSummary } from "./drafts";
import { DraftAuthor } from "./DraftView";
import { kindsOf } from "~/lib/resources";
import { Alert, AlertDescription } from "~/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "~/components/ui/alert-dialog";
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
/** The Drafts view's filters: the resource filters' query parameters, passed to GET /drafts as they are. */
function draftFilter(params: URLSearchParams): Record<string, string> {
  const filter: Record<string, string> = {};
  for (const { field } of FILTERS) {
    const key = field.slice(0, -1);
    const value = params.get(key);
    if (value) filter[key] = value;
  }
  return filter;
}

/** Documents: the live documents (search, resource filters, Has drafts) with the new documents' drafts above them, or,
 * on the Drafts tab (?view=drafts), every draft (new documents and drafts of documents) with the same resource filters. */
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
      : Promise.all([getDocuments(params, controller.signal), getNewDrafts(controller.signal)]).then(
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
          // The resource filters carry over; search, Has drafts and paging belong to the documents list.
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
              Create document
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
                    <TableHead>Updated</TableHead>
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
                      </TableCell>
                      <TableCell className="whitespace-normal">
                        {draft.document_id ? (
                          <Link className="underline-offset-4 hover:underline" to={`/documents/${draft.document_id}`}>
                            Open document
                          </Link>
                        ) : (
                          <span className="text-muted-foreground">
                            New document{draft.folder ? ` in ${draft.folder}/` : ""}
                          </span>
                        )}
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
                  <TableHead>Folder</TableHead>
                  <TableHead>Written by</TableHead>
                  <TableHead>Updated</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {newDrafts.map((draft) => (
                  <TableRow key={draft.id}>
                    <TableCell>
                      <Link
                        className="font-medium underline-offset-4 hover:underline"
                        to={`/documents/drafts/${draft.id}`}
                      >
                        {draft.title}
                      </Link>
                    </TableCell>
                    <TableCell>{draft.folder ?? "—"}</TableCell>
                    <TableCell>
                      <DraftAuthor draft={draft} />
                    </TableCell>
                    <TableCell>{new Date(draft.updated_at).toLocaleString()}</TableCell>
                    <TableCell>
                      <div className="flex justify-end gap-2">
                        <Button asChild size="sm" variant="outline">
                          <Link to={`/documents/drafts/${draft.id}`} aria-label={`Open ${draft.title}`}>
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
      <AlertDialog
        open={discarding !== null}
        onOpenChange={(open) => {
          if (!open && !busy) setDiscarding(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{`Discard “${discarding?.title ?? ""}”?`}</AlertDialogTitle>
            <AlertDialogDescription>
              {discarding?.document_id
                ? "The draft is deleted (the audit history keeps a record). The live document does not change."
                : "The draft is deleted (the audit history keeps a record). No document is created."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={busy}
              onClick={(e) => {
                e.preventDefault();
                void discard();
              }}
            >
              Discard draft
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
