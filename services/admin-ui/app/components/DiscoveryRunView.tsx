import { useState } from "react";
import { Link, Navigate } from "react-router";
import { closeSession, sendMessage, sessionPath, stopSession } from "~/lib/sessions";
import { runPath } from "~/lib/runs";
import { useFillViewport } from "~/lib/useFillViewport";
import { useSessionEvents, useSessionNotice } from "~/lib/useSessionEvents";
import RunProposals from "./RunProposals";
import RunWorkspace from "./RunWorkspace";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";

/** One discovery run: the agent's transcript (as Generate documents) and, in the sidebar, its proposals, refreshed
 * while the agent works; Review opens one with Accept and Reject. */
export default function DiscoveryRunView({ serverId, runId }: { serverId: string; runId: string }) {
  const run = useSessionEvents(runId);
  const { session, setSession } = run;
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const { notice: sessionNotice, clear, stopping, sent } = useSessionNotice(session?.status);
  const fill = useFillViewport<HTMLDivElement>();

  async function act(action: () => Promise<void>) {
    setBusy(true); run.setError(""); clear();
    try { await action(); } catch (e) { run.setError(e instanceof Error ? e.message : "The request failed"); } finally { setBusy(false); }
  }
  if (!session) return run.error ? <Alert variant="destructive"><AlertDescription>{run.error}</AlertDescription></Alert> : <Skeleton className="h-60" />;
  if (session.purpose !== "discovery") return <Navigate replace to={sessionPath(runId)} />;
  const closed = session.status === "closed";
  return <div ref={fill.ref} style={fill.style} className="flex min-w-0 flex-col">
    <RunWorkspace run={run} agentName={session.agent_name} resources="Per run" notice={sessionNotice}
      heading={<><Link className="self-start underline" to={`/servers/${encodeURIComponent(serverId)}`}>Server</Link>
        <h1 className="break-words text-2xl font-semibold">{session.title}</h1>
        <p className="text-sm text-muted-foreground">{session.model_name}</p></>}
      headerActions={<Button type="button" variant="outline" size="sm" disabled={busy || closed}
        onClick={() => void act(async () => { await closeSession(runId); setSession(value => value && { ...value, status: "closed" }); })}>Close run</Button>}
      sidebar={<RunProposals runId={runId} version={`${session.id}:${session.turns}:${session.status}`} />}
      answerActions={turn => <Button variant="ghost" size="sm" asChild><Link to={runPath(`cc:${session.id}:${turn.turn}`)}>Full record</Link></Button>}
      empty={<p className="text-sm text-muted-foreground">The agent's messages, thinking and tool calls appear here.</p>}
      composer={{ value: message, onChange: setMessage, busy, label: "Message",
        onSend: () => void act(async () => { const result = await sendMessage(runId, message.trim()); setMessage(""); sent(result.working); }),
        onStop: () => void act(async () => { await stopSession(runId); stopping(); }),
        blockedReason: closed ? "This run is closed. Start a new run from the server page." : null,
        placeholder: "For example: also look at the cron jobs" }} />
  </div>;
}
