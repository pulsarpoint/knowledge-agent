import { useEffect, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { MessageSquare, X } from "lucide-react";
import { resourceTag, type ResourceType } from "./api";
import { useAgentSettings } from "./agentSettings";
import { useWidePage } from "~/lib/widePage";
import { cn } from "~/lib/utils";
import ChatColumn from "./ChatColumn";
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

/** A server's, service's or environment's page: its Info (from the page) with Chat (the latest open chat, else a new one)
 * and the page's actions beside it. The chat panel (ChatColumn) has the open conversation's title, Conversations (every
 * chat about this resource: the resource runs tagged with it, newest first; its Write with an agent runs are not chats
 * on it), New chat and Hide chat.
 * The chat panel is hidden until Chat is clicked; `?chat=<id>` opens that chat (a chat of another resource is refused by the
 * chat itself) and follows the shown chat. At 2xl the Info is on the left and the chat on the right; below that, tabs
 * Info / Chat. With `pages`, the page is laid out in tabs of the given pages; `?view=<value>` keeps the open tab (none or
 * unknown: the first). With `children` (a page made of sub-pages: its section links and the open sub-page), they are
 * shown under the Info instead. `onSessionChange` is called when the
 * shown chat's turns or status change (a page reloads what the chat's agent may have changed). */
export default function ResourceChats({
  resource,
  info,
  actions,
  blockedReason = null,
  wide = false,
  pages,
  children,
  onSessionChange,
}: {
  resource: ChatResource;
  info: ReactNode;
  actions?: ReactNode;
  blockedReason?: string | null;
  /** The page needs the full width even with the chat closed (the environment workspace). */
  wide?: boolean;
  pages?: ResourcePage[];
  children?: ReactNode;
  onSessionChange?: () => void;
}) {
  const [params, setParams] = useSearchParams();
  const chat = params.get("chat");
  const tag = resourceTag(resource);
  const [open, setOpen] = useState(chat !== null);
  const [tab, setTab] = useState<"info" | "chat">(chat !== null ? "chat" : "info");
  const scope = { kind: resource.type, id: resource.id, name: resource.name };
  const settings = useAgentSettings(open ? scope : null);
  useWidePage(open || wide);

  // A ?chat= link followed on this page opens the chat panel.
  useEffect(() => {
    if (chat !== null && !open) {
      setOpen(true);
      setTab("chat");
    }
  }, [chat]);

  const show = () => {
    setOpen(true);
    setTab("chat");
  };
  const hide = () => {
    setOpen(false);
    setTab("info");
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.delete("chat");
        return next;
      },
      { replace: true },
    );
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
          {children}
        </div>
        {open && (
          <div
            data-testid="resource-chat"
            className={cn("min-w-0 2xl:sticky 2xl:top-4", tab === "info" ? "hidden 2xl:block" : "block")}
          >
            <ChatColumn
              scope={scope}
              settings={settings}
              filter={{ tag, purpose: "resource" }}
              description={`Every chat about ${resource.name}, newest first.`}
              blockedReason={blockedReason}
              actions={
                <Button type="button" size="sm" variant="ghost" onClick={hide}>
                  <X data-icon="inline-start" />
                  Hide chat
                </Button>
              }
              onSessionChange={(id) => {
                // the page reloads what the chat's agent may have changed
                if (id !== null) onSessionChange?.();
              }}
            />
          </div>
        )}
      </div>
    </div>
  );
}
