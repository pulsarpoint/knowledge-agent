import { useState } from "react";
import { Link } from "react-router";
import { draftPath } from "~/features/documents/drafts";
import { runPagePath } from "~/features/servers/discovery";
import { agentRunPath, closeSession, sendMessage, statusLabels, stopSession } from "./api";
import { runPath } from "./runs";
import { useFillViewport } from "~/lib/useFillViewport";
import { useSessionEvents, useSessionNotice } from "./useSessionEvents";
import Markdown from "~/components/Markdown";
import RunWorkspace from "./RunWorkspace";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Skeleton } from "~/components/ui/skeleton";

/** Any run on the run workspace: its turns, messages, Stop and Close; where it was started; and, for a run that writes a
 * document draft, the draft. The page fits the screen; the main pane scrolls on its own. */
export default function SessionView({ sessionId }: { sessionId: string }) {
  const run = useSessionEvents(sessionId);
  const { session, setSession } = run;
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [draftOpen, setDraftOpen] = useState(false);
  const { notice, clear, stopping, sent } = useSessionNotice(session?.status);
  const fill = useFillViewport<HTMLDivElement>();

  async function act(action: () => Promise<void>) {
    setBusy(true);
    run.setError("");
    clear();
    try {
      await action();
    } catch (e) {
      run.setError(e instanceof Error ? e.message : "The request failed");
    } finally {
      setBusy(false);
    }
  }
  const send = () =>
    act(async () => {
      const result = await sendMessage(sessionId, message.trim());
      setMessage("");
      sent(result.working);
    });
  const stop = () =>
    act(async () => {
      await stopSession(sessionId);
      stopping();
    });
  const close = () =>
    act(async () => {
      await closeSession(sessionId);
      setSession((current) => current && { ...current, status: "closed" });
    });

  if (!session)
    return run.error ? (
      <Alert variant="destructive">
        <AlertDescription>{run.error}</AlertDescription>
      </Alert>
    ) : (
      <Skeleton className="h-60" />
    );
  const startedFrom =
    session.purpose === "documents" && session.document_name ? (
      <>the new document {session.document_name}</>
    ) : session.target_document_id ? (
      <Link className="underline" to={agentRunPath(session.target_document_id, session.id)}>
        the document
      </Link>
    ) : session.server_id ? (
      <Link className="underline" to={runPagePath(session.server_id, session.id)}>
        the server
      </Link>
    ) : session.agent_id ? (
      <Link
        className="underline"
        to={`/agents/${encodeURIComponent(session.agent_id)}?view=test&session_id=${encodeURIComponent(session.id)}`}
      >
        the agent
      </Link>
    ) : null;
  const documentDraft = session.document_draft;
  const hasDraft = Boolean(documentDraft) || Boolean(session.draft);
  const closed = session.status === "closed";
  const draftTitle = session.draft_turn ? `Draft · turn ${session.draft_turn}` : "Draft";
  const promotedTo =
    documentDraft?.status === "promoted" || documentDraft?.status === "replaced"
      ? (documentDraft.document_id ?? session.document_id)
      : null;
  // The page fills the screen below the app header and never grows past it, so the message box stays visible.
  return (
    <div ref={fill.ref} style={fill.style} className="flex min-w-0 flex-col">
      <RunWorkspace
        run={run}
        agentName={session.agent_name}
        resources={session.run_resources ? "Per run" : "Fixed"}
        notice={notice}
        heading={
          <>
            <h1 className="break-words text-2xl font-semibold">{session.title}</h1>
            <p className="text-sm text-muted-foreground">
              {statusLabels[session.status]} · {session.model_name}
              {startedFrom && <> · Started from {startedFrom}</>}
            </p>
          </>
        }
        headerActions={
          <Button type="button" variant="outline" size="sm" disabled={busy || closed} onClick={() => void close()}>
            Close session
          </Button>
        }
        sidebar={
          hasDraft ? (
            <section aria-label="Draft" className="flex flex-col gap-2">
              <h2 className="text-sm font-semibold">{draftTitle}</h2>
              <div className="flex flex-wrap gap-2">
                {session.draft && (
                  <Button type="button" variant="outline" size="sm" onClick={() => setDraftOpen(true)}>
                    Preview draft
                  </Button>
                )}
                {documentDraft?.status === "open" ? (
                  <Button asChild size="sm">
                    <Link to={draftPath(documentDraft)}>Open draft</Link>
                  </Button>
                ) : promotedTo ? (
                  <Button asChild size="sm">
                    <Link to={`/documents/${encodeURIComponent(promotedTo)}`}>Open document</Link>
                  </Button>
                ) : null}
              </div>
              {documentDraft?.status === "open" && (
                <p className="text-xs text-muted-foreground">
                  Each finished turn updates this draft until someone saves it. Promote it on its page to make it the
                  live document.
                </p>
              )}
              {documentDraft?.status === "promoted" && (
                <p className="text-xs text-muted-foreground">
                  The document draft was promoted. The agent's next finished turn starts a new draft.
                </p>
              )}
              {documentDraft?.status === "replaced" && (
                <p className="text-xs text-muted-foreground">
                  Replaced by another promoted draft. The agent's next finished turn starts a new draft.
                </p>
              )}
              {documentDraft?.status === "discarded" && (
                <p className="text-xs text-muted-foreground">The document draft was discarded.</p>
              )}
            </section>
          ) : undefined
        }
        answerActions={(turn) => (
          <Button variant="ghost" size="sm" asChild>
            <Link to={runPath(`cc:${session.id}:${turn.turn}`)}>Full record</Link>
          </Button>
        )}
        empty={
          <p className="text-sm text-muted-foreground">The agent's messages, thinking and tool calls appear here.</p>
        }
        composer={{
          value: message,
          onChange: setMessage,
          onSend: () => void send(),
          onStop: () => void stop(),
          busy,
          blockedReason: closed ? "This session is closed. Start a new session to continue." : null,
          label: "Message",
          placeholder: "For example: shorten the API section",
        }}
      />
      {session.draft && (
        <Sheet open={draftOpen} onOpenChange={setDraftOpen}>
          <SheetContent className="w-full overflow-y-auto sm:max-w-3xl">
            <SheetHeader>
              <SheetTitle>{draftTitle}</SheetTitle>
              <SheetDescription>
                The agent's latest complete document. It is kept as a document draft; open the draft to compare it with
                the live version and promote it.
              </SheetDescription>
            </SheetHeader>
            <div className="mt-4">
              <Markdown content={session.draft ?? ""} />
            </div>
          </SheetContent>
        </Sheet>
      )}
    </div>
  );
}
