import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { AtSign } from "lucide-react";
import { newDocumentResource } from "./api";
import { getSessionDrafts, mentionable, mentionText, sessionDraftPath, stateLabel, type SessionDraft } from "./drafts";
import { resourceTag, type ChatScope } from "~/features/sessions/api";
import { useAgentSettings } from "~/features/sessions/agentSettings";
import ChatColumn from "~/features/sessions/ChatColumn";
import { useWidePage } from "~/lib/widePage";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";

/** Documents → New → Write with an agent: a chat that writes documents, with no title or slug up front. On a resource's
 * New document (?resource=…) the chat is on that resource; without one the person picks resources in the chat's
 * settings (new_document needs a resource of the run). Left: Documents in this chat; right: the chat (ChatColumn:
 * Conversations lists the earlier Write with an agent conversations, of the resource when there is one). */
export default function NewDocumentAgent() {
  const [params] = useSearchParams();
  const found = newDocumentResource(params);
  const resource = useMemo(() => found, [found?.type, found?.id]);
  const scope: ChatScope = useMemo(() => ({ kind: "new-document", ...(resource ? { resource } : {}) }), [resource]);
  const settings = useAgentSettings(scope);
  useWidePage(true);
  const [shown, setShown] = useState<{ id: string | null; version: string }>({ id: null, version: "" });
  const [writes, setWrites] = useState(0);
  const [mention, setMention] = useState<{ text: string; key: number } | null>(null);
  const name = resource
    ? (settings.options?.resources[`${resource.type}s`].find((item) => item.id === resource.id)?.name ?? "it")
    : null;
  return (
    <div
      data-testid="write-layout"
      className="grid min-w-0 gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)] lg:items-start"
    >
      <ChatDrafts
        sessionId={shown.id}
        version={`${shown.version}:${writes}`}
        onMention={(draft) => setMention((current) => ({ text: mentionText(draft), key: (current?.key ?? 0) + 1 }))}
      />
      <div className="min-w-0 lg:sticky lg:top-4">
        <ChatColumn
          scope={scope}
          settings={settings}
          filter={{ purpose: "documents", ...(resource ? { tag: resourceTag(resource) } : {}) }}
          description={
            name
              ? `Every Write with an agent chat about ${name}, newest first.`
              : "Every Write with an agent chat, newest first."
          }
          onSessionChange={(id, version) => setShown({ id, version })}
          mention={mention}
          onDocumentWrite={(_, count) => setWrites(count)}
        />
      </div>
    </div>
  );
}

/** Documents in this chat: every draft the shown conversation created or edited, loaded when it is shown (cleared at
 * once when another one is) and again when `version` changes (its turns, its status, or a document write's result in its events). Each title opens in a
 * new tab; New or Edit of <document>; its state; Mention puts its reference into the message box. */
function ChatDrafts({
  sessionId,
  version,
  onMention,
}: {
  sessionId: string | null;
  version: string;
  onMention: (draft: SessionDraft) => void;
}) {
  const [drafts, setDrafts] = useState<SessionDraft[] | null>(null);
  const [error, setError] = useState("");
  // Another conversation: its documents load from nothing (never another conversation's rows with their Mention).
  const [listed, setListed] = useState(sessionId);
  if (listed !== sessionId) {
    setListed(sessionId);
    setDrafts(null);
    setError("");
  }
  useEffect(() => {
    if (sessionId === null) {
      setDrafts([]);
      setError("");
      return;
    }
    const controller = new AbortController();
    getSessionDrafts(sessionId, controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        setDrafts(value.drafts);
        setError("");
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Could not load the documents.");
      });
    return () => controller.abort();
  }, [sessionId, version]);
  return (
    <section aria-label="Documents in this chat" className="flex min-w-0 flex-col gap-3 rounded-lg border p-4">
      <h2 className="font-semibold">Documents in this chat</h2>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {drafts === null ? (
        <Skeleton className="h-16 w-full" />
      ) : drafts.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {sessionId === null
            ? "The documents the agent writes in this chat show here."
            : "The agent has not written a document in this chat yet."}
        </p>
      ) : (
        <ul aria-label="Documents" className="flex flex-col divide-y">
          {drafts.map((draft) => {
            const title = draft.title ?? "Untitled";
            const path = sessionDraftPath(draft);
            return (
              <li key={draft.id} className="flex flex-col gap-1 py-2">
                <div className="flex items-start justify-between gap-2">
                  {path ? (
                    <a
                      href={path}
                      target="_blank"
                      rel="noopener"
                      className="font-medium underline-offset-4 [overflow-wrap:anywhere] hover:underline"
                    >
                      {title}
                    </a>
                  ) : (
                    <span className="font-medium text-muted-foreground [overflow-wrap:anywhere]">{title}</span>
                  )}
                  {mentionable(draft) && (
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      aria-label={`Mention ${title}`}
                      onClick={() => onMention(draft)}
                    >
                      <AtSign data-icon="inline-start" />
                      Mention
                    </Button>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  {draft.draft_of ? (
                    <span>
                      Edit of{" "}
                      <a
                        href={`/documents/${encodeURIComponent(draft.draft_of)}`}
                        target="_blank"
                        rel="noopener"
                        className="underline"
                      >
                        {draft.draft_of_title ?? "a document"}
                      </a>
                    </span>
                  ) : (
                    <Badge variant="secondary">New</Badge>
                  )}
                  <Badge variant="outline">{stateLabel(draft.state)}</Badge>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
