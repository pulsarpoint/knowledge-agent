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

/** What a removal asks before it happens: the question, what it means, and the action's label. */
export interface Removal {
  title: string;
  description: string;
  action: string;
  run: () => void;
}

/** The confirmation of a removal in the workspace: Cancel closes it, the action runs `removal.run`. */
export default function ConfirmRemove({ removal, onClose }: { removal: Removal | null; onClose: () => void }) {
  return (
    <AlertDialog open={removal !== null} onOpenChange={(open) => !open && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{removal?.title}</AlertDialogTitle>
          <AlertDialogDescription>{removal?.description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            className={buttonVariants({ variant: "destructive" })}
            onClick={() => {
              removal?.run();
              onClose();
            }}
          >
            {removal?.action}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
