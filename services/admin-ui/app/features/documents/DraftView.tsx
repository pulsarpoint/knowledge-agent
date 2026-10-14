import { useEffect, useState } from "react";
import { Link } from "react-router";
import { ApiError } from "~/lib/api";
import { agentRunPath, sessionPath } from "~/features/sessions/api";
import {
  discardDraft,
  getDraft,
  otherDrafts,
  promoteDraft,
  promoteWarning,
  STALE_BASE,
  type DraftDetail,
  type DraftSource,
  type DraftSummary,
  type PromoteResult,
} from "./drafts";
import DiffView from "./DiffView";
import Markdown from "~/components/Markdown";
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
import { Skeleton } from "~/components/ui/skeleton";
import { Spinner } from "~/components/ui/spinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";

const failure = (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback);

/** Who wrote a draft: the person, or "Agent run" linking to the run. */
export function DraftAuthor({
  draft,
}: {
  draft: Pick<DraftSummary, "origin" | "session_id" | "session_title" | "author" | "session_purpose" | "document_id">;
}) {
  if (draft.origin === "agent")
    return draft.session_id ? (
      <Link
        className="underline"
        title={draft.session_title ?? undefined}
        to={
          draft.session_purpose === "revision" && draft.document_id
            ? agentRunPath(draft.document_id, draft.session_id)
            : sessionPath(draft.session_id)
        }
      >
        Agent run
      </Link>
    ) : (
      <span>Agent run</span>
    );
  return <span>{draft.author || "Someone"}</span>;
}

/** The documents a draft or version was built from; a deleted one keeps its title and version. */
export function SourceList({ sources, empty }: { sources: DraftSource[]; empty: string }) {
  if (sources.length === 0) return <p className="text-sm text-muted-foreground">{empty}</p>;
  return (
    <ul className="flex flex-col gap-1 text-sm">
      {sources.map((source, index) => (
        <li key={`${source.source_document_id ?? "deleted"}-${index}`}>
          <span className="text-muted-foreground">{source.kind === "parent" ? "Revises " : "Read "}</span>
          {source.deleted || !source.source_document_id ? (
            <span>{`${source.source_title} v${source.source_version ?? "?"} (deleted)`}</span>
          ) : (
            <>
              <Link className="underline" to={`/documents/${encodeURIComponent(source.source_document_id)}`}>
                {source.source_title}
              </Link>
              {` v${source.source_version ?? "?"}`}
            </>
          )}
        </li>
      ))}
    </ul>
  );
}

