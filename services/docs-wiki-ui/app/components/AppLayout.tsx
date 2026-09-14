import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router";
import { getToc, type TocGroup } from "../lib/api";
import AppSidebar from "./AppSidebar";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "~/components/ui/breadcrumb";
import { Separator } from "~/components/ui/separator";
import { SidebarInset, SidebarProvider, SidebarTrigger } from "~/components/ui/sidebar";

// Page-name crumb for the routes that don't resolve a doc out of the toc
// (search/chat/gaps) — keyed off the path segment right after :app.
const SECTION_LABELS: Record<string, string> = {
  search: "Search",
  chat: "Chat",
  gaps: "Gaps",
};

export default function AppLayout({ app, activePath, children }: {
  app: string; activePath?: string; children: React.ReactNode;
}) {
  const [groups, setGroups] = useState<TocGroup[]>([]);
  useEffect(() => { getToc(app).then(setGroups).catch(() => setGroups([])); }, [app]);

  const location = useLocation();
  const section = location.pathname.split("/")[2];
  const sectionLabel = section ? SECTION_LABELS[section] : undefined;

  // Resolve the active doc's quadrant + title out of the toc groups already
  // fetched above. An activePath that isn't (yet) found in `groups` — toc
  // still loading, or a path outside any quadrant — just falls back to the
  // app crumb alone; it never throws.
  let doc: { group: string; title: string } | null = null;
  if (activePath) {
    for (const group of groups) {
      const hit = group.docs.find((d) => d.path === activePath);
      if (hit) {
        doc = { group: group.group, title: hit.title };
        break;
      }
    }
  }

  const hasTrailing = Boolean(doc || sectionLabel);

  // Parse sidebar_state cookie to restore persisted open/collapsed preference.
  // Default to true (expanded) if absent or unreadable; SidebarProvider writes
  // this cookie on toggle. Plain document.cookie access is safe in jsdom.
  let defaultOpen = true;
  if (typeof document !== "undefined") {
    const cookies = document.cookie.split("; ");
    const sidebarCookie = cookies.find((c) => c.startsWith("sidebar_state="));
    if (sidebarCookie) {
      const value = sidebarCookie.split("=")[1];
      defaultOpen = value !== "false";
    }
  }

  return (
    <SidebarProvider defaultOpen={defaultOpen}>
      <AppSidebar app={app} groups={groups} activePath={activePath} />
      <SidebarInset>
        <header className="flex h-16 shrink-0 items-center gap-2 border-b px-4">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="mr-2 h-4" />
          <Breadcrumb>
            <BreadcrumbList>
              <BreadcrumbItem className={hasTrailing ? "hidden md:block" : undefined}>
                {hasTrailing ? (
                  <BreadcrumbLink asChild>
                    <Link to={`/${app}`}>{app}</Link>
                  </BreadcrumbLink>
                ) : (
                  <BreadcrumbPage>{app}</BreadcrumbPage>
                )}
              </BreadcrumbItem>
              {hasTrailing && <BreadcrumbSeparator className="hidden md:block" />}
              {doc && (
                <>
                  {/* Quadrant label: plain (non-link) crumb — kept short on
                      narrow viewports so the header never overflows. */}
                  <BreadcrumbItem>
                    <span className="inline-block max-w-[5rem] truncate align-bottom sm:max-w-none">
                      {doc.group}
                    </span>
                  </BreadcrumbItem>
                  <BreadcrumbSeparator />
                </>
              )}
              {(doc || sectionLabel) && (
                <BreadcrumbItem>
                  <BreadcrumbPage className="inline-block max-w-[9rem] truncate align-bottom sm:max-w-none">
                    {doc ? doc.title : sectionLabel}
                  </BreadcrumbPage>
                </BreadcrumbItem>
              )}
            </BreadcrumbList>
          </Breadcrumb>
        </header>
        <div className="flex-1 min-w-0 p-4 md:p-6">{children}</div>
      </SidebarInset>
    </SidebarProvider>
  );
}
