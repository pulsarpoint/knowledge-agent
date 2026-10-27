import { useEffect, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { History, Plus } from "lucide-react";
import { getSessions, statusLabels, type ChatScope, type RunSummary, type SessionPurpose } from "./api";
import type { useAgentSettings } from "./agentSettings";
import AgentChat from "./AgentChat";
import { Button } from "~/components/ui/button";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";

/** A page's chat: the open conversation's title, Conversations (a right-side sheet like Run details: the page's
 * conversations, `filter` of the runs list, newest first, the open one marked; picking one opens it), New chat (a new
 * conversation) and the page's own `actions`, above the chat (AgentChat). `?chat=<id>` opens that conversation (one of
 * another page is refused by the chat itself) and follows the shown one. A resource's page (ResourceChats) and Write
 * with an agent use it. `onSessionChange` reports the shown conversation (null: a new one) whenever its turns or status
 * change; `mention` and `onDocumentWrite` pass through to the chat. */
export default function ChatColumn({
  scope,
  settings,
  filter,
  description,
  actions,
  blockedReason = null,
  onSessionChange,
  mention,
  onDocumentWrite,
}: {
  scope: ChatScope;
  settings: ReturnType<typeof useAgentSettings>;
  filter: { tag?: string; purpose?: SessionPurpose };
  /** The Conversations sheet's description. */
  description: string;
  actions?: ReactNode;
  blockedReason?: string | null;
  onSessionChange?: (id: string | null, version: string) => void;
  mention?: { text: string; key: number } | null;
  onDocumentWrite?: (sessionId: string, writes: number) => void;
}) {
  const [params, setParams] = useSearchParams();
  const chat = params.get("chat");
  const [fresh, setFresh] = useState(0); // > 0: New chat was clicked (the chat starts on a new conversation)
  // The chat shown when New chat was clicked: until the URL drops it, the new chat must not be handed it (it would reopen it).
  const [left, setLeft] = useState<string | null>(null);
  const initialRunId = left !== null && chat === left ? null : chat;
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [listFailed, setListFailed] = useState(false);
  const [listed, setListed] = useState(0);
  const [listing, setListing] = useState(false); // the Conversations sheet is open
  const shownVersion = useRef("");
  const listKey = `${filter.tag ?? ""}:${filter.purpose ?? ""}`;

  useEffect(() => {
    const controller = new AbortController();
    getSessions(filter, controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        setRuns(value.sessions);
        setListFailed(false);
      })
      .catch(() => {
        if (!controller.signal.aborted) setListFailed(true);
      });
    return () => controller.abort();
  }, [listKey, listed]);
  // Once the URL has left the old chat, it no longer matters.
  useEffect(() => {
    if (chat !== left) setLeft(null);
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
  const pick = (id: string) => {
    setLeft(null);
    if (id !== chat) setChat(id);
  };
  const newChat = () => {
    setLeft(chat);
    setFresh((n) => n + 1);
    setChat(null);
  };

  const shown = initialRunId === null ? null : (runs?.find((item) => item.id === initialRunId) ?? null);
  const title = initialRunId === null ? "New conversation" : (shown?.title ?? "Conversation");
  return (
    <>
      <div className="flex items-center gap-2">
        <h2 data-testid="conversation-title" className="mr-auto min-w-0 truncate text-sm font-semibold" title={title}>
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
        {actions}
      </div>
      <Sheet open={listing} onOpenChange={setListing}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-sm">
          <SheetHeader>
            <SheetTitle>Conversations</SheetTitle>
            <SheetDescription>{description}</SheetDescription>
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
      {/* One chat per New chat click: a new key starts it on a new conversation. */}
      <AgentChat
        key={`${listKey}:${fresh}`}
        scope={scope}
        settings={settings}
        initialRunId={initialRunId}
        fresh={fresh > 0}
        blockedReason={blockedReason}
        onRunChange={followRun}
        mention={mention}
        onDocumentWrite={onDocumentWrite}
        onSessionChange={(id, version) => {
          // the list shows each chat's status: reload it when the shown chat's turns or status change
          if (id !== null && version !== shownVersion.current) {
            shownVersion.current = version;
            setListed((n) => n + 1);
          }
          onSessionChange?.(id, version);
        }}
      />
    </>
  );
}
