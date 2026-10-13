import { useCallback, useEffect, useRef, useState } from "react";
import { getProposals, type Proposal } from "~/lib/discovery";
import ProposalSheet, { proposalLabel } from "./ProposalSheet";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";

const STATUS: Record<Proposal["status"], string> = { pending: "Pending", accepted: "Accepted", rejected: "Rejected" };

/** A run's proposals (pending, accepted, rejected); Review opens one with Accept and Reject. Reloads when `version`
 * changes (the run's turns and status) and while the run works (the page passes a changing version, and polling runs
 * every 3 seconds while `version` ends with a live status). */
export default function RunProposals({ runId, version, onChanged }: { runId: string | null; version: string; onChanged?: () => void }) {
  const [proposals, setProposals] = useState<Proposal[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [decision, setDecision] = useState<string | null>(null);
  const [error, setError] = useState("");

  // Every load (first, polled, after a decision) shares one request slot: a poll tick is skipped while one is pending, a
  // newer load replaces an older one, and a response that is no longer the latest is ignored.
  const inflight = useRef<AbortController | null>(null);
  const load = useCallback(async (poll = false) => {
    if (runId === null || (poll && inflight.current)) return;
    inflight.current?.abort();
    const controller = new AbortController();
    inflight.current = controller;
    try {
      const result = await getProposals(runId, controller.signal);
      if (inflight.current !== controller) return;
      setProposals(result.proposals); setError("");
    } catch (e) {
      if (controller.signal.aborted || inflight.current !== controller) return;
      setError(e instanceof Error ? e.message : "Could not load the proposals.");
    } finally { if (inflight.current === controller) inflight.current = null; }
  }, [runId]);
  const live = /:(starting|working)$/.test(version);
  useEffect(() => { setProposals(null); setOpen(null); setDecision(null); }, [runId]);
  useEffect(() => {
    void load();
    const timer = live ? setInterval(() => void load(true), 3000) : undefined;
    return () => { if (timer) clearInterval(timer); inflight.current?.abort(); inflight.current = null; };
  }, [load, live, version]);

  const current = proposals?.find(item => item.id === open) ?? null;
  const pending = proposals?.filter(item => item.status === "pending").length ?? 0;
  return <section aria-label="Proposals panel" className="flex flex-col gap-2">
    <h2 className="text-sm font-semibold">Proposals{proposals ? ` · ${pending} pending` : ""}</h2>
    {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    {(runId === null || proposals?.length === 0) && <p className="text-xs text-muted-foreground">Proposals appear here as the agent makes them.</p>}
    <ul aria-label="Proposals" className="flex flex-col gap-2">{proposals?.map(item => <li key={item.id} className="flex flex-col gap-1 rounded border p-2">
      <span className="text-sm">{`${item.number} · ${proposalLabel(item)}`}</span>
      <div className="flex items-center justify-between gap-2"><Badge variant={item.status === "pending" ? "secondary" : "outline"}>{STATUS[item.status]}</Badge>
        <Button size="sm" variant="outline" onClick={() => { setDecision(null); setOpen(item.id); }}>Review</Button></div></li>)}</ul>
    {current && <ProposalSheet key={`${current.id}:${current.revision}`} proposal={current} message={decision}
      onClose={() => { setOpen(null); setDecision(null); }} onMessage={setDecision}
      onReload={() => { void load(); onChanged?.(); }} />}
  </section>;
}
