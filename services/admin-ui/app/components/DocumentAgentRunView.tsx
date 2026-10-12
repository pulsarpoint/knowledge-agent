import { useEffect, useState } from "react";
import { Link, Navigate } from "react-router";
import { getDocument } from "~/lib/documents";
import { getDraft, draftPath, type DraftDetail } from "~/lib/drafts";
import { closeSession, sendMessage, stopSession } from "~/lib/generations";
import { runPath } from "~/lib/runs";
import { savedIn } from "~/lib/agentSessions";
import { useFillViewport } from "~/lib/useFillViewport";
import { useSessionEvents, useSessionNotice } from "~/lib/useSessionEvents";
import RunWorkspace from "./RunWorkspace";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Skeleton } from "~/components/ui/skeleton";

export { savedIn };

/** One Ask agent run: the transcript (as Generate documents), the document it revises, its draft in the sidebar, Show
 * config (the saved JSON) and Close; a finished turn without a successful save_doc or edit_doc says so. */
export default function DocumentAgentRunView({ documentId, runId }: { documentId: string; runId: string }) {
  const run = useSessionEvents(runId);
  const { session, setSession } = run;
  const [title, setTitle] = useState("Document");
  const [draft, setDraft] = useState<DraftDetail | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [showConfig, setShowConfig] = useState(false);
  const { notice: sessionNotice, clear, stopping, sent } = useSessionNotice(session?.status);
  const fill = useFillViewport<HTMLDivElement>();

  useEffect(() => {
    const controller = new AbortController();
    getDocument(documentId, controller.signal).then(value => { if (!controller.signal.aborted) setTitle(value.title); }).catch(() => undefined);
    return () => controller.abort();
  }, [documentId]);

  const draftId = session?.document_draft?.id ?? null;
  const draftStatus = session?.document_draft?.status;
  const status = session?.status;
  useEffect(() => {
    if (!draftId) { setDraft(null); return; }
    const controller = new AbortController();
    getDraft(draftId, controller.signal).then(value => { if (!controller.signal.aborted) setDraft(value); }).catch(() => undefined);
    return () => controller.abort();
  }, [draftId, draftStatus, status === "waiting"]);

  async function act(action: () => Promise<void>) {
    setBusy(true); run.setError(""); clear();
    try { await action(); } catch (e) { run.setError(e instanceof Error ? e.message : "The request failed"); } finally { setBusy(false); }
  }
  if (!session) return run.error ? <Alert variant="destructive"><AlertDescription>{run.error}</AlertDescription></Alert> : <Skeleton className="h-60" />;
  if (session.purpose !== "revision") return <Navigate replace to={`/documentation/generations/${encodeURIComponent(runId)}`} />;
  const closed = session.status === "closed";
  const linked = session.document_draft;
  return <div ref={fill.ref} style={fill.style} className="flex min-w-0 flex-col">
    <RunWorkspace run={run} agentName={session.agent_name} resources="Per run" notice={sessionNotice}
      heading={<><Link to={`/documents/${encodeURIComponent(documentId)}`} className="self-start underline">{title}</Link>
        <h1 className="break-words text-2xl font-semibold">{session.title}</h1>
        <p className="text-sm text-muted-foreground">{session.model_name}</p></>}
      headerActions={<>
        <Button type="button" variant="outline" size="sm" onClick={() => setShowConfig(true)}>Show config</Button>
        <Button type="button" variant="outline" size="sm" disabled={busy || closed}
          onClick={() => void act(async () => { await closeSession(runId); setSession(value => value && { ...value, status: "closed" }); })}>Close run</Button></>}
      sidebar={<section aria-label="Draft panel" className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold">Draft</h2>
        {!linked && <p className="text-xs text-muted-foreground">The agent's draft appears here when it saves the document.</p>}
        {linked?.status === "open" && <div className="flex flex-col gap-2">
          <p className="flex flex-wrap items-center gap-2 text-sm">{draft ? `based on version ${draft.base_version ?? "?"}` : "Loading the draft…"}
            {draft?.stale && <Badge variant="destructive">stale</Badge>}</p>
          <Button size="sm" variant="outline" asChild><Link to={draftPath({ id: linked.id, document_id: linked.document_id ?? documentId })}>Open draft</Link></Button></div>}
        {linked?.status === "promoted" && <p className="text-sm">Promoted. <Link className="underline" to={`/documents/${encodeURIComponent(documentId)}`}>Open document</Link></p>}
        {linked?.status === "discarded" && <p className="text-sm">Discarded. The next save starts a new draft.</p>}
      </section>}
      answerActions={turn => <>{!savedIn(turn) && <span className="text-xs text-muted-foreground">No draft saved in this turn</span>}
        <Button variant="ghost" size="sm" asChild><Link to={runPath(`cc:${session.id}:${turn.turn}`)}>Full record</Link></Button></>}
      empty={<p className="text-sm text-muted-foreground">The agent's messages, thinking and tool calls appear here.</p>}
      composer={{ value: message, onChange: setMessage, busy, label: "Message",
        onSend: () => void act(async () => { const result = await sendMessage(runId, message.trim()); setMessage(""); sent(result.working); }),
        onStop: () => void act(async () => { await stopSession(runId); stopping(); }),
        blockedReason: closed ? "This run is closed. Start a new one with Ask agent on the document." : null,
        placeholder: "For example: also remove the old cron section" }} />
    <Sheet open={showConfig} onOpenChange={setShowConfig}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
        <SheetHeader><SheetTitle>Saved configuration</SheetTitle><SheetDescription>The configuration this run started with.</SheetDescription></SheetHeader>
        <pre aria-label="Run configuration" className="overflow-x-auto whitespace-pre-wrap break-words px-4 pb-4 text-xs">{JSON.stringify(session.config, null, 2)}</pre>
      </SheetContent>
    </Sheet>
  </div>;
}
