import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router";
import { BookOpen, ChevronsUpDown, FileWarning, MessageSquare, Search } from "lucide-react";
import { getApps, type AppInfo, type TocGroup } from "../lib/api";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "~/components/ui/hover-card";
import ThemeToggle from "./ThemeToggle";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "~/components/ui/dropdown-menu";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "~/components/ui/sidebar";

const NAV = [
  { label: "Search", to: "search", icon: Search },
  { label: "Chat", to: "chat", icon: MessageSquare },
  { label: "Gaps", to: "gaps", icon: FileWarning },
];

function isNavActive(pathname: string, target: string): boolean {
  return pathname === target || pathname.startsWith(`${target}/`);
}

// App identity box + name, shared between the plain link and the
// dropdown-trigger variants of the sidebar header.
function AppIdentity({ app }: { app: string }) {
  return (
    <>
      <div className="flex aspect-square size-8 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
        <BookOpen />
      </div>
      <div className="grid flex-1 text-left text-sm leading-tight">
        <span className="truncate font-semibold">{app}</span>
        <span className="truncate text-xs text-sidebar-foreground/70">Knowledge Agent v3</span>
      </div>
    </>
  );
}

export default function AppSidebar({ app, groups, activePath }: {
  app: string; groups: TocGroup[]; activePath?: string;
}) {
  const [apps, setApps] = useState<AppInfo[]>([]);
  useEffect(() => { getApps().then(setApps).catch(() => setApps([])); }, []);
  const location = useLocation();

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            {apps.length > 1 ? (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <SidebarMenuButton
                    size="lg"
                    className="data-[state=open]:bg-sidebar-accent data-[state=open]:text-sidebar-accent-foreground"
                  >
                    <AppIdentity app={app} />
                    <ChevronsUpDown className="ml-auto" />
                  </SidebarMenuButton>
                </DropdownMenuTrigger>
                <DropdownMenuContent
                  className="w-[var(--radix-dropdown-menu-trigger-width)] min-w-56 rounded-lg"
                  align="start"
                  sideOffset={4}
                >
                  <DropdownMenuLabel className="text-xs text-muted-foreground">Apps</DropdownMenuLabel>
                  <DropdownMenuGroup>
                    {apps.map((a) => (
                      <DropdownMenuItem key={a.name} asChild>
                        <Link to={`/${a.name}`}>{a.name}</Link>
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuGroup>
                </DropdownMenuContent>
              </DropdownMenu>
            ) : (
              <SidebarMenuButton size="lg" asChild>
                <Link to={`/${app}`}>
                  <AppIdentity app={app} />
                </Link>
              </SidebarMenuButton>
            )}
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarMenu>
            {NAV.map(({ label, to, icon: Icon }) => {
              const target = `/${app}/${to}`;
              return (
                <SidebarMenuItem key={to}>
                  <SidebarMenuButton asChild isActive={isNavActive(location.pathname, target)}>
                    <Link to={target}>
                      <Icon data-icon="inline-start" />
                      <span>{label}</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              );
            })}

          </SidebarMenu>
        </SidebarGroup>
        {groups.map((group) => (
          <SidebarGroup key={group.group}>
            <SidebarGroupLabel>{group.group}</SidebarGroupLabel>
            <SidebarMenu>
              {group.docs.map((doc) => (
                // Titles truncate against the rail width; the full name is a
                // hover card away (Goran's ruling 2026-08-05 — freshness badge
                // removed, doc metadata lives in the DocView header now).
                <SidebarMenuItem key={doc.path}>
                  <HoverCard openDelay={300} closeDelay={100}>
                    <HoverCardTrigger asChild>
                      <SidebarMenuButton asChild isActive={doc.path === activePath}>
                        <Link to={`/${app}/doc/${doc.path}`}>
                          <span className="w-full truncate">{doc.title}</span>
                        </Link>
                      </SidebarMenuButton>
                    </HoverCardTrigger>
                    <HoverCardContent side="right" align="start" className="w-auto max-w-72 p-2 text-sm">
                      {doc.title}
                    </HoverCardContent>
                  </HoverCard>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroup>
        ))}
      </SidebarContent>
      <SidebarFooter>
        <ThemeToggle />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
