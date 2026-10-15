import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link as RouterLink } from "react-router";
import { ApiError } from "~/lib/api";
import { getDocuments, type DocumentSummary } from "~/features/documents/api";
import {
  ENTRY_LABELS,
  ENTRY_PAGES,
  LINK_LABELS,
  createLink,
  deleteLink,
  formatAttributes,
  getEntryDocuments,
  getLinks,
  linkDocument,
  linkKindsFrom,
  parseAttributes,
  searchEntries,
  targetKinds,
  unlinkDocument,
  updateLink,
  type EntryDocuments,
  type EntryHit,
  type Link,
  type LinkKind,
  type MapEntry,
} from "./inventoryMap";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Textarea } from "~/components/ui/textarea";

const say = (reason: unknown, fallback: string) => (reason instanceof ApiError ? reason.message : fallback);
/** How a link reads from this entry: "depends on → worker" or "billing → connects to". */
const linkText = (link: Link, entryId: string) =>
  link.from.id === entryId
    ? `${LINK_LABELS[link.kind]} → ${link.to.name}`
    : `${link.from.name} → ${LINK_LABELS[link.kind]}`;
const linkNote = (link: Link) =>
  [link.description, ...Object.entries(link.attributes).map(([name, text]) => `${name}: ${text}`)]
    .filter(Boolean)
    .join(" · ");

function LinkFields({
  id,
  description,
  attributes,
  onDescription,
  onAttributes,
}: {
  id: string;
  description: string;
  attributes: string;
  onDescription: (text: string) => void;
  onAttributes: (text: string) => void;
}) {
  const parsed = parseAttributes(attributes);
  const problem = "problem" in parsed ? parsed.problem : null;
  return (
    <FieldGroup>
      <Field>
        <FieldLabel htmlFor={`${id}-description`}>Description</FieldLabel>
        <Textarea
          id={`${id}-description`}
          value={description}
          maxLength={4000}
          rows={2}
          onChange={(event) => onDescription(event.target.value)}
        />
      </Field>
      <Field data-invalid={problem !== null || undefined}>
        <FieldLabel htmlFor={`${id}-attributes`}>Attributes</FieldLabel>
        <Textarea
          id={`${id}-attributes`}
          value={attributes}
          rows={3}
          spellCheck={false}
          className="font-mono text-xs"
          placeholder="port: 5432"
          aria-invalid={problem !== null}
          onChange={(event) => onAttributes(event.target.value)}
        />
        {problem ? (
          <FieldError>{problem}</FieldError>
        ) : (
          <FieldDescription>Optional; one name: value per line.</FieldDescription>
        )}
      </Field>
    </FieldGroup>
  );
}

