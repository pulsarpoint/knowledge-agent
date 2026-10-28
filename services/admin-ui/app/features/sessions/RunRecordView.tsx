import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { MessageSquare, Sparkles } from "lucide-react";
import { getRunRecord, runPath, toolUsageLine, type RunEvent, type RunRecord } from "./runs";
import { getSession, isWorking, type SessionDetail } from "./api";
import { reviewReason, sessionOfRunKey } from "./reviews";
import ReviewSheet, { useReviewParam } from "./ReviewSheet";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";

const text = (value: unknown) => (typeof value === "string" ? value : JSON.stringify(value, null, 2));
const LABELS: Record<string, string> = {
  instructions: "Instructions",
  input: "Input",
  reasoning: "Reasoning",
  message: "Message",
  tool_call: "Tool call",
  tool_result: "Tool result",
  usage: "Usage",
  error: "Error",
};

function Block({ children }: { children: string }) {
  return (
    <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted p-3 text-xs">
      {children}
    </pre>
  );
}

function Event({ event }: { event: RunEvent }) {
  const p = event.payload;
  const who = typeof p.agent === "string" ? p.agent : event.agent_id;
  const head = (
    <span className="text-sm font-medium">
      {LABELS[event.kind] ?? event.kind}
      {event.kind.startsWith("tool") && p.name ? ` · ${p.name}` : ""}{" "}
      <span className="font-normal text-muted-foreground">· {who}</span>
    </span>
  );
  if (event.kind === "instructions" || event.kind === "input")
    return (
      <details>
        <summary className="cursor-pointer">{head}</summary>
        <Block>{event.kind === "input" ? text({ task: p.task, history: p.history }) : text(p.text)}</Block>
      </details>
    );
  if (event.kind === "reasoning")
    return (
      <details>
        <summary className="cursor-pointer">{head}</summary>
        {p.available === false ? (
          <p className="text-sm text-muted-foreground">Reasoning not returned by the provider.</p>
        ) : (
          <Block>{text(p.text)}</Block>
        )}
      </details>
    );
  if (event.kind === "tool_call")
    return (
      <div>
        {head}
        <Block>{text(p.arguments)}</Block>
      </div>
    );
  if (event.kind === "tool_result")
    return (
      <div>
        {head}
        <Block>{text(p.output)}</Block>
      </div>
    );
  if (event.kind === "message")
    return (
      <div>
        {head}
        <Block>{text(p.text)}</Block>
      </div>
    );
  if (event.kind === "usage")
    return (
      <p className="text-xs text-muted-foreground">
        {who}: {String(p.input_tokens)} in · {String(p.output_tokens)} out · {String(p.total_tokens)} total
      </p>
    );
  if (event.kind === "error")
    return (
      <Alert variant="destructive">
        <AlertDescription>
          {String(p.type)}: {String(p.message)}
        </AlertDescription>
      </Alert>
    );
  return null;
}

const REVIEW_POLL_MS = 5000; // how often a working session's status is loaded again

/** Improve agent on a run of a session (`cc:` keys; none for a run outside a session): the review chat (ReviewSheet)
 * on this run's key, open while the URL has `?review=`; disabled with the reason while the session works (its status is loaded again every 5 s until it
 * finishes) or when it is not a saved agent's chat or event run (the API refuses those with the same texts). */
function ImproveAgent({ runKey }: { runKey: string }) {
  const sessionId = sessionOfRunKey(runKey);
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [failed, setFailed] = useState(false);
  const setReview = useReviewParam()[1];
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!isWorking(session?.status)) return;
    const timer = setTimeout(() => setTick((n) => n + 1), REVIEW_POLL_MS);
    return () => clearTimeout(timer);
  }, [session]);
  useEffect(() => {
    if (sessionId === null) return;
    const controller = new AbortController();
    getSession(sessionId, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setSession(value);
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      });
    return () => controller.abort();
  }, [sessionId, tick]);
  if (sessionId === null) return null;
  const loading = sessionId !== null && session === null && !failed;
  const reason = loading ? null : failed ? "Could not load the run's session." : reviewReason(session);
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={loading || reason !== null}
        title={reason ?? undefined}
        onClick={() => setReview("new")}
      >
        <Sparkles data-icon="inline-start" />
        Improve agent
      </Button>
      {reason && <span className="text-xs text-muted-foreground">{reason}</span>}
      {session && reason === null && <ReviewSheet runKey={runKey} agentName={session.agent_name} />}
    </div>
  );
}

/** A review session's run: Continue review opens the reviewed run's record with this review in its sheet (a review
 * is never reviewed itself, so it has no Improve agent). */
function ContinueReview({ runKey, reviewOf }: { runKey: string; reviewOf: string }) {
  const review = sessionOfRunKey(runKey);
  if (review === null) return null;
  return (
    <Button type="button" size="sm" variant="outline" asChild>
      <Link to={`${runPath(reviewOf)}?${new URLSearchParams({ review })}`}>
        <MessageSquare data-icon="inline-start" />
        Continue review
      </Link>
    </Button>
  );
}

export default function RunRecordView({ runKey }: { runKey: string }) {
  const call = useSearchParams()[0].get("call") ?? undefined;
  const [record, setRecord] = useState<RunRecord | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    getRunRecord(runKey, controller.signal, call)
      .then((data) => {
        if (!controller.signal.aborted) setRecord(data);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Could not load the run record");
      });
    return () => controller.abort();
  }, [runKey, call]);
  if (error)
    return (
      <Alert variant="destructive">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  if (!record) return <Skeleton className="h-64 w-full" />;
  const start = record.events.find((e) => e.kind === "start")?.payload ?? {};
  const end = record.events.filter((e) => e.kind === "end").at(-1)?.payload;
  const tokens = record.events
    .filter((e) => e.kind === "usage")
    .reduce((sum, e) => sum + Number(e.payload.total_tokens ?? 0), 0);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <h2 className="min-w-0 break-words text-xl font-semibold">{String(start.title ?? record.run_key)}</h2>
          {record.purpose === "review" ? (
            record.review_of && <ContinueReview runKey={record.run_key} reviewOf={record.review_of} />
          ) : (
            <ImproveAgent runKey={record.run_key} />
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          <Badge variant="secondary">{String(start.kind ?? "run")}</Badge>
          <span>{String(start.agent ?? record.agent_id)}</span>
          <span>· {record.user ?? "system"}</span>
          <Badge>{String(end?.status ?? "running")}</Badge>
          <span>{tokens.toLocaleString()} tokens</span>
          {record.tool_counts && (
            <>
              <span>·</span>
              <span aria-label="Tools used">{toolUsageLine(record.tool_counts)}</span>
            </>
          )}
        </div>
      </div>
      <ol className="flex flex-col gap-3">
        {record.events
          .filter((e) => !["start", "end"].includes(e.kind))
          .map((e) => (
            <li key={e.seq}>
              <Event event={e} />
            </li>
          ))}
      </ol>
    </div>
  );
}
