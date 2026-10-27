import { useEffect, useState } from "react";
import { Link } from "react-router";
import { Pencil, Plus, Trash2 } from "lucide-react";
import {
  getEntryDocuments,
  newDocumentPath,
  type EntryDocument,
  type EntryDocuments as Documents,
  type MapEntry,
} from "./inventoryMap";
import { RowAction, RowActions } from "./RowAction";
import { deleteDocument } from "~/features/documents/api";
import { discardDraft } from "~/features/documents/drafts";
import Markdown from "~/components/Markdown";
import TagLinks from "~/features/documents/TagLinks";
import ConfirmRemove from "~/components/ConfirmRemove";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";

/** A resource page's documents: New document (the New page built for the resource); its inventory document rendered
 * when it has one (Open edits it through drafts as usual); its document list (its and its direct children's documents:
 * title, tags (each a link to the Documents list filtered by it), short description, and the child's name for a child's
 * document), each row with Edit (the document's page; a new document's draft's too) and Delete (asks first; a new
 * document's draft is discarded). */
export default function EntryDocuments({
  entry,
  onChanged,
  ownOnly = false,
  refresh = 0,
}: {
  entry: MapEntry;
  onChanged?: () => void;
  /** Changing it reloads the documents (the page chat's agent may have drafted some). */
  refresh?: number;
  /** Only the entry's own documents, not its children's (the environment page: each server and service shows its own). */
  ownOnly?: boolean;
}) {
  const [documents, setDocuments] = useState<Documents | null>(null);
  const [version, setVersion] = useState(0);
  const [deleting, setDeleting] = useState<EntryDocument | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    getEntryDocuments(entry, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setDocuments(value);
      })
      .catch(() => undefined); // nothing shown
    return () => controller.abort();
  }, [entry.kind, entry.id, version, refresh]);
  async function remove(item: EntryDocument) {
    setDeleting(null);
    setError("");
    try {
      if (item.is_draft) await discardDraft(item.id, item.revision);
      else await deleteDocument(item.id, item.revision);
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not delete the document");
    }
    setVersion((value) => value + 1);
  }
  const inventory = documents?.inventory_document ?? null;
  const list = (documents?.documents ?? []).filter((item) => !ownOnly || item.resource.id === entry.id);
  return (
    <section aria-label="Documents" className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">Documents</h2>
        <Button asChild variant="outline" size="sm">
          <Link to={newDocumentPath(entry)}>
            <Plus data-icon="inline-start" />
            New document
          </Link>
        </Button>
      </div>
      {inventory && entry.kind !== "code_project" && (
        <section aria-label="Inventory document" className="flex flex-col gap-2 rounded-lg border p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="font-semibold">{inventory.title}</h3>
              {inventory.is_draft && <Badge variant="outline">Draft — not published</Badge>}
            </div>
            <Button asChild size="sm" variant="outline">
              <Link to={`/documents/${encodeURIComponent(inventory.id)}`}>Open</Link>
            </Button>
          </div>
          <div className="min-w-0">
            <Markdown content={inventory.markdown} />
          </div>
        </section>
      )}
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {documents &&
        (list.length === 0 ? (
          <p className="text-sm text-muted-foreground">No documents yet.</p>
        ) : (
          <ul aria-label="Documents" className="flex flex-col gap-2 text-sm">
            {list.map((item) => (
              <li key={item.id} className="flex items-start justify-between gap-2">
                <div className="flex min-w-0 flex-col gap-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Link className="font-medium underline" to={`/documents/${encodeURIComponent(item.id)}`}>
                      {item.title}
                    </Link>
                    {item.is_draft && <Badge variant="outline">Draft — not published</Badge>}
                    {item.draft_waiting && <Badge variant="outline">Draft waiting</Badge>}
                    <TagLinks tags={item.tags} />
                    {item.resource.id !== entry.id && (
                      <span className="text-xs text-muted-foreground">· {item.resource.name}</span>
                    )}
                  </div>
                  {item.description_short && <p className="text-muted-foreground">{item.description_short}</p>}
                </div>
                <RowActions>
                  {/* a new document's draft opens at /documents/{id} too (the id it keeps when published) */}
                  <RowAction label="Edit" name={item.title} to={`/documents/${encodeURIComponent(item.id)}`}>
                    <Pencil />
                  </RowAction>
                  <RowAction
                    label={item.is_draft ? "Discard draft" : "Delete"}
                    name={item.title}
                    destructive
                    onClick={() => setDeleting(item)}
                  >
                    <Trash2 />
                  </RowAction>
                </RowActions>
              </li>
            ))}
          </ul>
        ))}
      <ConfirmRemove
        removal={
          deleting && {
            title: deleting.is_draft ? `Discard “${deleting.title}”?` : `Delete “${deleting.title}”?`,
            description: deleting.is_draft
              ? "The draft is discarded; nothing was published."
              : "It is deleted everywhere, with its history." +
                (deleting.linked_elsewhere.length > 0
                  ? ` It is also linked to: ${deleting.linked_elsewhere.map((link) => link.name).join(", ")}.`
                  : ""),
            action: deleting.is_draft ? "Discard draft" : "Delete document",
            run: () => void remove(deleting),
          }
        }
        onClose={() => setDeleting(null)}
      />
    </section>
  );
}
