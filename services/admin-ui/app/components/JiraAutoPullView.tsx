import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { CalendarClock, Play } from "lucide-react";
import { ApiError } from "../lib/api";
import { DEFAULT_CRON, browserTimeZone, getAutoPull, runSchedule, saveAutoPull, type AutoPull } from "../lib/schedules";
import { ScheduleStatus } from "./SchedulesView";
import TimetableFields, { type Timetable } from "./TimetableFields";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "~/components/ui/card";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "~/components/ui/empty";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";
import { Spinner } from "~/components/ui/spinner";
import { Switch } from "~/components/ui/switch";

const when = (value: string) => new Date(value).toLocaleString();
const text = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback;

export default function JiraAutoPullView() {
  const [state, setState] = useState<AutoPull | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [jql, setJql] = useState("");
  const [limit, setLimit] = useState("25");
  const [timetable, setTimetable] = useState<Timetable>({ cron: DEFAULT_CRON, timezone: browserTimeZone() });
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function show(next: AutoPull) {
    setState(next);
    setEnabled(next.schedule?.enabled ?? false);
    setJql(next.jql ?? ""); setLimit(String(next.limit ?? 25));
    if (next.schedule) setTimetable({ cron: next.schedule.cron, timezone: next.schedule.timezone });
  }
  useEffect(() => {
    const controller = new AbortController();
    getAutoPull(controller.signal).then(show).catch(reason => { if (!controller.signal.aborted) setError(text(reason, "Could not load the auto pull settings.")); });
    return () => controller.abort();
  }, []);

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError(null); setNotice(null);
    try { show(await saveAutoPull({ enabled, cron: timetable.cron.trim(), timezone: timetable.timezone, jql: jql.trim(), limit: Number(limit) })); setNotice("Auto pull saved."); }
    catch (reason) { setError(text(reason, "Could not save the auto pull settings.")); }
    finally { setBusy(false); }
  }
  async function runNow() {
    if (!state?.schedule) return;
    setBusy(true); setError(null); setNotice(null);
    try { const { schedule } = await runSchedule(state.schedule.id); setState({ ...state, schedule }); setNotice("The pull starts within a few seconds."); }
    catch (reason) { setError(text(reason, "Could not start the pull.")); }
    finally { setBusy(false); }
  }

  if (state === null) return error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : <Skeleton className="h-64 w-full" />;
  if (!state.connected) return <Empty className="border"><EmptyHeader><EmptyMedia variant="icon"><CalendarClock /></EmptyMedia><EmptyTitle>Connect Jira first</EmptyTitle>
    <EmptyDescription>Save your Jira server, username and token in Jira → Settings, then set up the automatic pull here.</EmptyDescription></EmptyHeader>
    <EmptyContent><Button asChild><Link to="/jira/settings">Open Jira settings</Link></Button></EmptyContent></Empty>;
  const schedule = state.schedule;
  return <div className="flex flex-col gap-6">
    {schedule && <Card><CardHeader><CardTitle>Status</CardTitle><CardDescription>{schedule.description} · {schedule.timezone}</CardDescription></CardHeader>
      <CardContent className="flex flex-wrap items-center gap-3 text-sm">
        <ScheduleStatus schedule={schedule} />
        <span className="text-muted-foreground">{schedule.last_finished_at ? `Last pull ${when(schedule.last_finished_at)}${schedule.last_message ? ` · ${schedule.last_message}` : ""}` : "Not pulled automatically yet"}</span>
        <span className="text-muted-foreground">{schedule.next_run_at ? `Next pull ${when(schedule.next_run_at)}` : "Paused"}</span>
        <Button size="sm" variant="outline" disabled={busy || schedule.running} onClick={runNow}><Play data-icon="inline-start" />Run now</Button>
        <Link className="underline" to="/schedules">All schedules</Link>
      </CardContent></Card>}
    <form onSubmit={save} className="flex max-w-2xl flex-col gap-4">
      <FieldGroup>
        <Field orientation="horizontal"><Switch id="auto-pull-enabled" checked={enabled} disabled={busy} onCheckedChange={setEnabled} /><FieldLabel htmlFor="auto-pull-enabled">Pull tickets automatically</FieldLabel></Field>
        <Field><FieldLabel htmlFor="auto-pull-jql">Jira query (JQL)</FieldLabel>
          <Input id="auto-pull-jql" required maxLength={4000} value={jql} disabled={busy} onChange={event => setJql(event.target.value)} />
          <FieldDescription>Separate from the query on Jira → Tickets. For example <code>project = DEMO ORDER BY updated DESC</code>.</FieldDescription></Field>
        <Field className="w-40"><FieldLabel htmlFor="auto-pull-limit">Number of tickets</FieldLabel>
          <Input id="auto-pull-limit" type="number" required min={1} max={200} step={1} value={limit} disabled={busy} onChange={event => setLimit(event.target.value)} /></Field>
        <TimetableFields key={schedule?.id ?? "new"} id="auto-pull" value={timetable} onChange={setTimetable} onProblem={setProblem} />
      </FieldGroup>
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      {notice && <p role="status" className="text-sm text-muted-foreground">{notice}</p>}
      <div><Button type="submit" disabled={busy || problem !== null || !jql.trim() || !timetable.cron.trim()}>{busy && <Spinner data-icon="inline-start" />}Save</Button></div>
    </form>
  </div>;
}