function EditLink({
  link,
  onSaved,
  onFailed,
  onCancel,
}: {
  link: Link;
  onSaved: () => void;
  onFailed: () => void;
  onCancel: () => void;
}) {
  const [description, setDescription] = useState(link.description);
  const [attributes, setAttributes] = useState(formatAttributes(link.attributes));
  const [error, setError] = useState<string | null>(null);
  const parsed = parseAttributes(attributes);
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!("value" in parsed)) return;
    setError(null);
    try {
      await updateLink(link.id, { description, attributes: parsed.value, revision: link.revision });
      onSaved();
    } catch (reason) {
      setError(say(reason, "Could not save the connection."));
      onFailed();
    }
  }
  return (
    <form onSubmit={save} className="flex flex-col gap-2 rounded-md border p-2">
      <LinkFields
        id={`link-${link.id}`}
        description={description}
        attributes={attributes}
        onDescription={setDescription}
        onAttributes={setAttributes}
      />
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={!("value" in parsed)}>
          Save connection
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function Connect({ entry, onConnected }: { entry: MapEntry; onConnected: () => void }) {
  const kinds = linkKindsFrom(entry.kind);
  const [kind, setKind] = useState<LinkKind>(kinds[0]);
  const [description, setDescription] = useState("");
  const [attributes, setAttributes] = useState("");
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<EntryHit[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const targets = targetKinds(kind);
  const parsed = parseAttributes(attributes);
  async function find(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      setHits(
        (await searchEntries(q.trim(), targets?.length === 1 ? targets[0] : undefined)).entries.filter(
          (hit) => hit.entry_id !== entry.id,
        ),
      );
    } catch (reason) {
      setError(say(reason, "Could not search."));
    }
  }
  async function connect(hit: EntryHit) {
    if (!("value" in parsed)) return;
    setError(null);
    try {
      await createLink({
        from: { kind: entry.kind, id: entry.id },
        to: { kind: hit.entry_kind, id: hit.entry_id },
        kind,
        description,
        attributes: parsed.value,
      });
      setDescription("");
      setAttributes("");
      setHits(null);
      setQ("");
      onConnected();
    } catch (reason) {
      setError(say(reason, "Could not connect."));
    }
  }
  return (
    <section aria-label="Connect" className="flex flex-col gap-2">
      <h3 className="text-sm font-medium">Connect to…</h3>
      <Select
        value={kind}
        onValueChange={(value) => {
          setKind(value as LinkKind);
          setHits(null);
        }}
      >
        <SelectTrigger aria-label="Connection kind" className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            {kinds.map((item) => (
              <SelectItem key={item} value={item}>
                {LINK_LABELS[item]}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
      <LinkFields
        id="connect"
        description={description}
        attributes={attributes}
        onDescription={setDescription}
        onAttributes={setAttributes}
      />
      <form onSubmit={find} className="flex gap-2">
        <Input
          aria-label="Find an entry"
          value={q}
          placeholder={targets ? `Find a ${ENTRY_LABELS[targets[0]]}` : "Find by name"}
          onChange={(event) => setQ(event.target.value)}
        />
        <Button type="submit" variant="outline">
          Find
        </Button>
      </form>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {hits &&
        (hits.length === 0 ? (
          <p className="text-muted-foreground text-sm">Nothing found.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {hits.map((hit) => (
              <li key={`${hit.entry_kind}:${hit.entry_id}`} className="flex items-center justify-between gap-2 text-sm">
                <span>
                  {hit.name} <span className="text-muted-foreground">· {ENTRY_LABELS[hit.entry_kind]}</span>
                </span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={!("value" in parsed)}
                  aria-label={`Connect: ${hit.name}`}
                  onClick={() => void connect(hit)}
                >
                  Connect
                </Button>
              </li>
            ))}
          </ul>
        ))}
    </section>
  );
}

function DocumentPicker({ linked, onPick }: { linked: string[]; onPick: (document: DocumentSummary) => void }) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<DocumentSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function find(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      setFound((await getDocuments(new URLSearchParams({ q: q.trim() }))).documents);
    } catch (reason) {
      setError(say(reason, "Could not search documents."));
    }
  }
  const shown = found?.filter((document) => !linked.includes(document.id));
  return (
    <form onSubmit={find} className="flex flex-col gap-2">
      <h4 className="text-sm font-medium">Link a document</h4>
      <div className="flex gap-2">
        <Input
          aria-label="Find a document"
          value={q}
          placeholder="Title or slug"
          onChange={(event) => setQ(event.target.value)}
        />
        <Button type="submit" variant="outline">
          Find
        </Button>
      </div>
      {error && <p className="text-destructive text-sm">{error}</p>}
      {shown &&
        (shown.length === 0 ? (
          <p className="text-muted-foreground text-sm">No documents to link.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {shown.map((document) => (
              <li key={document.id} className="flex items-center justify-between gap-2 text-sm">
                <span>{document.title}</span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  aria-label={`Link: ${document.title}`}
                  onClick={() => onPick(document)}
                >
                  Link
                </Button>
              </li>
            ))}
          </ul>
        ))}
    </form>
  );
}

export default function EntrySheet({
  entry,
  onClose,
  onChanged,
}: {
  entry: MapEntry;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [links, setLinks] = useState<Link[] | null>(null);
  const [documents, setDocuments] = useState<EntryDocuments>({ inventory_document: null, documents: [] });
  const [editing, setEditing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const [linked, documented] = await Promise.all([getLinks(entry), getEntryDocuments(entry)]);
      setLinks(linked.links);
      setDocuments(documented);
    } catch (reason) {
      setError(say(reason, "Could not load the entry."));
    }
  }, [entry.kind, entry.id]);
  useEffect(() => {
    void load();
  }, [load]);
  async function act(work: () => Promise<unknown>, fallback: string) {
    setError(null);
    try {
      await work();
      setEditing(null);
      onChanged();
      await load();
    } catch (reason) {
      setError(say(reason, fallback));
    }
  }
  const page = ENTRY_PAGES[entry.kind];
  return (
    <Sheet
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>{entry.name}</SheetTitle>
          <SheetDescription>
            {ENTRY_LABELS[entry.kind]} ·{" "}
            <RouterLink to={page.page} className="underline">
              Open in {page.label}
            </RouterLink>
          </SheetDescription>
        </SheetHeader>
        <div className="flex flex-col gap-6 px-4 pb-4">
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <section aria-label="Connections" className="flex flex-col gap-2">
            <h3 className="text-sm font-medium">Connections</h3>
            {links === null ? (
              <Spinner />
            ) : links.length === 0 ? (
              <p className="text-muted-foreground text-sm">No connections yet.</p>
            ) : (
              <ul className="flex flex-col gap-2 text-sm">
                {links.map((link) => {
                  const text = linkText(link, entry.id);
                  return (
                    <li key={link.id} className="flex flex-col gap-1">
                      <div className="flex items-center justify-between gap-2">
                        <span>{text}</span>
                        <span className="flex gap-1">
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            aria-label={`Edit ${text}`}
                            onClick={() => setEditing(link.id)}
                          >
                            Edit
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            aria-label={`Remove ${text}`}
                            onClick={() => void act(() => deleteLink(link.id), "Could not remove the connection.")}
                          >
                            Remove
                          </Button>
                        </span>
                      </div>
                      {linkNote(link) && <span className="text-muted-foreground text-xs">{linkNote(link)}</span>}
                      {editing === link.id && (
                        <EditLink
                          link={link}
                          onCancel={() => setEditing(null)}
                          onFailed={() => void load()}
                          onSaved={() => {
                            setEditing(null);
                            onChanged();
                            void load();
                          }}
                        />
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
          <Connect
            entry={entry}
            onConnected={() => {
              onChanged();
              void load();
            }}
          />
          <section aria-label="Documents" className="flex flex-col gap-2">
            <h3 className="text-sm font-medium">Documents</h3>
            {!documents.inventory_document && documents.documents.length === 0 ? (
              <p className="text-muted-foreground text-sm">No documents are linked.</p>
            ) : (
              <ul className="flex flex-col gap-1 text-sm">
                {documents.inventory_document && (
                  <li className="flex items-center gap-2">
                    <RouterLink to={`/documents/${documents.inventory_document.id}`} className="underline">
                      {documents.inventory_document.title}
                    </RouterLink>
                    <Badge variant="secondary">Inventory</Badge>
                  </li>
                )}
                {documents.documents.map((document) => (
                  <li key={document.id} className="flex items-center justify-between gap-2">
                    <span className="flex items-center gap-2">
                      <RouterLink to={`/documents/${document.id}`} className="underline">
                        {document.title}
                      </RouterLink>
                      {document.connection === "built_for" && (
                        <span className="text-muted-foreground text-xs">built for</span>
                      )}
                    </span>
                    {document.connection === "linked" && (
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        aria-label={`Remove link: ${document.title}`}
                        onClick={() => void act(() => unlinkDocument(entry, document.id), "Could not remove the link.")}
                      >
                        Remove
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            )}
            <DocumentPicker
              linked={[
                ...(documents.inventory_document ? [documents.inventory_document.id] : []),
                ...documents.documents.map((document) => document.id),
              ]}
              onPick={(document) => void act(() => linkDocument(entry, document.id), "Could not link the document.")}
            />
          </section>
        </div>
      </SheetContent>
    </Sheet>
  );
}
