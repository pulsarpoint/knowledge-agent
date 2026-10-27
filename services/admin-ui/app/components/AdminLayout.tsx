import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router";
import {
  Bell,
  Brain,
  BookOpen,
  Braces,
  CalendarClock,
  Bot,
  Database,
  Files,
  Inbox,
  ListChecks,
  FolderGit2,
  Layers,
  Network,
  LogOut,
  Plug,
  Server,
  Settings,
  Shield,
  Ticket,
  Workflow,
} from "lucide-react";
import { ApiError, getAdminSession } from "../lib/api";
import ThemeToggle from "./ThemeToggle";
import { cn } from "~/lib/utils";
import { WidePageContext } from "~/lib/widePage";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { Toaster } from "~/components/ui/sonner";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
} from "~/components/ui/sidebar";

/** The sections of the administration: a page belongs to the section whose path it is or starts with (a record's page
 * under its list), so the layout always shows the section's title and description and the sidebar highlights it. */
const SECTIONS = [
  {
    prefix: "/output-schemas",
    title: "Output schemas",
    description:
      "JSON Schemas a run can be asked to finish with, versioned. Handlers, the API, Test and Run agent choose one per run.",
  },
  { prefix: "/prompts", title: "Prompts", description: "Reusable instructions for agents, organised by tags." },
  {
    prefix: "/notifications",
    title: "Notifications",
    description: "Answers agents gave to events, and the calls that failed.",
  },
  {
    prefix: "/memory",
    title: "Memory",
    description: "What agents saved, shared by all agents. Search, filter, edit, restore or delete entries.",
  },
  {
    prefix: "/events",
    title: "Events",
    description: "Things that happened at your sources, and handlers that turn them into new events or agent answers.",
  },
  { prefix: "/schedules", title: "Schedules", description: "Run work on a timetable, such as pulling Jira tickets." },
  {
    prefix: "/activity/runs",
    title: "Agent runs",
    description: "Complete records of agent runs: instructions, reasoning, tool calls with full output, and answers.",
  },
  {
    // A session is one agent run with its conversation: it belongs with the run records.
    prefix: "/sessions",
    title: "Agent runs",
    description: "Complete records of agent runs: instructions, reasoning, tool calls with full output, and answers.",
    nav: "/activity/runs",
  },
  {
    prefix: "/storage",
    title: "Storage",
    description: "See where source code and code graphs are stored, and browse their folders.",
  },
  {
    prefix: "/environments",
    title: "Environments",
    description: "Organize services and servers into Development, Staging, Production, or your own environments.",
  },
  {
    prefix: "/documents",
    title: "Documents",
    description: "Write and maintain documentation linked to your code projects, services, and servers.",
  },
  {
    prefix: "/mcp-servers",
    title: "MCP servers",
    description: "Manage external tools for Code search and SSH agents.",
  },
  {
    prefix: "/llm-settings",
    title: "LLM Settings",
    description: "Manage model providers, credentials, and connection tests.",
  },
  {
    prefix: "/skills",
    title: "Skills",
    description: "Folders of instructions, references and scripts your agents load when a task needs them.",
  },
  {
    prefix: "/agents",
    title: "Agents",
    description: "Create agents with their own models, instructions, resources, and conversations.",
  },
  {
    prefix: "/code-projects",
    title: "Code projects",
    description: "Browse the mounted code folder and organize local folders and Git sources into projects.",
  },
  {
    prefix: "/code-graphs",
    title: "Code graphs",
    description: "Generate and explore each code project's graph: symbols, relationships, routes and languages.",
  },
  {
    prefix: "/system-map",
    title: "System map",
    description: "The system as a graph you open one level at a time.",
  },
  {
    prefix: "/services",
    title: "Services",
    description: "Connect applications, source code, and server deployments for agent investigations.",
  },
  {
    prefix: "/servers",
    title: "Servers",
    description: "Manage hosts, descriptions, and tags for your service infrastructure.",
  },
  {
    prefix: "/jira/settings",
    title: "Jira Settings",
    description: "Configure access to your Jira Data Center server and test the connection.",
  },
  { prefix: "/jira/tickets", title: "Jira Tickets", description: "Filter the Jira tickets saved by the pull." },
];

