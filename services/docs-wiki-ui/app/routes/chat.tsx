import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { ChevronDown, Trash2 } from "lucide-react";
import AppLayout from "../components/AppLayout";
import ChatPanel from "../components/ChatPanel";
import { deleteChat, getApps, getMyChats, type AppInfo } from "../lib/api";
import { listRecent, removeRecent, type RecentChat } from "../lib/recentChats";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "~/components/ui/alert-dialog";
import { Button, buttonVariants } from "~/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "~/components/ui/collapsible";
import { useIsMobile } from "~/hooks/use-mobile";
import { cn } from "~/lib/utils";

// Descriptions must stay short even for very long first questions — the
// server-side data (and thus what's actually being deleted) isn't bounded.
function truncateForDialog(question: string): string {
  return question.length > 60 ? `${question.slice(0, 60)}…` : question;
}

export default function ChatPage() {
  const { app, key } = useParams() as { app: string; key?: string };
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const [ask, setAsk] = useState<{ enabled: boolean; reason: string | null; sources?: AppInfo["sources"]; contextEnabled?: boolean }>(
    { enabled: true, reason: null },
  );
  const [recent, setRecent] = useState<RecentChat[]>([]);
  // Computes the recent list without touching state — callers decide when
  // (and whether) to commit it, so the mount effect below can apply the
  // codebase's usual stale-guard (see AdminUsersView.tsx et al.) around its
  // own call, while remove()'s call (not effect-bound, always current) just
  // awaits and sets directly.
  const loadRecent = useCallback(async (): Promise<RecentChat[]> => {
    // Server-authoritative: a non-empty server list (identity present)
    // wins and is identical on every machine; empty or failing falls
    // back to this browser's localStorage history.
    try {
      const r = await getMyChats(app);
      if (r.sessions.length > 0) {
        return r.sessions.map((s) => ({
          key: s.session_key,
          firstQuestion: s.first_question,
          ts: Date.parse(s.last_ts) || 0,
        }));
      }
    } catch { /* fall through to localStorage */ }
    return listRecent(app);
  }, [app]);
  useEffect(() => {
    getApps().then((apps) => {
      const me = apps.find((a) => a.name === app);
      if (me) setAsk({ enabled: me.ask_enabled, reason: me.ask_reason, sources: me.sources, contextEnabled: me.context_enabled });
    }).catch(() => undefined);
    let stale = false;
    loadRecent().then((list) => {
      if (stale) return;
      // Merge-preserving: a session bridged into the sidebar while this
      // fetch was in flight (first answer not yet recorded server-side)
      // must survive the resolution.
      setRecent((prev) => {
        const missing = prev.filter((p) => !list.some((l) => l.key === p.key));
        return [...missing, ...list];
      });
    });
    return () => { stale = true; };
  }, [app, loadRecent]);

  // Deleting removes the conversation server-side for everyone holding its
  // link, not just this browser's list — the AlertDialog confirms before
  // it's gone (see the per-row trigger in the recent list below).
  async function remove(k: string) {
    try { await deleteChat(app, k); } catch { /* already gone server-side */ }
    removeRecent(app, k);
    setRecent(await loadRecent());
    if (k === key) navigate(`/${app}/chat`);
  }

  // Shared between the desktop aside and the mobile collapsible below — one
  // set of elements, never rendered twice at once. `isMobile` picks exactly
  // one of the two branches (see the return below), so there is only ever a
  // single "New conversation" link and a single delete button per recent
  // entry in the DOM, keeping AlertDialog accessible names and existing
  // unscoped `getByRole` test queries unambiguous regardless of viewport.
  const newConversationLink = (
    <Link to={`/${app}/chat`} className="font-semibold text-foreground hover:text-primary">
      New conversation
    </Link>
  );
  const recentRows = recent.map((r) => (
    <div key={r.key} className="flex items-center gap-2">
      <Link
        to={`/${app}/chat/${r.key}`}
        className={cn(
          "flex-1 truncate text-muted-foreground hover:text-foreground",
          r.key === key && "text-primary font-medium",
        )}
      >
        {r.firstQuestion}
      </Link>
      <AlertDialog>
        <AlertDialogTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={`delete ${r.firstQuestion}`}
            className="size-6 text-muted-foreground hover:text-destructive"
          >
            <Trash2 data-icon="inline-start" />
          </Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this conversation?</AlertDialogTitle>
            <AlertDialogDescription>
              Remove this conversation from your list?
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className={cn(buttonVariants({ variant: "destructive" }))}
              onClick={() => void remove(r.key)}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  ));

  return (
    <AppLayout app={app}>
      <div className="flex flex-col gap-4 md:flex-row md:gap-6">
        {isMobile ? (
          // Below md: the fixed w-60 column would squeeze the chat panel
          // unusably narrow, so recents move above it as a collapsible
          // drawer (closed by default — a long history shouldn't push the
          // conversation itself below the fold on a small screen).
          <Collapsible>
            <CollapsibleTrigger asChild>
              <Button type="button" variant="outline" className="group w-full justify-between">
                {`Conversations (${recent.length})`}
                <ChevronDown
                  data-icon="inline-end"
                  className="transition-transform group-data-[state=open]:rotate-180"
                />
              </Button>
            </CollapsibleTrigger>
            <CollapsibleContent className="mt-2 flex flex-col gap-2 text-sm">
              {newConversationLink}
              {recentRows}
            </CollapsibleContent>
          </Collapsible>
        ) : (
          <aside className="w-60 shrink-0 flex flex-col gap-2 text-sm">
            {newConversationLink}
            {recentRows}
          </aside>
        )}
        <div className="flex-1 min-w-0">
          <ChatPanel
            key={app}
            app={app}
            askEnabled={ask.enabled}
            askReason={ask.reason}
            sources={ask.sources}
            contextEnabled={ask.contextEnabled}
            sessionKey={key}
            onSession={(k) => {
              navigate(`/${app}/chat/${k}`, { replace: true });
              const local = listRecent(app).find((r) => r.key === k);
              setRecent((prev) => prev.some((r) => r.key === k) ? prev
                : local ? [local, ...prev] : prev);
            }}
          />
        </div>
      </div>
    </AppLayout>
  );
}