/** Asks before a promote that deletes the document's other drafts: "Promote and delete {n} other draft(s)?". */
export function PromoteWarning({
  others,
  busy = false,
  onConfirm,
  onCancel,
}: {
  /** The other drafts the promote deletes; null: not asking. */
  others: number | null;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <AlertDialog
      open={others !== null}
      onOpenChange={(value) => {
        if (!value && !busy) onCancel();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{promoteWarning(others ?? 0)}</AlertDialogTitle>
          <AlertDialogDescription>
            Promoting makes this draft the live version and deletes the document's other drafts.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            disabled={busy}
            onClick={(e) => {
              e.preventDefault();
              onConfirm();
            }}
          >
            Promote
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

/** Promote after asking when it deletes other drafts: `ask` counts them fresh (an agent may have added one) and either
 * opens the warning (`others`) or runs `promote` at once. */
export function usePromoteWarning(promote: () => void, onError: (message: string) => void) {
  const [others, setOthers] = useState<number | null>(null);
  return {
    others,
    ask: async (draft: { id: string; document_id: string | null }) => {
      try {
        const count = await otherDrafts(draft);
        if (count > 0) setOthers(count);
        else promote();
      } catch (e) {
        onError(failure(e, "Could not check the document's other drafts"));
      }
    },
    confirm: () => {
      setOthers(null);
      promote();
    },
    cancel: () => setOthers(null),
  };
}

/** One draft: who wrote it and when, the diff against the live version (a draft of a document) and the preview, its
 * sources, and Edit, Promote (asking first when it deletes other drafts; a stale draft asks again: Promote anyway) and
 * Discard. Promote and Discard delete the draft; a draft gone meanwhile (404) shows the server's message. */
export default function DraftView({
  draftId,
  reloadKey = 0,
  onEdit,
  onPromoted,
  onDiscarded,
  onLoaded,
  confirm = (run) => run(),
}: {
  draftId: string;
  reloadKey?: number;
  onEdit?: (draft: DraftDetail) => void;
  onPromoted: (result: PromoteResult) => void;
  onDiscarded: () => void;
  onLoaded?: (draft: DraftDetail) => void;
  /** Runs Edit and Promote; the document page asks first when its editor has unsaved changes. */
  confirm?: (run: () => void) => void;
}) {
  const [draft, setDraft] = useState<DraftDetail | null>(null);
  const [error, setError] = useState("");
  const [stale, setStale] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [view, setView] = useState("changes");
  const [load, setLoad] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    getDraft(draftId, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) {
          setDraft(value);
          onLoaded?.(value);
        }
      })
      .catch((e) => {
        if (controller.signal.aborted) return;
        if (e instanceof ApiError && e.status === 404) setDraft(null); // promoted, discarded or replaced meanwhile
        setError(failure(e, "Could not load the draft"));
      });
    return () => controller.abort();
  }, [draftId, reloadKey, load]);

  async function promote(confirmStale: boolean) {
    if (!draft) return;
    setBusy(true);
    setError("");
    try {
      const result = await promoteDraft(draft.id, draft.revision, confirmStale);
      setStale("");
      onPromoted(result);
    } catch (e) {
      if (e instanceof ApiError && e.code === STALE_BASE) {
        setStale(e.message);
        setView("changes");
        setLoad((n) => n + 1);
      } else {
        setStale("");
        setError(failure(e, "Could not promote the draft"));
        setLoad((n) => n + 1);
      }
    } finally {
      setBusy(false);
    }
  }
  async function discard() {
    if (!draft) return;
    setBusy(true);
    setError("");
    try {
      await discardDraft(draft.id, draft.revision);
      setConfirmDiscard(false);
      onDiscarded();
    } catch (e) {
      setConfirmDiscard(false);
      setError(failure(e, "Could not discard the draft"));
      setLoad((n) => n + 1);
    } finally {
      setBusy(false);
    }
  }

  const warning = usePromoteWarning(() => void promote(false), setError);
  if (!draft)
    return error ? (
      <Alert variant="destructive">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    ) : (
      <Skeleton className="h-48 w-full" />
    );
  const isNew = draft.document_id === null;
  return (
    <section aria-label="Draft" className="flex min-w-0 flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="break-words font-semibold">{draft.title}</h2>
          <p className="text-sm text-muted-foreground">
            Written by <DraftAuthor draft={draft} /> · updated {new Date(draft.updated_at).toLocaleString()}
            {draft.base_version ? ` · based on version ${draft.base_version}` : ""}
            {isNew ? ` · new document in ${draft.folder ? `${draft.folder}/` : "the top folder"}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {draft.stale && <Badge variant="destructive">stale</Badge>}
          {onEdit && (
            <Button type="button" variant="outline" disabled={busy} onClick={() => confirm(() => onEdit(draft))}>
              Edit
            </Button>
          )}
          <Button type="button" variant="outline" disabled={busy} onClick={() => setConfirmDiscard(true)}>
            Discard
          </Button>
          <Button
            type="button"
            disabled={busy || stale !== ""}
            onClick={() =>
              confirm(() => {
                setError("");
                void warning.ask(draft);
              })
            }
          >
            {busy && <Spinner data-icon="inline-start" />}Promote
          </Button>
        </div>
      </div>
      {stale && (
        <Alert variant="destructive">
          <AlertDescription className="flex flex-col gap-2">
            <span>{stale}</span>
            <span className="flex gap-2">
              <Button type="button" size="sm" variant="destructive" disabled={busy} onClick={() => void promote(true)}>
                Promote anyway
              </Button>
              <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => setStale("")}>
                Cancel
              </Button>
            </span>
          </AlertDescription>
        </Alert>
      )}
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <Tabs value={isNew ? "preview" : view} onValueChange={setView}>
        <TabsList>
          {!isNew && <TabsTrigger value="changes">Changes</TabsTrigger>}
          <TabsTrigger value="preview">Preview</TabsTrigger>
        </TabsList>
        {!isNew && (
          <TabsContent value="changes">
            <DiffView before={draft.live_markdown ?? ""} after={draft.markdown} />
          </TabsContent>
        )}
        <TabsContent value="preview">
          <div className="rounded-lg border p-4">
            <Markdown content={draft.markdown} />
          </div>
        </TabsContent>
      </Tabs>
      <section aria-label="Sources" className="flex flex-col gap-1">
        <h3 className="text-sm font-semibold">Sources</h3>
        <SourceList sources={draft.sources} empty="No recorded sources." />
      </section>
      <AlertDialog
        open={confirmDiscard}
        onOpenChange={(value) => {
          if (!busy) setConfirmDiscard(value);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{`Discard “${draft.title}”?`}</AlertDialogTitle>
            <AlertDialogDescription>
              The draft is deleted (the audit history keeps a record). The live document does not change.
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
      <PromoteWarning others={warning.others} busy={busy} onConfirm={warning.confirm} onCancel={warning.cancel} />
    </section>
  );
}
