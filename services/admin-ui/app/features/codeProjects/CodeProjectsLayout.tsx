import { useEffect, useState } from "react";
import { Outlet, useLocation, useOutletContext } from "react-router";
import { Plus } from "lucide-react";
import CodeProjectForm from "./CodeProjectForm";
import SectionLinks from "~/components/SectionLinks";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";

/** What the list's sub-pages get from the layout: `reloaded` counts saved new projects (Projects reloads on it), and
 * the notice shown above the sub-page. */
export interface CodeProjectsPageContext {
  reloaded: number;
  setNotice: (notice: string) => void;
}
export const useCodeProjectsPage = () => useOutletContext<CodeProjectsPageContext>();

const SECTIONS = [
  { to: "/code-projects", label: "Projects", end: true },
  { to: "/code-projects/folder", label: "Code folder" },
];

/** Code projects (`/code-projects`): the sub-pages Projects (the table) and Code folder (the code root browser) as
 * links; on Projects, New project opens the code project form in a right-side sheet. */
export default function CodeProjectsLayout() {
  const { pathname } = useLocation();
  const projects = pathname.replace(/\/$/, "") === "/code-projects";
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState("");
  const [reloaded, setReloaded] = useState(0);
  useEffect(() => setNotice(""), [pathname]);
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <SectionLinks label="Code projects sections" sections={SECTIONS} />
        {projects && (
          <Button
            onClick={() => {
              setNotice("");
              setCreating(true);
            }}
          >
            <Plus data-icon="inline-start" />
            New project
          </Button>
        )}
      </div>
      {notice && (
        <Alert>
          <AlertDescription role="status">{notice}</AlertDescription>
        </Alert>
      )}
      <Outlet context={{ reloaded, setNotice } satisfies CodeProjectsPageContext} />
      <Sheet open={creating} onOpenChange={setCreating}>
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
                onCancel={() => setCreating(false)}
                onSaved={(saved) => {
                  setCreating(false);
                  setNotice(saved);
                  setReloaded((n) => n + 1);
                }}
              />
            )}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
