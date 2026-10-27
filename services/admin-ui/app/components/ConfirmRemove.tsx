import { useEffect, useState } from "react";
import { ApiError } from "~/lib/api";
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
import { buttonVariants } from "~/components/ui/button";

/** What a removal asks before it happens: the question (`Delete “<name>”?`, `Remove …?` or `Discard …?`), what it
 * means, the action's label (`Delete <thing>`, `Remove …`, `Discard …`) and what it does. A `run` that returns a
 * promise keeps the dialog open until it settles: it closes when it succeeds and shows the error when it fails. */
export interface Removal {
  title: string;
  description: React.ReactNode;
  action: string;
  run: () => void | Promise<unknown>;
}

const failed = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : "The removal failed.");

/** The confirmation of every removal: Cancel closes it, the destructive action runs `removal.run`. `children` adds to
 * the question (a note, an option); `busy` and `error` come from a page that runs the removal itself. */
export default function ConfirmRemove({
  removal,
  onClose,
  children,
  busy = false,
  error = null,
}: {
  removal: Removal | null;
  onClose: () => void;
  children?: React.ReactNode;
  busy?: boolean;
  error?: string | null;
}) {
  const [running, setRunning] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  // A new question starts clean (pages may build the removal on every render, so its identity is not the key).
  const open = removal !== null;
  useEffect(() => {
    setRunning(false);
    setFailure(null);
  }, [open, removal?.title]);
  const disabled = busy || running;
  const shown = error ?? failure;
  return (
    <AlertDialog open={removal !== null} onOpenChange={(open) => !open && !disabled && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{removal?.title}</AlertDialogTitle>
          <AlertDialogDescription>{removal?.description}</AlertDialogDescription>
        </AlertDialogHeader>
        {children}
        {shown && (
          <Alert variant="destructive">
            <AlertDescription>{shown}</AlertDescription>
          </Alert>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={disabled}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            className={buttonVariants({ variant: "destructive" })}
            disabled={disabled}
            onClick={(event) => {
              event.preventDefault();
              if (!removal) return;
              const result = removal.run();
              if (!(result instanceof Promise)) return onClose();
              setRunning(true);
              setFailure(null);
              result.then(
                () => {
                  setRunning(false);
                  onClose();
                },
                (e) => {
                  setRunning(false);
                  setFailure(failed(e));
                },
              );
            }}
          >
            {removal?.action}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