/** The section a path belongs to; `prefix` is the sidebar entry it highlights. */
export function sectionOf(pathname: string) {
  const found = SECTIONS.find(({ prefix }) => pathname === prefix || pathname.startsWith(`${prefix}/`));
  return found && { ...found, prefix: found.nav ?? found.prefix };
}

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const [authorized, setAuthorized] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [wide, setWide] = useState(false); // a page asked for the full width (useWidePage)
  const { pathname } = useLocation();
  useEffect(() => {
    let active = true;
    getAdminSession()
      .then((session) => {
        if (!session.is_admin) throw new Error("Administrator access required");
        if (active) setAuthorized(true);
      })
      .catch((e) => {
        if (active) setError(e instanceof ApiError ? e.message : "Administrator access could not be verified");
      });
    return () => {
      active = false;
    };
  }, []);

  if (error)
    return (
      <main className="mx-auto flex max-w-xl flex-col gap-4 p-8">
        <Alert variant="destructive">
          <AlertTitle>Administration unavailable</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
        <Button asChild variant="outline">
          <a href="/oauth2/sign_out">Sign out</a>
        </Button>
      </main>
    );
  if (!authorized)
    return (
      <main className="p-8">
        <Skeleton className="h-32 w-full" />
      </main>
    );
  const section = sectionOf(pathname);
  const at = (prefix: string) => section?.prefix === prefix;
  return (
    <SidebarProvider>
      <Sidebar collapsible="icon">
        <SidebarHeader>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton size="lg" asChild>
                <Link to="/">
                  <Shield />
                  <span className="flex flex-col">
                    <span className="font-semibold">Knowledge Agent</span>
                    <span className="text-xs text-muted-foreground">Administration</span>
                  </span>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarHeader>
        <SidebarContent>
          <SidebarGroup role="navigation" aria-label="Administration">
            <SidebarGroupLabel>Administration</SidebarGroupLabel>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={at("/storage")}>
                  <Link to="/storage">
                    <Database />
                    <span>Storage</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={at("/skills")}>
                  <Link to="/skills">
                    <BookOpen />
                    <span>Skills</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={at("/prompts")}>
                  <Link to="/prompts">
                    <BookOpen />
                    <span>Prompts</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={at("/mcp-servers")}>
                  <Link to="/mcp-servers">
                    <Plug />
                    <span>MCP servers</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={at("/activity/runs")}>
                  <Link to="/activity/runs">
                    <ListChecks />
                    <span>Agent runs</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={at("/schedules")}>
                  <Link to="/schedules">
                    <CalendarClock />
                    <span>Schedules</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={at("/events")}>
                  <Link to="/events">
                    <Inbox />
                    <span>Events</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={at("/notifications")}>
                  <Link to="/notifications">
                    <Bell />
                    <span>Notifications</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={at("/memory")}>
                  <Link to="/memory">
                    <Brain />
                    <span>Memory</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroup>
          <SidebarGroup role="navigation" aria-label="Agents">
            <SidebarGroupLabel>Agents</SidebarGroupLabel>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={at("/agents")}>
                  <Link to="/agents" aria-current={at("/agents") ? "page" : undefined}>
                    <Bot />
                    <span>All agents</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={at("/output-schemas")}>
                  <Link to="/output-schemas">
                    <Braces />
                    <span>Output schemas</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroup>
          <SidebarGroup>
            <SidebarGroupLabel>LLM Settings</SidebarGroupLabel>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={at("/llm-settings")}>
                  <Link to="/llm-settings">
                    <Settings />
                    <span>Models</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroup>
          <SidebarGroup>
            <SidebarGroupLabel>Jira</SidebarGroupLabel>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={at("/jira/tickets")}>
                  <Link to={`/jira/tickets`}>
                    <Ticket />
                    <span>Tickets</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={at("/jira/settings")}>
                  <Link to={`/jira/settings`}>
                    <Settings />
                    <span>Settings</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroup>
          <SidebarGroup role="navigation" aria-label="Environments">
            <SidebarGroupLabel>Environments</SidebarGroupLabel>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={at("/environments")}>
                  <Link to="/environments">
                    <Network />
                    <span>Environments</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={at("/services")}>
                  <Link to={`/services`}>
                    <Layers />
                    <span>Services</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={at("/servers")}>
                  <Link to={`/servers`}>
                    <Server />
                    <span>Servers</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={at("/system-map")}>
                  <Link to="/system-map">
                    <Workflow />
                    <span>System map</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroup>
          <SidebarGroup role="navigation" aria-label="Code">
            <SidebarGroupLabel>Code</SidebarGroupLabel>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={at("/code-projects")}>
                  <Link to="/code-projects">
                    <FolderGit2 />
                    <span>Code projects</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={at("/code-graphs")}>
                  <Link to="/code-graphs">
                    <Network />
                    <span>Code graphs</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroup>
          <SidebarGroup role="navigation" aria-label="Docs">
            <SidebarGroupLabel>Docs</SidebarGroupLabel>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={at("/documents")}>
                  <Link to="/documents">
                    <Files />
                    <span>Documents</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroup>
        </SidebarContent>
        <SidebarFooter>
          <ThemeToggle />
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton asChild>
                <a href="/oauth2/sign_out">
                  <LogOut />
                  <span>Sign out</span>
                </a>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarFooter>
      </Sidebar>
      <SidebarInset>
        <header className="flex h-16 items-center gap-3 border-b px-6">
          <SidebarTrigger />
          <span className="text-sm text-muted-foreground">Administration</span>
        </header>
        <main className={cn("mx-auto flex w-full flex-col gap-6 p-6", !wide && "max-w-6xl")}>
          <div>
            <h1 className="text-2xl font-semibold">{section?.title ?? "Administration"}</h1>
            {section && <p className="mt-1 text-sm text-muted-foreground">{section.description}</p>}
          </div>
          <WidePageContext.Provider value={setWide}>{children}</WidePageContext.Provider>
        </main>
        <Toaster position="bottom-right" />
      </SidebarInset>
    </SidebarProvider>
  );
}
