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

// The document page with its Ask agent run (/documents/:id/agent/:runId) is the same page: the editor stays.
const page = (pathname: string) => pathname.replace(/\/agent\/[^/]+\/?$/, "");

export default function UnsavedDocumentChanges({
  dirty,
  leaving,
}: {
  dirty: boolean;
  leaving: React.RefObject<boolean>;
}) {
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirty && !leaving.current && page(currentLocation.pathname) !== page(nextLocation.pathname),
  );
  useBeforeUnload((event) => {
    if (dirty && !leaving.current) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  return (
    <AlertDialog
      open={blocker.state === "blocked"}
      onOpenChange={(open) => {
        if (!open && blocker.state === "blocked") blocker.reset();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Discard unsaved changes?</AlertDialogTitle>
          <AlertDialogDescription>
            Your unsaved edits will be lost. Saved versions will remain available.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel
            onClick={() => {
              if (blocker.state === "blocked") blocker.reset();
            }}
          >
            Keep editing
          </AlertDialogCancel>
          <AlertDialogAction
            onClick={() => {
              if (blocker.state === "blocked") blocker.proceed();
            }}
          >
            Discard changes
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
