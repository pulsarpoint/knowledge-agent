import { useEffect, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { MessageSquare, Plus, X } from "lucide-react";
import { getSessions, resourceTag, statusLabels, type ResourceType, type RunSummary } from "./api";
import { useAgentSettings } from "./agentSettings";
import { useWidePage } from "~/lib/widePage";
import { cn } from "~/lib/utils";
import AgentChat from "./AgentChat";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
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

/** A server's, service's or environment's page: its Info (from the page) and its Chats (the runs tagged with it, open first),
 * with Chat (the latest open chat, else a new one), New chat and the page's actions. The chat panel is hidden until one of
 * those is clicked; `?chat=<id>` opens that chat (a chat of another resource is refused by the chat itself) and follows the
 * shown chat. At 2xl the Info is on the left and the chat on the right; below that, tabs Info / Chat.
 * With `pages`, `info` is the page's header (Chat, New chat and the actions beside it) and the page is laid out in tabs:
 * the given pages, then Chats; `?view=<value>` keeps the open tab (none: the first). */
export default function ResourceChats({
  resource,
  info,
  actions,
  blockedReason = null,
  wide = false,
  pages,
}: {
  resource: ChatResource;
  info: ReactNode;
  actions?: ReactNode;
  blockedReason?: string | null;
  /** The page needs the full width even with the chat closed (the environment workspace). */
  wide?: boolean;
  pages?: ResourcePage[];
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

  const view =
    pages?.some((page) => page.value === params.get("view")) || params.get("view") === "chats"
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
      <Button type="button" size="sm" variant="outline" onClick={newChat}>
        <Plus data-icon="inline-start" />
        New chat
      </Button>
      {actions}
    </div>
  );
  const chatList = (
    <>
      {listFailed && (
        <Alert variant="destructive">
          <AlertDescription>
            Could not load the chats.{" "}
            <Button type="button" variant="link" className="h-auto p-0" onClick={() => setListed((n) => n + 1)}>
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {runs !== null && runs.length === 0 && (
        <p className="text-sm text-muted-foreground">No chats yet. Start one with Chat or New chat.</p>
      )}
      {runs !== null && runs.length > 0 && (
        <ul className="flex flex-col divide-y rounded-lg border">
          {runs.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                aria-current={open && item.id === chat ? "true" : undefined}
                onClick={() => pick(item.id)}
                className="flex w-full flex-wrap items-center justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-muted/50 aria-[current]:bg-muted"
              >
                <span className="min-w-0 break-words font-medium">{item.title}</span>
                <span className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  {(item.tags ?? []).includes("proposals") && <Badge variant="secondary">proposals</Badge>}
                  <span>{statusLabels[item.status]}</span>
                  <span>{new Date(item.last_activity_at).toLocaleString()}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </>
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
          {pages ? (
            <>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">{info}</div>
                {buttons}
              </div>
              <Tabs value={view} onValueChange={setView}>
                <TabsList aria-label="Page">
                  {pages.map((page) => (
                    <TabsTrigger key={page.value} value={page.value}>
                      {page.label}
                    </TabsTrigger>
                  ))}
                  <TabsTrigger value="chats">Chats</TabsTrigger>
                </TabsList>
                {pages.map((page) => (
                  <TabsContent key={page.value} value={page.value} className="min-w-0">
                    {page.content}
                  </TabsContent>
                ))}
                <TabsContent value="chats">
                  <section aria-label="Chats" className="flex flex-col gap-2">
                    {chatList}
                  </section>
                </TabsContent>
              </Tabs>
            </>
          ) : (
            <>
              {info}
              <section aria-label="Chats" className="flex flex-col gap-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="text-lg font-semibold">Chats</h2>
                  {buttons}
                </div>
                {chatList}
              </section>
            </>
          )}
        </div>
        {open && (
          <div
            data-testid="resource-chat"
            className={cn("min-w-0 2xl:sticky 2xl:top-4", tab === "info" ? "hidden 2xl:block" : "block")}
          >
            <div className="flex justify-end">
              <Button type="button" size="sm" variant="ghost" onClick={hide}>
                <X data-icon="inline-start" />
                Hide chat
              </Button>
            </div>
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
                }
              }}
            />
          </div>
        )}
      </div>
    </div>
  );
}
