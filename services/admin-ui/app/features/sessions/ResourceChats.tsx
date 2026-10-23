import { useEffect, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { History, MessageSquare, Plus, X } from "lucide-react";
import { getSessions, resourceTag, statusLabels, type ResourceType, type RunSummary } from "./api";
import { useAgentSettings } from "./agentSettings";
import { useWidePage } from "~/lib/widePage";
import { cn } from "~/lib/utils";
import AgentChat from "./AgentChat";
import { Button } from "~/components/ui/button";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";

/** One tab of a resource page laid out in tabs (`pages`). */
export interface ResourcePage {
  value: string;
  label: string;
  content: ReactNode;
}

export interface ChatResource {
  type: ResourceType;
  id: string;
  name: string;
}

/** A server's, service's or environment's page: its Info (from the page) with Chat (the latest open chat, else a new one)
 * and the page's actions beside it. The chat panel's header has the open conversation's title, Conversations (a right-side
 * sheet like Run details: every conversation about this resource, the runs tagged with it, newest first, the open one
 * marked; picking one opens it), New chat (a new conversation) and Hide chat.
 * The chat panel is hidden until Chat is clicked; `?chat=<id>` opens that chat (a chat of another resource is refused by the
 * chat itself) and follows the shown chat. At 2xl the Info is on the left and the chat on the right; below that, tabs
 * Info / Chat. With `pages`, the page is laid out in tabs of the given pages; `?view=<value>` keeps the open tab (none or
 * unknown: the first). `onSessionChange` is called when the
 * shown chat's turns or status change (a page reloads what the chat's agent may have changed). */
export default function ResourceChats({
  resource,
  info,
  actions,
  blockedReason = null,
  wide = false,
  pages,
  onSessionChange,
}: {
  resource: ChatResource;
  info: ReactNode;
  actions?: ReactNode;
  blockedReason?: string | null;
  /** The page needs the full width even with the chat closed (the environment workspace). */
  wide?: boolean;
  pages?: ResourcePage[];
  onSessionChange?: () => void;
}) {
  const [params, setParams] = useSearchParams();
  const chat = params.get("chat");
  const tag = resourceTag(resource);
  const [open, setOpen] = useState(chat !== null);
  const [fresh, setFresh] = useState(0); // > 0: New chat was clicked (the chat starts on a new conversation)
  // The chat shown when New chat was clicked: until the URL drops it, the new chat must not be handed it (it would reopen it).
  const [left, setLeft] = useState<string | null>(null);
  const initialRunId = left !== null && chat === left ? null : chat;
  const [tab, setTab] = useState<"info" | "chat">(chat !== null ? "chat" : "info");
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [listFailed, setListFailed] = useState(false);
  const [listed, setListed] = useState(0);
  const [listing, setListing] = useState(false); // the Conversations sheet is open
  const shownVersion = useRef("");
  const scope = { kind: resource.type, id: resource.id, name: resource.name };
  const settings = useAgentSettings(open ? scope : null);
  useWidePage(open || wide);

  useEffect(() => {
    const controller = new AbortController();
    getSessions({ tag }, controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        setRuns(value.sessions);
        setListFailed(false);
      })
      .catch(() => {
        if (!controller.signal.aborted) setListFailed(true);
      });
    return () => controller.abort();
  }, [tag, listed]);
  // A ?chat= link followed on this page opens the chat panel; once the URL has left the old chat, it no longer matters.
  useEffect(() => {
    if (chat !== left) setLeft(null);
    if (chat !== null && !open) {
      setOpen(true);
      setTab("chat");
    }
  }, [chat]);

  const setChat = (id: string | null) =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (id === null) next.delete("chat");
        else next.set("chat", id);
        return next;
      },
      { replace: true },
    );
  /** The URL follows the chat's run (a reload reopens it). */
  const followRun = (id: string | null) => {
    if (id !== initialRunId) setChat(id);
    if (id !== null && !runs?.some((item) => item.id === id)) setListed((n) => n + 1); // a chat just started
  };
  const show = () => {
    setOpen(true);
    setTab("chat");
  };
  const pick = (id: string) => {
    setLeft(null);
    show();
    if (id !== chat) setChat(id);
  };
  const newChat = () => {
    setLeft(chat);
    setFresh((n) => n + 1);
    setChat(null);
    show();
  };
  const hide = () => {
    setOpen(false);
    setFresh(0);
    setLeft(null);
    setTab("info");
    setChat(null);
  };

  const view = pages?.some((page) => page.value === params.get("view"))
    ? params.get("view")!
    : (pages?.[0]?.value ?? "");
  const setView = (value: string) =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (value === pages?.[0]?.value) next.delete("view");
        else next.set("view", value);
        return next;
      },
      { replace: true },
    );
  const buttons = (
    <div className="flex flex-wrap items-center gap-2">
      <Button type="button" size="sm" variant="outline" aria-pressed={open} onClick={show}>
        <MessageSquare data-icon="inline-start" />
        Chat
      </Button>
      {actions}
    </div>
  );
  const shown = initialRunId === null ? null : (runs?.find((item) => item.id === initialRunId) ?? null);
  const title = initialRunId === null ? "New conversation" : (shown?.title ?? "Conversation");
  const conversations = (
    <Sheet open={listing} onOpenChange={setListing}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-sm">
        <SheetHeader>
          <SheetTitle>Conversations</SheetTitle>
          <SheetDescription>Every conversation about {resource.name}, newest first.</SheetDescription>
        </SheetHeader>
        <div className="mt-4 flex flex-col gap-2 px-4">
          {listFailed && (
            <Alert variant="destructive">
              <AlertDescription>
                Could not load the conversations.{" "}
                <Button type="button" variant="link" className="h-auto p-0" onClick={() => setListed((n) => n + 1)}>
                  Retry
                </Button>
              </AlertDescription>
            </Alert>
          )}
          {runs !== null && runs.length === 0 && (
            <p className="text-sm text-muted-foreground">No earlier conversations.</p>
          )}
          {runs !== null && runs.length > 0 && (
            <ul aria-label="Conversations" className="flex flex-col divide-y rounded-lg border">
              {runs.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    aria-current={item.id === initialRunId ? "true" : undefined}
                    onClick={() => {
                      setListing(false);
                      pick(item.id);
                    }}
                    className="flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left text-sm hover:bg-muted/50 aria-[current]:bg-muted"
                  >
                    <span className="font-medium [overflow-wrap:anywhere]">{item.title}</span>
                    <span className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                      <span>{statusLabels[item.status]}</span>
                      <span>{new Date(item.last_activity_at).toLocaleString()}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );

  return (
    <div className="flex min-w-0 flex-col gap-4">
      {open && (
        <Tabs value={tab} onValueChange={(value) => setTab(value === "chat" ? "chat" : "info")}>
          <TabsList aria-label="Info or chat" className="2xl:hidden">
            <TabsTrigger value="info">Info</TabsTrigger>
            <TabsTrigger value="chat">Chat</TabsTrigger>
          </TabsList>
        </Tabs>
      )}
      <div
        data-testid="resource-layout"
        className={open ? "grid min-w-0 gap-6 2xl:grid-cols-2 2xl:items-start" : "min-w-0"}
      >
        <div
          data-testid="resource-info"
          className={cn("min-w-0 flex-col gap-6", open && tab === "chat" ? "hidden 2xl:flex" : "flex")}
        >
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0">{info}</div>
            {buttons}
          </div>
          {pages && (
            <Tabs value={view} onValueChange={setView}>
              <TabsList aria-label="Page">
                {pages.map((page) => (
                  <TabsTrigger key={page.value} value={page.value}>
                    {page.label}
                  </TabsTrigger>
                ))}
              </TabsList>
              {pages.map((page) => (
                <TabsContent key={page.value} value={page.value} className="min-w-0">
                  {page.content}
                </TabsContent>
              ))}
            </Tabs>
          )}
        </div>
        {open && (
          <div
            data-testid="resource-chat"
            className={cn("min-w-0 2xl:sticky 2xl:top-4", tab === "info" ? "hidden 2xl:block" : "block")}
          >
            <div className="flex items-center gap-2">
              <h2
                data-testid="conversation-title"
                className="mr-auto min-w-0 truncate text-sm font-semibold"
                title={title}
              >
                {title}
              </h2>
              <Button type="button" size="sm" variant="ghost" onClick={() => setListing(true)}>
                <History data-icon="inline-start" />
                Conversations
              </Button>
              <Button type="button" size="sm" variant="outline" onClick={newChat}>
                <Plus data-icon="inline-start" />
                New chat
              </Button>
              <Button type="button" size="sm" variant="ghost" onClick={hide}>
                <X data-icon="inline-start" />
                Hide chat
              </Button>
            </div>
            {conversations}
            {/* One chat per New chat click: a new key starts it on a new conversation. */}
            <AgentChat
              key={`${tag}:${fresh}`}
              scope={scope}
              settings={settings}
              initialRunId={initialRunId}
              fresh={fresh > 0}
              blockedReason={blockedReason}
              onRunChange={followRun}
              onSessionChange={(id, version) => {
                // the list shows each chat's status: reload it when the shown chat's turns or status change
                if (id !== null && version !== shownVersion.current) {
                  shownVersion.current = version;
                  setListed((n) => n + 1);
                  onSessionChange?.();
                }
              }}
            />
          </div>
        )}
      </div>
    </div>
  );
}
