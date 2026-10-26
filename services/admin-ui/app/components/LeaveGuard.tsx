import { useRef } from "react";
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

/** Leaving the page (another section, back, any link) while it has unsaved changes asks first. */
export default function LeaveGuard({ dirty }: { dirty: boolean }) {
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname,
  );
  useBeforeUnload((event) => {
    if (dirty) event.preventDefault();
  });
  // Leave closes the dialog too: its close must not reset the navigation it just let through.
  const leaving = useRef(false);
  const stay = () => {
    if (!leaving.current && blocker.state === "blocked") blocker.reset();
  };
  return (
    <AlertDialog open={blocker.state === "blocked"} onOpenChange={(open) => !open && stay()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Unsaved changes</AlertDialogTitle>
          <AlertDialogDescription>You have unsaved changes. Leave without saving?</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={stay}>Stay</AlertDialogCancel>
          <AlertDialogAction
            onClick={() => {
              if (blocker.state !== "blocked") return;
              leaving.current = true;
              blocker.proceed();
            }}
          >
            Leave
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
