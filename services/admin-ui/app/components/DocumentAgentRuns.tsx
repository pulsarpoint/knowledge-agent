import { useEffect, useState } from "react";
import { Link } from "react-router";
import { agentRunPath, getSessions, type RunSummary } from "~/lib/sessions";
import { draftPath } from "~/lib/drafts";
import { statusLabels } from "~/lib/sessions";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Skeleton } from "~/components/ui/skeleton";

/** The document page's Agent runs tab: the Ask agent runs on this document, open ones first, with their drafts. */
export default function DocumentAgentRuns({ documentId }: { documentId: string }) {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    getSessions({ documentId }, controller.signal).then(value => { if (!controller.signal.aborted) setRuns(value.sessions); })
      .catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Could not load the agent runs"); });
    return () => controller.abort();
  }, [documentId]);
  if (error) return <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>;
  if (!runs) return <Skeleton className="h-40" />;
  if (runs.length === 0) return <p className="text-sm text-muted-foreground">No agent runs yet. Ask agent starts one.</p>;
  return <ul className="flex flex-col gap-2">{runs.map(run => <li key={run.id} className="flex flex-wrap items-start justify-between gap-2 rounded-lg border p-3">
    <div className="min-w-0"><Link className="break-words font-medium underline" to={agentRunPath(documentId, run.id)}>{run.title}</Link>
      <p className="text-sm text-muted-foreground">{`${statusLabels[run.status]} · ${run.model_name} · ${run.started_by ?? "Someone"} · ${new Date(run.last_activity_at).toLocaleString()}`}</p></div>
    {run.draft_id && <Link className="text-sm underline" to={draftPath({ id: run.draft_id, document_id: documentId })}>Open draft</Link>}
  </li>)}</ul>;
}
