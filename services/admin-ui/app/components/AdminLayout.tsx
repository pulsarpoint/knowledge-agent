import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router";
import {
  Bell,
  Brain,
  BookOpen,
  Braces,
  CalendarClock,
  FolderOpen,
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

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const [authorized, setAuthorized] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [wide, setWide] = useState(false); // a page asked for the full width (useWidePage)
  const { pathname, search } = useLocation();
  const chatPage = pathname.startsWith("/agents/") && new URLSearchParams(search).get("view") === "test";
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
  const repositories = pathname === `/code-projects`;
  const codeGraphs = pathname === "/code-graphs";
  const services = pathname === `/services`;
  const environments = pathname === "/environments";
  const servers = pathname === `/servers` || pathname.startsWith("/servers/");
  const systemMap = pathname === "/system-map";
  const jiraSettings = pathname === `/jira/settings`;
  const jiraTickets = pathname === `/jira/tickets`;
  const agentPage = pathname === "/agents" || pathname.startsWith("/agents/");
  const docsPage = pathname.startsWith("/documents") || pathname.startsWith("/documentation/");
  const promptsPage = pathname === "/prompts" || pathname.startsWith("/prompts/");
  const sessionPage = pathname.startsWith("/sessions/") || pathname.startsWith("/servers/"); // its own heading is the session or server title
  const docsFolders = pathname.startsWith("/documentation/folders");
  const runsPage = pathname.startsWith("/activity/runs");
  const schedulesPage = pathname === "/schedules";
  const eventsPage = pathname === "/events" || pathname.startsWith("/events/");
  const notificationsPage = pathname === "/notifications";
  const memoryPage = pathname === "/memory" || pathname.startsWith("/memory/");
  const outputSchemasPage = pathname === "/output-schemas" || pathname.startsWith("/output-schemas/");
  const title = outputSchemasPage
    ? "Output schemas"
    : promptsPage
      ? "Prompts"
      : notificationsPage
        ? "Notifications"
        : memoryPage
          ? "Memory"
          : eventsPage
            ? "Events"
            : schedulesPage
              ? "Schedules"
              : runsPage
                ? "Agent runs"
                : pathname === "/storage"
                  ? "Storage"
                  : environments
                    ? "Environments"
                    : docsPage
                      ? docsFolders
                        ? "Documentation folders"
                        : "Documents"
                      : pathname === "/mcp-servers"
                        ? "MCP servers"
                        : pathname === "/llm-settings"
                          ? "LLM Settings"
                          : pathname === "/skills"
                            ? "Skills"
                            : agentPage
                              ? "Agents"
                              : repositories
                                ? "Code projects"
                                : codeGraphs
                                  ? "Code graphs"
                                  : systemMap
                                    ? "System map"
                                    : services
                                      ? "Services"
                                      : servers
                                        ? "Servers"
                                        : jiraSettings
                                          ? "Jira Settings"
                                          : jiraTickets
                                            ? "Jira Tickets"
                                            : "Administration";
  const description = outputSchemasPage
    ? "JSON Schemas a run can be asked to finish with, versioned. Handlers, the API, Test and Run agent choose one per run."
    : promptsPage
      ? "Reusable instructions for agents, organised by tags."
      : notificationsPage
        ? "Answers agents gave to events, and the calls that failed."
        : memoryPage
          ? "What agents saved, shared by all agents. Search, filter, edit, restore or delete entries."
          : eventsPage
            ? "Things that happened at your sources, and handlers that turn them into new events or agent answers."
            : schedulesPage
              ? "Run work on a timetable, such as pulling Jira tickets."
              : runsPage
                ? "Complete records of agent runs: instructions, reasoning, tool calls with full output, and answers."
                : pathname === "/storage"
                  ? "See where source code and code graphs are stored, and browse their folders."
                  : environments
                    ? "Organize services and servers into Development, Staging, Production, or your own environments."
                    : docsPage
                      ? "Write and maintain documentation linked to your code projects, services, and servers."
                      : pathname === "/mcp-servers"
                        ? "Manage external tools for Code search and SSH agents."
                        : pathname === "/llm-settings"
                          ? "Manage model providers, credentials, and connection tests."
                          : pathname === "/skills"
                            ? "Manage reusable instructions for your agents."
                            : agentPage
                              ? "Create agents with their own models, instructions, resources, and test conversations."
                              : repositories
                                ? "Browse the mounted code folder and organize local folders and Git sources into projects."
                                : codeGraphs
                                  ? "Generate and explore each code project's graph: symbols, relationships, routes and languages."
                                  : services
                                    ? "Connect applications, source code, and server deployments for agent investigations."
                                    : systemMap
                                      ? "The system as a graph you open one level at a time."
                                      : servers
                                        ? "Manage hosts, descriptions, and tags for your service infrastructure."
                                        : jiraSettings
                                          ? "Configure access to your Jira Data Center server and test the connection."
                                          : jiraTickets
                                            ? "Filter the Jira tickets saved by the pull."
                                            : "";
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
                <SidebarMenuButton asChild isActive={pathname === "/storage"}>
                  <Link to="/storage">
                    <Database />
                    <span>Storage</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={pathname === "/skills"}>
                  <Link to="/skills">
                    <BookOpen />
                    <span>Skills</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={promptsPage}>
                  <Link to="/prompts">
                    <BookOpen />
                    <span>Prompts</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={pathname === "/mcp-servers"}>
                  <Link to="/mcp-servers">
                    <Plug />
                    <span>MCP servers</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={runsPage}>
                  <Link to="/activity/runs">
                    <ListChecks />
                    <span>Agent runs</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={schedulesPage}>
                  <Link to="/schedules">
                    <CalendarClock />
                    <span>Schedules</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={eventsPage}>
                  <Link to="/events">
                    <Inbox />
                    <span>Events</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={notificationsPage}>
                  <Link to="/notifications">
                    <Bell />
                    <span>Notifications</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={memoryPage}>
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
                <SidebarMenuButton asChild isActive={agentPage}>
                  <Link to="/agents" aria-current={agentPage ? "page" : undefined}>
                    <Bot />
                    <span>All agents</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={outputSchemasPage}>
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
                <SidebarMenuButton asChild isActive={pathname === "/llm-settings"}>
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
                <SidebarMenuButton asChild isActive={jiraTickets}>
                  <Link to={`/jira/tickets`}>
                    <Ticket />
                    <span>Tickets</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={jiraSettings}>
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
                <SidebarMenuButton asChild isActive={environments}>
                  <Link to="/environments">
                    <Network />
                    <span>Environments</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={services}>
                  <Link to={`/services`}>
                    <Layers />
                    <span>Services</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={servers}>
                  <Link to={`/servers`}>
                    <Server />
                    <span>Servers</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={systemMap}>
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
                <SidebarMenuButton asChild isActive={repositories}>
                  <Link to="/code-projects">
                    <FolderGit2 />
                    <span>Projects</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={codeGraphs}>
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
                <SidebarMenuButton asChild isActive={pathname.startsWith("/documents")}>
                  <Link to="/documents">
                    <Files />
                    <span>Documents</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={docsFolders}>
                  <Link to="/documentation/folders">
                    <FolderOpen />
                    <span>Folders</span>
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
          {!sessionPage && (
            <div className={cn(chatPage && "sr-only")}>
              <h1 className="text-2xl font-semibold">{title}</h1>
              <p className="mt-1 text-sm text-muted-foreground">{description}</p>
            </div>
          )}
          <WidePageContext.Provider value={setWide}>{children}</WidePageContext.Provider>
        </main>
        <Toaster position="bottom-right" />
      </SidebarInset>
    </SidebarProvider>
  );
}
