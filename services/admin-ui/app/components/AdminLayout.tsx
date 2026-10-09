import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router";
import { BookOpen, CalendarClock, FolderOpen, Bot, Database, Files, Inbox, ListChecks, Search, FolderGit2, Layers, Network, LogOut, Plug, Server, Settings, Shield, Ticket } from "lucide-react";
import { ApiError, getAdminSession } from "../lib/api";
import ThemeToggle from "./ThemeToggle";
import { cn } from "~/lib/utils";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { Toaster } from "~/components/ui/sonner";
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupLabel, SidebarHeader,
  SidebarInset, SidebarMenu, SidebarMenuButton, SidebarMenuItem, SidebarProvider, SidebarTrigger,
} from "~/components/ui/sidebar";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const [authorized, setAuthorized] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { pathname, search } = useLocation();
  const chatPage = pathname.startsWith('/agents/') && new URLSearchParams(search).get('view') === 'test';
  useEffect(() => {
    let active = true;
    getAdminSession().then((session) => {
      if (!session.is_admin) throw new Error("Administrator access required");
      if (active) setAuthorized(true);
    }).catch((e) => {
      if (active) setError(e instanceof ApiError ? e.message : "Administrator access could not be verified");
    });
    return () => { active = false; };
  }, []);

  if (error) return <main className="mx-auto flex max-w-xl flex-col gap-4 p-8">
    <Alert variant="destructive"><AlertTitle>Administration unavailable</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>
    <Button asChild variant="outline"><a href="/oauth2/sign_out">Sign out</a></Button>
  </main>;
  if (!authorized) return <main className="p-8"><Skeleton className="h-32 w-full" /></main>;
  const repositories = pathname === `/code-projects`;
  const codeGraphs = pathname === '/code-graphs';
  const services = pathname === `/services`;
  const environments = pathname === "/environments";
  const servers = pathname === `/servers`;
  const jiraSettings = pathname === `/jira/settings`;
  const jiraTickets = pathname === `/jira/tickets`;
  const jiraAutoPull = pathname === "/jira/auto-pull";
  const agentPage = pathname === '/agents' || pathname.startsWith('/agents/');
  const ragPages = [
    { path: "collections", title: "RAG Collections", label: "Collections", description: "Search units mapped to folders in the data root: sync state, settings and indexed content.", icon: Files },
    { path: "jobs", title: "RAG Processing jobs", label: "Processing jobs", description: "Follow indexing progress and inspect processing history.", icon: ListChecks },
    { path: "settings", title: "RAG Settings", label: "Settings", description: "Configure file processing and embedding models through the backoffice.", icon: Database },
    { path: "search", title: "RAG Search", label: "Search", description: "Test folder-scoped full-text and embedding retrieval.", icon: Search },
  ];
  const ragPage = ragPages.find((page) => pathname === `/rag/${page.path}` || (page.path === "collections" && pathname.startsWith("/rag/collections")));
  const docsPage = pathname.startsWith('/documents') || pathname.startsWith('/documentation/');
  const docsPrompts = pathname.startsWith('/documentation/prompts');
  const docsHistory = pathname.startsWith('/documentation/generations');
  const sessionPage = /^\/documentation\/generations\/(?!new$)[^/]+$/.test(pathname);  // its own heading is the session title
  const docsFolders = pathname.startsWith('/documentation/folders');
  const runsPage = pathname.startsWith('/activity/runs');
  const schedulesPage = pathname === "/schedules";
  const eventsPage = pathname === "/events" || pathname.startsWith("/events/");
  const title = eventsPage ? "Events" : schedulesPage ? "Schedules" : runsPage ? "Agent runs" : pathname === "/storage" ? "Storage" : environments ? "Environments" : docsPage ? (docsPrompts ? "Documentation prompts" : docsHistory ? "Generate documents" : docsFolders ? "Documentation folders" : "Documents") : pathname === "/mcp-servers" ? "MCP servers" : pathname === "/llm-settings" ? "LLM Settings" : pathname === "/skills" ? "Skills" : agentPage ? "Agents" : ragPage ? ragPage.title : repositories ? "Code projects" : codeGraphs ? "Code graphs" : services ? "Services" : servers ? "Servers" : jiraSettings ? "Jira Settings" : jiraTickets ? "Jira Tickets" : jiraAutoPull ? "Jira Auto pull" : "Administration";
  const description = eventsPage ? "Things that happened at your sources, and handlers that turn them into new events or agent sessions." : schedulesPage ? "Run work on a timetable, such as pulling Jira tickets." : runsPage ? "Complete records of agent runs: instructions, reasoning, tool calls with full output, and answers." : pathname === "/storage" ? "See where source code, RAG files and documentation are stored, and browse their folders." : environments ? "Organize services and servers into Development, Staging, Production, or your own environments." : docsPage ? "Write and maintain documentation linked to your code projects, services, and servers." : pathname === "/mcp-servers" ? "Manage external tools for Code search and SSH agents." : pathname === "/llm-settings" ? "Manage model providers, credentials, and connection tests." : pathname === "/skills" ? "Manage reusable instructions for your agents." : agentPage ? "Create agents with their own models, instructions, resources, and test conversations." : ragPage ? ragPage.description : repositories ? "Browse the mounted code folder and organize local folders and Git sources into projects."
    : codeGraphs ? "Generate and explore each code project's graph: symbols, relationships, routes and languages."
    : services ? "Connect applications, source code, and server deployments for agent investigations."
    : servers ? "Manage hosts, descriptions, and tags for your service infrastructure."
    : jiraSettings ? "Configure access to your Jira Data Center server and test the connection."
    : jiraTickets ? "Filter Jira tickets and export their content to RAG."
    : jiraAutoPull ? "Pull Jira tickets on a timetable with their own query."
    : "";
  return <SidebarProvider>
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <SidebarMenu><SidebarMenuItem><SidebarMenuButton size="lg" asChild>
          <Link to="/"><Shield /><span className="flex flex-col"><span className="font-semibold">Knowledge Agent</span><span className="text-xs text-muted-foreground">Administration</span></span></Link>
        </SidebarMenuButton></SidebarMenuItem></SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup role="navigation" aria-label="Administration"><SidebarGroupLabel>Administration</SidebarGroupLabel><SidebarMenu>

          <SidebarMenuItem><SidebarMenuButton asChild isActive={pathname === "/storage"}><Link to="/storage"><Database /><span>Storage</span></Link></SidebarMenuButton></SidebarMenuItem>
          <SidebarMenuItem><SidebarMenuButton asChild isActive={pathname === "/skills"}><Link to="/skills"><BookOpen /><span>Skills</span></Link></SidebarMenuButton></SidebarMenuItem>
          <SidebarMenuItem><SidebarMenuButton asChild isActive={pathname === "/mcp-servers"}><Link to="/mcp-servers"><Plug /><span>MCP servers</span></Link></SidebarMenuButton></SidebarMenuItem>
          <SidebarMenuItem><SidebarMenuButton asChild isActive={runsPage}><Link to="/activity/runs"><ListChecks /><span>Agent runs</span></Link></SidebarMenuButton></SidebarMenuItem>
          <SidebarMenuItem><SidebarMenuButton asChild isActive={schedulesPage}><Link to="/schedules"><CalendarClock /><span>Schedules</span></Link></SidebarMenuButton></SidebarMenuItem>
          <SidebarMenuItem><SidebarMenuButton asChild isActive={eventsPage}><Link to="/events"><Inbox /><span>Events</span></Link></SidebarMenuButton></SidebarMenuItem>
        </SidebarMenu></SidebarGroup>
        <SidebarGroup role="navigation" aria-label="Agents"><SidebarGroupLabel>Agents</SidebarGroupLabel><SidebarMenu>
          <SidebarMenuItem><SidebarMenuButton asChild isActive={agentPage}><Link to="/agents" aria-current={agentPage ? 'page' : undefined}><Bot /><span>All agents</span></Link></SidebarMenuButton></SidebarMenuItem>
          
        </SidebarMenu></SidebarGroup>
        <SidebarGroup><SidebarGroupLabel>LLM Settings</SidebarGroupLabel><SidebarMenu>
          <SidebarMenuItem><SidebarMenuButton asChild isActive={pathname === "/llm-settings"}><Link to="/llm-settings"><Settings /><span>Models</span></Link></SidebarMenuButton></SidebarMenuItem>
        </SidebarMenu></SidebarGroup>
        <SidebarGroup><SidebarGroupLabel>Jira</SidebarGroupLabel><SidebarMenu>
          <SidebarMenuItem><SidebarMenuButton asChild isActive={jiraTickets}><Link to={`/jira/tickets`}><Ticket /><span>Tickets</span></Link></SidebarMenuButton></SidebarMenuItem>
          <SidebarMenuItem><SidebarMenuButton asChild isActive={jiraAutoPull}><Link to="/jira/auto-pull"><CalendarClock /><span>Auto pull</span></Link></SidebarMenuButton></SidebarMenuItem>
          <SidebarMenuItem><SidebarMenuButton asChild isActive={jiraSettings}><Link to={`/jira/settings`}><Settings /><span>Settings</span></Link></SidebarMenuButton></SidebarMenuItem>
        </SidebarMenu></SidebarGroup>
        <SidebarGroup role="navigation" aria-label="Environments"><SidebarGroupLabel>Environments</SidebarGroupLabel><SidebarMenu>
          <SidebarMenuItem><SidebarMenuButton asChild isActive={environments}><Link to="/environments"><Network /><span>Environments</span></Link></SidebarMenuButton></SidebarMenuItem>
          <SidebarMenuItem><SidebarMenuButton asChild isActive={services}><Link to={`/services`}><Layers /><span>Services</span></Link></SidebarMenuButton></SidebarMenuItem>
          <SidebarMenuItem><SidebarMenuButton asChild isActive={servers}><Link to={`/servers`}><Server /><span>Servers</span></Link></SidebarMenuButton></SidebarMenuItem>
        </SidebarMenu></SidebarGroup>
        <SidebarGroup role="navigation" aria-label="Code"><SidebarGroupLabel>Code</SidebarGroupLabel><SidebarMenu>
          <SidebarMenuItem><SidebarMenuButton asChild isActive={repositories}><Link to="/code-projects"><FolderGit2 /><span>Projects</span></Link></SidebarMenuButton></SidebarMenuItem>
          <SidebarMenuItem><SidebarMenuButton asChild isActive={codeGraphs}><Link to="/code-graphs"><Network /><span>Code graphs</span></Link></SidebarMenuButton></SidebarMenuItem>
        </SidebarMenu></SidebarGroup>
        <SidebarGroup role="navigation" aria-label="Docs"><SidebarGroupLabel>Docs</SidebarGroupLabel><SidebarMenu>
          <SidebarMenuItem><SidebarMenuButton asChild isActive={pathname.startsWith('/documents')}><Link to="/documents"><Files /><span>Documents</span></Link></SidebarMenuButton></SidebarMenuItem>
          <SidebarMenuItem><SidebarMenuButton asChild isActive={docsPrompts}><Link to="/documentation/prompts"><BookOpen /><span>Prompts</span></Link></SidebarMenuButton></SidebarMenuItem>
          <SidebarMenuItem><SidebarMenuButton asChild isActive={docsHistory}><Link to="/documentation/generations"><ListChecks /><span>Generate documents</span></Link></SidebarMenuButton></SidebarMenuItem>
          <SidebarMenuItem><SidebarMenuButton asChild isActive={docsFolders}><Link to="/documentation/folders"><FolderOpen /><span>Folders</span></Link></SidebarMenuButton></SidebarMenuItem>
        </SidebarMenu></SidebarGroup>
        <SidebarGroup><SidebarGroupLabel>RAG</SidebarGroupLabel><SidebarMenu>
          {ragPages.map((page) => <SidebarMenuItem key={page.path}><SidebarMenuButton asChild isActive={ragPage?.path === page.path}><Link to={`/rag/${page.path}`}><page.icon /><span>{page.label}</span></Link></SidebarMenuButton></SidebarMenuItem>)}
        </SidebarMenu></SidebarGroup>
      </SidebarContent>
      <SidebarFooter><ThemeToggle /><SidebarMenu><SidebarMenuItem><SidebarMenuButton asChild>
        <a href="/oauth2/sign_out"><LogOut /><span>Sign out</span></a>
      </SidebarMenuButton></SidebarMenuItem></SidebarMenu></SidebarFooter>
    </Sidebar>
    <SidebarInset>
      <header className="flex h-16 items-center gap-3 border-b px-6"><SidebarTrigger /><span className="text-sm text-muted-foreground">Administration</span></header>
      <main className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-6">
        {!sessionPage && <div className={cn(chatPage && 'sr-only')}><h1 className="text-2xl font-semibold">{title}</h1><p className="mt-1 text-sm text-muted-foreground">{description}</p></div>}
        {children}
      </main>
      <Toaster position="bottom-right" />
    </SidebarInset>
  </SidebarProvider>;
}
