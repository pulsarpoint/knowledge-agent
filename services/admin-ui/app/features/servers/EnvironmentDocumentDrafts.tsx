import { useEffect, useState } from "react";
import { Link } from "react-router";
import { getEnvironmentDocumentDrafts, type EnvironmentDocumentDraft } from "./environments";
import { Badge } from "~/components/ui/badge";

/** Drafts to review on the environment's Documents tab: every document draft the header counts (of the environment,
 * its member servers and their services in it), each with its title (a link to its draft page), the resource it
 * belongs to, and "New document" or "New version". Nothing is shown when none waits. `refresh` reloads the list. */
export default function EnvironmentDocumentDrafts({
  environmentId,
  refresh = 0,
}: {
  environmentId: string;
  refresh?: number;
}) {
  const [drafts, setDrafts] = useState<EnvironmentDocumentDraft[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    getEnvironmentDocumentDrafts(environmentId, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setDrafts(value.drafts);
      })
      .catch(() => undefined); // nothing shown
    return () => controller.abort();
  }, [environmentId, refresh]);
  if (drafts.length === 0) return null;
  return (
    <section aria-label="Drafts to review" className="flex flex-col gap-2 rounded-lg border p-4">
      <h2 className="text-lg font-semibold">Drafts to review</h2>
      <ul className="flex flex-col gap-2 text-sm">
        {drafts.map((draft) => (
          <li key={draft.id} className="flex flex-wrap items-center gap-2">
            <Link className="font-medium underline" to={`/documents/drafts/${encodeURIComponent(draft.id)}`}>
              {draft.title}
            </Link>
            <Badge variant="outline">{draft.new ? "New document" : "New version"}</Badge>
            <span className="text-xs text-muted-foreground">· {draft.resource.name}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
