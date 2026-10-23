import { useEffect, useState } from "react";
import { Link } from "react-router";
import {
  getEntryDocuments,
  newInventoryDocumentPath,
  type EntryDocuments as Documents,
  type MapEntry,
} from "./inventoryMap";
import EntrySheet from "./EntrySheet";
import Markdown from "~/components/Markdown";
import TagLinks from "~/features/documents/TagLinks";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";

/** A resource page's documents: its inventory document rendered (Open edits it through drafts as usual), or New
 * inventory document when it has none; its document list (its and its direct children's documents: title, tags (each a
 * link to the Documents list filtered by it), short description, and the child's name for a child's document); and Connections and documents
 * (the entry sheet) to change its links. */
export default function EntryDocuments({
  entry,
  onChanged,
  ownOnly = false,
}: {
  entry: MapEntry;
  onChanged?: () => void;
  /** Only the entry's own documents, not its children's (the environment page: each server and service shows its own). */
  ownOnly?: boolean;
}) {
  const [documents, setDocuments] = useState<Documents | null>(null);
  const [editing, setEditing] = useState(false);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    getEntryDocuments(entry, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setDocuments(value);
      })
      .catch(() => undefined); // nothing shown; the sheet shows its own errors
    return () => controller.abort();
  }, [entry.kind, entry.id, version]);
  const inventory = documents?.inventory_document ?? null;
  const list = (documents?.documents ?? []).filter((item) => !ownOnly || item.resource.id === entry.id);
  return (
    <section aria-label="Documents" className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">Documents</h2>
        <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
          Connections and documents
        </Button>
      </div>
      {documents && entry.kind !== "code_project" && (
        <section aria-label="Inventory document" className="flex flex-col gap-2 rounded-lg border p-4">
          {inventory ? (
            <>
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
            </>
          ) : (
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm text-muted-foreground">No inventory document yet.</p>
              <Button asChild size="sm">
                <Link to={newInventoryDocumentPath(entry)}>New inventory document</Link>
              </Button>
            </div>
          )}
        </section>
      )}
      {documents &&
        (list.length === 0 ? (
          <p className="text-sm text-muted-foreground">No documents yet.</p>
        ) : (
          <ul aria-label="Documents" className="flex flex-col gap-2 text-sm">
            {list.map((item) => (
              <li key={item.id} className="flex flex-col gap-1">
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
              </li>
            ))}
          </ul>
        ))}
      {editing && (
        <EntrySheet
          entry={entry}
          onClose={() => setEditing(false)}
          onChanged={() => {
            setVersion((value) => value + 1);
            onChanged?.();
          }}
        />
      )}
    </section>
  );
}
