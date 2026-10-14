import { useEffect, useState } from "react";
import { Link } from "react-router";
import { getDocuments, type DocumentSummary } from "./api";
import { addLink, getConnections, removeLink, type Connections } from "./drafts";
import { SourceList } from "./DraftView";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";

/** The document page's Connections tab: Depends on (added with a document picker, removable), Used by, and what the
 * live version was built from. */
export default function DocumentConnections({ documentId }: { documentId: string }) {
  const [connections, setConnections] = useState<Connections | null>(null);
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<DocumentSummary[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
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
  return (
    <section aria-label="Connections" className="flex flex-col gap-6">
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
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
                  onClick={() => void change(() => removeLink(documentId, item.id))}
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
    </section>
  );
}
