import { useEffect, useState } from "react";
import { Link } from "react-router";
import { getSchedules, type Schedule } from "~/features/events/schedules";

/** Jira → Tickets: one line about the automatic pull (the jira.pull schedule), linking to the Schedules page.
 * Renders nothing on errors. */
export default function JiraPullStatus({ refreshKey }: { refreshKey: string | null }) {
  const [schedules, setSchedules] = useState<Schedule[] | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    getSchedules(controller.signal)
      .then((result) => setSchedules(Array.isArray(result.schedules) ? result.schedules : null))
      .catch(() => {
        if (!controller.signal.aborted) setSchedules(null);
      });
    return () => controller.abort();
  }, [refreshKey]);
  if (!schedules) return null;
  const schedule = schedules.find((item) => item.target_kind === "jira.pull");
  const summary = schedule?.enabled
    ? `Auto pull: ${schedule.description}${schedule.next_run_at ? ` · next ${new Date(schedule.next_run_at).toLocaleString()}` : ""}`
    : "Auto pull is off";
  return (
    <p className="text-sm text-muted-foreground">
      {summary} ·{" "}
      <Link className="underline" to="/schedules">
        Edit in Schedules
      </Link>
    </p>
  );
}
