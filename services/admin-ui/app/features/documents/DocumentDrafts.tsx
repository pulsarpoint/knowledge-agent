import { useEffect, useState } from "react";
import { getDocumentDrafts, type DraftDetail, type DraftSummary, type PromoteResult } from "./drafts";
import DraftView, { DraftAuthor } from "./DraftView";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";

/** The document page's Drafts tab: its open drafts (who wrote each, when, the version it is based on, "stale" when the
 * live version moved on) and the selected one in the draft view. */
export default function DocumentDrafts({
  documentId,
  selected,
  onEdit,
  onPromoted,
  confirm,
}: {
  documentId: string;
  selected: string | null;
  onEdit: (draft: DraftDetail) => void;
  onPromoted: (result: PromoteResult) => void;
  /** Runs Edit and Promote, after asking when the editor has unsaved changes. */
  confirm?: (run: () => void) => void;
}) {
  const [drafts, setDrafts] = useState<DraftSummary[] | null>(null);
  const [open, setOpen] = useState<string | null>(selected);
  const [error, setError] = useState("");
  const [load, setLoad] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    getDocumentDrafts(documentId, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setDrafts(value.drafts);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Could not load the drafts");
      });
    return () => controller.abort();
  }, [documentId, load]);
  if (!drafts)
    return error ? (
      <Alert variant="destructive">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    ) : (
      <Skeleton className="h-48 w-full" />
    );
  if (drafts.length === 0)
    return (
      <p className="text-sm text-muted-foreground">
        No open drafts. Save draft keeps the editor's text as a draft; agent runs write drafts too.
      </p>
    );
  const shown = drafts.some((draft) => draft.id === open) ? open : null;
  return (
    <div className="grid gap-6 md:grid-cols-[260px_minmax(0,1fr)]">
      <ul aria-label="Drafts" className="flex flex-col gap-2">
        {drafts.map((draft) => (
          <li key={draft.id} className="flex flex-col gap-1">
            <Button
              type="button"
              variant={shown === draft.id ? "secondary" : "outline"}
              className="h-auto w-full justify-start whitespace-normal py-3 text-left"
              onClick={() => setOpen(draft.id)}
            >
              <span>
                {draft.title}
                {draft.stale && (
                  <Badge variant="destructive" className="ml-2">
                    stale
                  </Badge>
                )}
              </span>
            </Button>
            <p className="px-1 text-xs text-muted-foreground">
              <DraftAuthor draft={draft} />
              {` · ${new Date(draft.updated_at).toLocaleString()} · based on version ${draft.base_version ?? "?"}`}
            </p>
          </li>
        ))}
      </ul>
      <div className="min-w-0">
        {shown ? (
          <DraftView
            key={shown}
            draftId={shown}
            onEdit={onEdit}
            onPromoted={onPromoted}
            confirm={confirm}
            onDiscarded={() => {
              setOpen(null);
              setLoad((n) => n + 1);
            }}
          />
        ) : (
          <p className="text-sm text-muted-foreground">Select a draft to compare it with the live version.</p>
        )}
      </div>
    </div>
  );
}
