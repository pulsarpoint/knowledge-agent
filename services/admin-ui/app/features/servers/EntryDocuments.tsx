import { useEffect, useState } from "react";
import { Link } from "react-router";
import { getAttachments, ENTRY_LABELS, type Attachment, type MapEntry } from "./inventoryMap";
import EntrySheet from "./EntrySheet";
import { Button } from "~/components/ui/button";

/** An inventory entry's attached documents, with Connections and documents (the entry sheet) to change them. */
export default function EntryDocuments({ entry, onChanged }: { entry: MapEntry; onChanged?: () => void }) {
  const [documents, setDocuments] = useState<Attachment[]>([]);
  const [editing, setEditing] = useState(false);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    let current = true;
    getAttachments(entry)
      .then((value) => {
        if (current) setDocuments(value.documents);
      })
      .catch(() => undefined); // the list stays empty; the sheet shows its own errors
    return () => {
      current = false;
    };
  }, [entry.kind, entry.id, version]);
  return (
    <section aria-label="Documents" className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">Documents</h2>
        <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
          Connections and documents
        </Button>
      </div>
      {documents.length === 0 ? (
        <p className="text-sm text-muted-foreground">No documents are attached to this {ENTRY_LABELS[entry.kind]}.</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {documents.map((item) => (
            <li key={item.id}>
              <Link className="underline" to={`/documents/${item.document_id}`}>
                {item.title}
              </Link>
            </li>
          ))}
        </ul>
      )}
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
