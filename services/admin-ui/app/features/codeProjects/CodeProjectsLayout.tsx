import { useState } from "react";
import { Outlet, useOutletContext } from "react-router";
import { toast } from "sonner";
import CodeProjectForm from "./CodeProjectForm";
import SectionLinks from "~/components/SectionLinks";
import { useSheetGuard } from "~/components/LeaveGuard";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";

/** What the list's sub-pages get from the layout: `reloaded` counts saved new code projects (the list reloads on it);
 * `newProject` opens the New code project sheet (the list's toolbar action). */
export interface CodeProjectsPageContext {
  reloaded: number;
  newProject: () => void;
}
export const useCodeProjectsPage = () => useOutletContext<CodeProjectsPageContext>();

const SECTIONS = [
  {
    to: "/code-projects",
    label: "Code projects",
    end: true,
    help: "Local folders and Git sources organized into projects that agents can search.",
  },
  {
    to: "/code-projects/folder",
    label: "Code folder",
    help: "Browse the mounted code folder that projects point into.",
  },
];

/** Code projects (`/code-projects`): the sub-pages Code projects (the table) and Code folder (the code root browser)
 * as links; New code project (in the Code projects list's toolbar) opens the code project form in a right-side
 * sheet. */
export default function CodeProjectsLayout() {
  const [creating, setCreating] = useState(false);
  const [reloaded, setReloaded] = useState(0);
  // Escape, the X or a click outside with input asks Stay / Leave; Cancel discards at once.
  const [dirty, setDirty] = useState(false);
  const guard = useSheetGuard(dirty);
  const close = () => {
    setCreating(false);
    setDirty(false);
  };
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <SectionLinks label="Code projects sections" sections={SECTIONS} />
      <Outlet context={{ reloaded, newProject: () => setCreating(true) } satisfies CodeProjectsPageContext} />
      <Sheet open={creating} onOpenChange={(open) => !open && guard.close(close)}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
          <SheetHeader>
            <SheetTitle>New code project</SheetTitle>
            <SheetDescription>
              Map folders inside the code root, or clone a Git repository into a named folder.
            </SheetDescription>
          </SheetHeader>
          <div className="px-4 pb-4">
            {creating && (
              <CodeProjectForm
                project={null}
                onCancel={close}
                onDirtyChange={setDirty}
                onSaved={(saved) => {
                  close();
                  toast.success(saved);
                  setReloaded((n) => n + 1);
                }}
              />
            )}
          </div>
        </SheetContent>
        {guard.dialog}
      </Sheet>
    </div>
  );
}
