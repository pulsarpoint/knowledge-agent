import { useEffect, useState } from "react";
import { Link } from "react-router";
import { getAutoPull, type AutoPull } from "~/lib/schedules";

/** Jira → Tickets: one line about the automatic pull, linking to its settings. Renders nothing on errors. */
export default function JiraAutoPullStatus({ refreshKey }: { refreshKey: string | null }) {
  const [state, setState] = useState<AutoPull | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    getAutoPull(controller.signal).then(setState).catch(() => { if (!controller.signal.aborted) setState(null); });
    return () => controller.abort();
  }, [refreshKey]);
  if (!state || state.connected !== true) return null;
  const schedule = state.schedule;
  const summary = schedule?.enabled
    ? `Auto pull: ${schedule.description}${schedule.next_run_at ? ` · next ${new Date(schedule.next_run_at).toLocaleString()}` : ""}`
    : "Auto pull is off";
  return <p className="text-sm text-muted-foreground">{summary} · <Link className="underline" to="/jira/auto-pull">Auto pull settings</Link></p>;
}
