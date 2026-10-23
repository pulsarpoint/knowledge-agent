import { useEffect, useState } from "react";
import { Check, X } from "lucide-react";
import { ApiError } from "~/lib/api";
import { confirmDraft, discardDraft, isStale, type DraftKind, type Reached } from "./inventory";
import { RowAction } from "./RowAction";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";

const CONFLICT = "This was changed meanwhile. Reload to see the latest.";

/** The draft Confirm or Discard acts on, with the revision read (a membership has none: null). */
export interface DraftTarget {
  kind: DraftKind;
  id: string;
  revision: number | null;
}
type Action = "confirm" | "discard";
const run = (action: Action, target: DraftTarget, dryRun: boolean): Promise<Reached[]> =>
  action === "confirm"
    ? confirmDraft(target.kind, target.id, target.revision, dryRun).then((value) => value.confirmed)
    : discardDraft(target.kind, target.id, target.revision, dryRun).then((value) => value.discarded);

export const DraftBadge = () => <Badge variant="outline">Draft</Badge>;

/** "Saving confirms the drafts it uses: …" — shown in a person's form whose save confirms the drafts it refers to
 * (a confirmed entry never needs a draft). Nothing when it uses none. */
export function DraftsUsed({ names }: { names: string[] }) {
  if (names.length === 0) return null;
  return <p className="text-sm text-muted-foreground">Saving confirms the drafts it uses: {names.join(", ")}.</p>;
}

/** Confirm and Discard row buttons for a draft; each opens ConfirmDraftSheet. `discard` defaults to `confirm` (a
 * server row confirms its membership but discards the draft server itself). Place inside RowActions. */
export function DraftActions({
  name,
  confirm,
  discard = confirm,
  onDone,
}: {
  name: string;
  confirm: DraftTarget;
  discard?: DraftTarget;
  onDone: () => void;
}) {
  const [open, setOpen] = useState<Action | null>(null);
  return (
    <>
      <RowAction label="Confirm" name={name} onClick={() => setOpen("confirm")}>
        <Check />
      </RowAction>
      <RowAction label="Discard" name={name} destructive onClick={() => setOpen("discard")}>
        <X />
      </RowAction>
      {open && (
        <ConfirmDraftSheet
          action={open}
          target={open === "confirm" ? confirm : discard}
          name={name}
          onClose={() => setOpen(null)}
          onDone={() => {
            setOpen(null);
            onDone();
          }}
        />
      )}
    </>
  );
}

/** Confirm or Discard one draft: first a dry run lists the other drafts it also confirms (or deletes), then the
 * person confirms. A stale revision says so and confirms nothing. */
export default function ConfirmDraftSheet({
  action,
  target,
  name,
  onDone,
  onClose,
}: {
  action: Action;
  target: DraftTarget;
  name: string;
  onDone: () => void;
  onClose: () => void;
}) {
  const [reached, setReached] = useState<Reached[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const say = (e: unknown) =>
    setError(isStale(e) ? CONFLICT : e instanceof ApiError ? e.message : "Could not reach the server. Try again.");

  useEffect(() => {
    let current = true;
    run(action, target, true).then(
      (value) => current && setReached(value),
      (e) => current && say(e),
    );
    return () => {
      current = false;
    };
  }, [action, target.kind, target.id, target.revision]);

  async function go() {
    setBusy(true);
    setError(null);
    try {
      await run(action, target, false);
      onDone();
    } catch (e) {
      say(e);
    } finally {
      setBusy(false);
    }
  }

  const confirming = action === "confirm";
  const others = (reached ?? []).filter((item) => !(item.kind === target.kind && item.id === target.id));
  return (
    <Sheet
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <SheetContent className="overflow-y-auto">
        <SheetHeader>
          <SheetTitle className="break-words">{`${confirming ? "Confirm" : "Discard"} ${name}?`}</SheetTitle>
          <SheetDescription>
            {confirming
              ? "A draft was written by an agent; confirming makes it a real entry."
              : "Discarding deletes the draft. This cannot be undone."}
          </SheetDescription>
        </SheetHeader>
        <div className="flex flex-col gap-4 p-4">
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          {reached === null && !error && <Spinner />}
          {reached !== null &&
            (others.length === 0 ? (
              <p className="text-sm">{`Nothing else is ${confirming ? "confirmed" : "deleted"} with it.`}</p>
            ) : (
              <div className="flex flex-col gap-1 text-sm">
                <p>{confirming ? "Confirming it also confirms these drafts:" : "Discarding it also deletes:"}</p>
                <ul className="list-disc pl-5">
                  {others.map((item) => (
                    <li key={`${item.kind}:${item.id}`} className="[overflow-wrap:anywhere]">
                      {item.name}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          <div className="flex gap-2">
            <Button
              variant={confirming ? "default" : "destructive"}
              disabled={busy || reached === null}
              onClick={() => void go()}
            >
              {busy && <Spinner data-icon="inline-start" />}
              {confirming ? "Confirm" : "Discard"}
            </Button>
            <Button type="button" variant="outline" disabled={busy} onClick={onClose}>
              Cancel
            </Button>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
