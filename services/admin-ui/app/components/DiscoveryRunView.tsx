import { useCallback, useEffect, useRef, useState } from "react";
import { Link, Navigate } from "react-router";
import { closeSession, sendMessage, stopSession } from "~/lib/sessions";
import { getProposals, type Proposal } from "~/lib/discovery";
import { runPath } from "~/lib/runs";
import { useFillViewport } from "~/lib/useFillViewport";
import { useSessionEvents, useSessionNotice } from "~/lib/useSessionEvents";
import ProposalSheet, { proposalLabel } from "./ProposalSheet";
import RunWorkspace from "./RunWorkspace";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";

const STATUS: Record<Proposal["status"], string> = { pending: "Pending", accepted: "Accepted", rejected: "Rejected" };

/** One discovery run: the agent's transcript (as Generate documents) and, in the sidebar, its proposals, refreshed
 * while the agent works; Review opens one with Accept and Reject. */
export default function DiscoveryRunView({ serverId, runId }: { serverId: string; runId: string }) {
  const run = useSessionEvents(runId);
  const { session, setSession } = run;
  const [proposals, setProposals] = useState<Proposal[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [decision, setDecision] = useState<string | null>(null);
  const [proposalsError, setProposalsError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const { notice: sessionNotice, clear, stopping, sent } = useSessionNotice(session?.status);
  const fill = useFillViewport<HTMLDivElement>();

  // Every load (first, polled, after a decision) shares one request slot: a poll tick is skipped while one is pending, a
  // newer load replaces an older one, and a response that is no longer the latest is ignored.
  const inflight = useRef<AbortController | null>(null);
  const load = useCallback(async (poll = false) => {
    if (poll && inflight.current) return;
    inflight.current?.abort();
    const controller = new AbortController();
    inflight.current = controller;
    try {
      const result = await getProposals(runId, controller.signal);
      if (inflight.current !== controller) return;
      setProposals(result.proposals); setProposalsError("");
    } catch (e) {
      if (controller.signal.aborted || inflight.current !== controller) return;
      setProposalsError(e instanceof Error ? e.message : "Could not load the proposals.");
    } finally { if (inflight.current === controller) inflight.current = null; }
  }, [runId]);
  const status = session?.status;
  useEffect(() => {
    void load();
    const live = status === "starting" || status === "working";
    const timer = live ? setInterval(() => void load(true), 3000) : undefined;
    return () => { if (timer) clearInterval(timer); inflight.current?.abort(); inflight.current = null; };
  }, [load, status]);

  async function act(action: () => Promise<void>) {
    setBusy(true); run.setError(""); clear();
    try { await action(); } catch (e) { run.setError(e instanceof Error ? e.message : "The request failed"); } finally { setBusy(false); }
  }
  if (!session) return run.error ? <Alert variant="destructive"><AlertDescription>{run.error}</AlertDescription></Alert> : <Skeleton className="h-60" />;
  if (session.purpose !== "discovery") return <Navigate replace to={`/documentation/generations/${encodeURIComponent(runId)}`} />;
  const closed = session.status === "closed";
  const current = proposals?.find(item => item.id === open) ?? null;
  const pending = proposals?.filter(item => item.status === "pending").length ?? 0;
  return <div ref={fill.ref} style={fill.style} className="flex min-w-0 flex-col">
    <RunWorkspace run={run} agentName={session.agent_name} resources="Per run" notice={sessionNotice}
      heading={<><Link className="self-start underline" to={`/servers/${encodeURIComponent(serverId)}`}>Server</Link>
        <h1 className="break-words text-2xl font-semibold">{session.title}</h1>
        <p className="text-sm text-muted-foreground">{session.model_name}</p></>}
      headerActions={<Button type="button" variant="outline" size="sm" disabled={busy || closed}
        onClick={() => void act(async () => { await closeSession(runId); setSession(value => value && { ...value, status: "closed" }); })}>Close run</Button>}
      sidebar={<section aria-label="Proposals panel" className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold">Proposals{proposals ? ` · ${pending} pending` : ""}</h2>
        {proposalsError && <p role="alert" className="text-xs text-destructive">{proposalsError}</p>}
        {proposals?.length === 0 && <p className="text-xs text-muted-foreground">Proposals appear here as the agent makes them.</p>}
        <ul aria-label="Proposals" className="flex flex-col gap-2">{proposals?.map(item => <li key={item.id} className="flex flex-col gap-1 rounded border p-2">
          <span className="text-sm">{`${item.number} · ${proposalLabel(item)}`}</span>
          <div className="flex items-center justify-between gap-2"><Badge variant={item.status === "pending" ? "secondary" : "outline"}>{STATUS[item.status]}</Badge>
            <Button size="sm" variant="outline" onClick={() => { setDecision(null); setOpen(item.id); }}>Review</Button></div></li>)}</ul>
      </section>}
      answerActions={turn => <Button variant="ghost" size="sm" asChild><Link to={runPath(`cc:${session.id}:${turn.turn}`)}>Full record</Link></Button>}
      empty={<p className="text-sm text-muted-foreground">The agent's messages, thinking and tool calls appear here.</p>}
      composer={{ value: message, onChange: setMessage, busy, label: "Message",
        onSend: () => void act(async () => { const result = await sendMessage(runId, message.trim()); setMessage(""); sent(result.working); }),
        onStop: () => void act(async () => { await stopSession(runId); stopping(); }),
        blockedReason: closed ? "This run is closed. Start a new run from the server page." : null,
        placeholder: "For example: also look at the cron jobs" }} />
    {current && <ProposalSheet key={`${current.id}:${current.revision}`} proposal={current} message={decision}
      onClose={() => { setOpen(null); setDecision(null); }} onMessage={setDecision} onReload={() => void load()} />}
  </div>;
}
