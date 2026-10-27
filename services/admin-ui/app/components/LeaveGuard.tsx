import { useEffect, useRef, useState } from "react";
import { useBeforeUnload, useBlocker } from "react-router";
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

/** The one question unsaved changes ask: Stay or Leave. */
function LeaveDialog({ open, onStay, onLeave }: { open: boolean; onStay: () => void; onLeave: () => void }) {
  // Leave closes the dialog too: its close must not undo what Leave just let through.
  const leaving = useRef(false);
  useEffect(() => {
    if (!open) leaving.current = false;
  }, [open]);
  const stay = () => {
    if (!leaving.current) onStay();
  };
  return (
    <AlertDialog open={open} onOpenChange={(next) => !next && stay()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Unsaved changes</AlertDialogTitle>
          <AlertDialogDescription>You have unsaved changes. Leave without saving?</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={stay}>Stay</AlertDialogCancel>
          <AlertDialogAction
            onClick={() => {
              leaving.current = true;
              onLeave();
            }}
          >
            Leave
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

const differentPath = (a: string, b: string) => a !== b;

/** Leaving the page (another section, back, any link) while it has unsaved changes asks first. `samePage(a, b)` says
 * which path changes stay on the page (the document page with its agent run); a page that navigates on its own after
 * saving or discarding sets `leaving` first, so its own navigation never asks. */
export default function LeaveGuard({
  dirty,
  samePage,
  leaving,
}: {
  dirty: boolean;
  samePage?: (a: string, b: string) => boolean;
  leaving?: React.RefObject<boolean>;
}) {
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirty &&
      !leaving?.current &&
      (samePage
        ? !samePage(currentLocation.pathname, nextLocation.pathname)
        : differentPath(currentLocation.pathname, nextLocation.pathname)),
  );
  useBeforeUnload((event) => {
    if (dirty && !leaving?.current) event.preventDefault();
  });
  return (
    <LeaveDialog
      open={blocker.state === "blocked"}
      onStay={() => blocker.state === "blocked" && blocker.reset()}
      onLeave={() => blocker.state === "blocked" && blocker.proceed()}
    />
  );
}

/** Closing a sheet with unsaved changes asks the same question: `close(done)` runs `done` at once when the sheet is
 * clean, and after Leave when it is dirty. Render `dialog` next to the sheet. */
export function useSheetGuard(dirty: boolean) {
  const [pending, setPending] = useState<(() => void) | null>(null);
  const close = (done: () => void) => (dirty ? setPending(() => done) : done());
  const dialog = (
    <LeaveDialog
      open={pending !== null}
      onStay={() => setPending(null)}
      onLeave={() => {
        const done = pending;
        setPending(null);
        done?.();
      }}
    />
  );
  return { close, dialog };
}
