import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import type { Proposal } from "./discovery";
import { getResourceProposals, resourceChatPath, type ProposalRun, type ResourceType } from "~/features/sessions/api";
import ProposalSheet, { AcceptedDocument, proposalLabel } from "~/features/sessions/ProposalSheet";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";

type Status = Proposal["status"];
const STATUS: Record<Status, string> = { pending: "Pending", accepted: "Accepted", rejected: "Rejected" };
const FILTERS: { value: Status | null; label: string }[] = [
  { value: null, label: "All" },
  { value: "pending", label: "Pending" },
  { value: "accepted", label: "Accepted" },
  { value: "rejected", label: "Rejected" },
];

/** A server's, service's or environment's proposals: every proposal made in the runs tagged with it, grouped by run
 * (newest first; its title, when and who started it, a link to open it as a chat), with a status filter; Review opens
 * one with Accept and Reject, and an accepted document links to its draft, then to the live document. */
export default function ProposalsPage({ resource }: { resource: { type: ResourceType; id: string } }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [runs, setRuns] = useState<ProposalRun[] | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [decision, setDecision] = useState<string | null>(null);
  const inflight = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    inflight.current?.abort();
    const controller = new AbortController();
    inflight.current = controller;
    try {
      const result = await getResourceProposals(resource, status ?? undefined, controller.signal);
      if (inflight.current !== controller) return;
      setRuns(result.runs);
      setError("");
    } catch (e) {
      if (controller.signal.aborted || inflight.current !== controller) return;
      setError(e instanceof Error ? e.message : "Could not load the proposals.");
    }
  }, [resource.type, resource.id, status]);
  useEffect(() => {
    void load();
    return () => {
      inflight.current?.abort();
      inflight.current = null;
    };
  }, [load]);

  const current = runs?.flatMap((run) => run.proposals).find((item) => item.id === open) ?? null;
  return (
    <div className="flex flex-col gap-4">
      <div role="group" aria-label="Status" className="flex flex-wrap gap-2">
        {FILTERS.map((item) => (
          <Button
            key={item.label}
            type="button"
            size="sm"
            variant={status === item.value ? "default" : "outline"}
            aria-pressed={status === item.value}
            onClick={() => setStatus(item.value)}
          >
            {item.label}
          </Button>
        ))}
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>
            {error}{" "}
            <Button type="button" variant="link" className="h-auto p-0" onClick={() => void load()}>
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {runs?.length === 0 && (
        <p className="text-sm text-muted-foreground">
          {status === null
            ? `No proposals yet. Start a run with Propose… on the ${resource.type}'s page.`
            : `No ${status} proposals.`}
        </p>
      )}
      {runs?.map((run) => (
        <section key={run.session_id} aria-label={run.title} className="flex flex-col gap-2 rounded-lg border p-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="min-w-0 break-words font-semibold">{run.title}</h2>
            <Link className="text-sm underline" to={resourceChatPath(resource, run.session_id)}>
              Open chat
            </Link>
          </div>
          <p className="text-xs text-muted-foreground">
            {`Started ${new Date(run.started_at).toLocaleString()}${run.started_by ? ` by ${run.started_by}` : ""}`}
          </p>
          <ul className="flex flex-col divide-y">
            {run.proposals.map((item) => (
              <li key={item.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span className="min-w-0 break-words text-sm">{`${item.number} · ${proposalLabel(item)}`}</span>
                <span className="flex flex-wrap items-center gap-2 text-sm">
                  <Badge variant={item.status === "pending" ? "secondary" : "outline"}>{STATUS[item.status]}</Badge>
                  {item.status === "accepted" && item.result_id && <AcceptedDocument proposal={item} />}
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      setDecision(null);
                      setOpen(item.id);
                    }}
                  >
                    Review
                  </Button>
                </span>
              </li>
            ))}
          </ul>
        </section>
      ))}
      {current && (
        <ProposalSheet
          key={`${current.id}:${current.revision}`}
          proposal={current}
          resourceType={resource.type}
          message={decision}
          onClose={() => {
            setOpen(null);
            setDecision(null);
          }}
          onMessage={setDecision}
          onReload={() => void load()}
        />
      )}
    </div>
  );
}
