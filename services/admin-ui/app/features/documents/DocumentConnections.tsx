import { useEffect, useState } from "react";
import { Link } from "react-router";
import { Plus } from "lucide-react";
import { toast } from "sonner";
import { getDocuments, type DocumentSummary } from "./api";
import {
  addLink,
  addResource,
  getConnections,
  removeLink,
  removeResource,
  type Connections,
  type DocumentResourceLink,
} from "./drafts";
import { SourceList } from "./DraftView";
import AddResourceSheet from "./AddResourceSheet";
import DocumentLinkHistory from "./DocumentLinkHistory";
import { linkedResourcePath, typeLabel, typeWord } from "./ResourceBadges";
import ConfirmRemove, { type Removal } from "~/components/ConfirmRemove";
import RowMenu, { deleteMenuItem } from "~/components/RowMenu";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";
import { Field, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";

/** One resource link: type, name (a link to the resource's page), Built for / Linked, who added it and when, and its
 * menu (Remove, off for a built-for link). */
function ResourceRow({
  link,
  busy,
  onRemove,
}: {
  link: DocumentResourceLink;
  busy: boolean;
  onRemove: (link: DocumentResourceLink) => void;
}) {
  const name = link.name ?? link.id;
  const to = linkedResourcePath(link);
  const builtFor = link.connection === "built_for";
  return (
    <TableRow>
      <TableCell>{typeLabel(link.type)}</TableCell>
      <TableCell className="[overflow-wrap:anywhere]">
        {to ? (
          <Link className="underline" to={to}>
            {name}
          </Link>
        ) : (
          name
        )}
      </TableCell>
      <TableCell>
        <Badge variant={builtFor ? "secondary" : "outline"}>{builtFor ? "Built for" : "Linked"}</Badge>
      </TableCell>
      <TableCell className="text-muted-foreground">
        {[link.added_by, new Date(link.created_at).toLocaleString()].filter(Boolean).join(" · ")}
      </TableCell>
      <TableCell className="w-10 text-right">
        <RowMenu
          name={`${typeWord(link.type)} ${name}`}
          disabled={busy}
          items={[
            {
              ...deleteMenuItem(() => onRemove(link), "Remove"),
              disabled: builtFor,
              hint: builtFor ? "Built for this resource: the link stays." : undefined,
            },
          ]}
        />
      </TableCell>
    </TableRow>
  );
}

/** The document page's Connections tab: Resources (its resource links: Add resource, Remove, Link history), Depends on
 * (added with a document picker, removable), Used by, and what the live version was built from. `onResourcesChanged`
 * gets the answer after a resource link is added or removed (the header's badges and the document's revision). */
export default function DocumentConnections({
  documentId,
  onResourcesChanged,
}: {
  documentId: string;
  onResourcesChanged?: (connections: Connections) => void;
}) {
  const [connections, setConnections] = useState<Connections | null>(null);
  const [adding, setAdding] = useState(false);
  const [historyLoad, setHistoryLoad] = useState(0);
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<DocumentSummary[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [removal, setRemoval] = useState<Removal | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    getConnections(documentId, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setConnections(value);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Could not load the connections");
      });
    return () => controller.abort();
  }, [documentId]);
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setFound([]);
      return;
    }
    const controller = new AbortController();
    getDocuments(new URLSearchParams({ q }), controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setFound(value.documents);
      })
      .catch(() => {
        if (!controller.signal.aborted) setFound([]);
      });
    return () => controller.abort();
  }, [query]);
  async function change(action: () => Promise<Connections>) {
    setBusy(true);
    setError("");
    try {
      setConnections(await action());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not change the connection");
    } finally {
      setBusy(false);
    }
  }
  if (!connections)
    return error ? (
      <Alert variant="destructive">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    ) : (
      <Skeleton className="h-48 w-full" />
    );
  const linked = new Set(connections.depends_on.map((item) => item.id));
  const offered = found.filter((item) => item.id !== documentId && !linked.has(item.id));
  /** The answer of a resource link added or removed: shown here, passed up, and the link history reloads. */
  const resourcesChanged = (next: Connections) => {
    setConnections(next);
    onResourcesChanged?.(next);
    setHistoryLoad((n) => n + 1);
  };
  const askRemove = (link: DocumentResourceLink) => {
    const what = `${typeWord(link.type)} ${link.name ?? link.id}`;
    setRemoval({
      title: `Remove the link to ${what}?`,
      description: `The document and the ${typeWord(link.type)} stay.`,
      action: "Remove link",
      run: async () => {
        resourcesChanged(await removeResource(documentId, link.type, link.id));
        toast.success(`Removed the link to ${what}.`);
      },
    });
  };
  return (
    <section aria-label="Connections" className="flex flex-col gap-6">
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <section aria-label="Resources" className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-medium">Resources</h3>
          <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => setAdding(true)}>
            <Plus data-icon="inline-start" />
            Add resource
          </Button>
        </div>
        {connections.resources.length === 0 ? (
          <p className="text-sm text-muted-foreground">Not linked to any resource yet.</p>
        ) : (
          <Table aria-label="Resources">
            <TableHeader>
              <TableRow>
                <TableHead>Type</TableHead>
                <TableHead>Name</TableHead>
                <TableHead>Connection</TableHead>
                <TableHead>Added</TableHead>
                <TableHead>
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {connections.resources.map((link) => (
                <ResourceRow key={`${link.type}-${link.id}`} link={link} busy={busy} onRemove={askRemove} />
              ))}
            </TableBody>
          </Table>
        )}
        <DocumentLinkHistory documentId={documentId} reloadKey={historyLoad} />
      </section>
      <section aria-label="Depends on" className="flex flex-col gap-2">
        <h3 className="font-medium">Depends on</h3>
        {connections.depends_on.length === 0 ? (
          <p className="text-sm text-muted-foreground">This document depends on no other document.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {connections.depends_on.map((item) => (
              <li key={item.id} className="flex items-center justify-between gap-2 text-sm">
                <Link className="underline" to={`/documents/${item.id}`}>
                  {item.title}
                </Link>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  aria-label={`Remove ${item.title}`}
                  onClick={() =>
                    setRemoval({
                      title: `Remove the link to “${item.title}”?`,
                      description: "This document no longer depends on it. Both documents stay.",
                      action: "Remove link",
                      run: () => void change(() => removeLink(documentId, item.id)),
                    })
                  }
                >
                  Remove
                </Button>
              </li>
            ))}
          </ul>
        )}
        <Field>
          <FieldLabel htmlFor="connection-search">Find a document</FieldLabel>
          <Input
            id="connection-search"
            placeholder="Title, slug or description"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") e.preventDefault(); /* the page's form must not submit */
            }}
          />
        </Field>
        {offered.length > 0 && (
          <ul aria-label="Documents to add" className="flex flex-col gap-1">
            {offered.map((item) => (
              <li key={item.id} className="flex items-center justify-between gap-2 text-sm">
                <span>{item.title}</span>
                <Button
                  type="button"
                  size="sm"
                  disabled={busy}
                  aria-label={`Add ${item.title}`}
                  onClick={() =>
                    void change(async () => {
                      const next = await addLink(documentId, item.id);
                      setQuery("");
                      return next;
                    })
                  }
                >
                  Add
                </Button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section aria-label="Used by" className="flex flex-col gap-2">
        <h3 className="font-medium">Used by</h3>
        {connections.used_by.length === 0 ? (
          <p className="text-sm text-muted-foreground">No document depends on this one.</p>
        ) : (
          <ul className="flex flex-col gap-1 text-sm">
            {connections.used_by.map((item) => (
              <li key={item.id}>
                <Link className="underline" to={`/documents/${item.id}`}>
                  {item.title}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section aria-label="Built from" className="flex flex-col gap-2">
        <h3 className="font-medium">Built from</h3>
        <SourceList sources={connections.built_from} empty="The live version records no sources." />
      </section>
      <ConfirmRemove removal={removal} onClose={() => setRemoval(null)} />
      {adding && (
        <AddResourceSheet
          linked={new Set(connections.resources.map((link) => `${link.type}:${link.id}`))}
          add={async (type, id, name) => {
            resourcesChanged(await addResource(documentId, type, id));
            setAdding(false);
            toast.success(`Linked to ${typeWord(type)} ${name}.`);
          }}
          onClose={() => setAdding(false)}
        />
      )}
    </section>
  );
}
