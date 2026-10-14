import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { CalendarClock, History, Pencil, Play, Plus, Trash2 } from "lucide-react";
import { ApiError } from "~/lib/api";
import { STATUS_LABELS, TARGETS, deleteSchedule, getScheduleRuns, getSchedules, runSchedule, saveSchedule, type Schedule, type ScheduleRun } from "./schedules";
import NewScheduleSheet from "./NewScheduleSheet";
import ScheduleEditor from "./ScheduleEditor";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "~/components/ui/alert-dialog";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "~/components/ui/empty";
import { Skeleton } from "~/components/ui/skeleton";
import { Switch } from "~/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

const REFRESH_MS = 10_000;
const when = (value: string | null) => value ? new Date(value).toLocaleString() : "—";
const text = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback;

export function ScheduleStatus({ schedule }: { schedule: Schedule }) {
  if (schedule.running) return <Badge variant="secondary">Running</Badge>;
  if (!schedule.last_status) return <span className="text-sm text-muted-foreground">Never run</span>;
  const variant = schedule.last_status === "failed" ? "destructive" : schedule.last_status === "skipped" ? "outline" : "secondary";
  return <Badge variant={variant}>{STATUS_LABELS[schedule.last_status]}</Badge>;
}

export default function SchedulesView() {
  const [schedules, setSchedules] = useState<Schedule[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<Schedule | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<Schedule | null>(null);
  const [removing, setRemoving] = useState(false);
  const deletingName = useRef("");
  const [history, setHistory] = useState<{ id: string; runs: ScheduleRun[] | null } | null>(null);

  const changes = useRef(0);

  const load = useCallback((signal?: AbortSignal) => {
    const seen = changes.current;
    return getSchedules(signal)
    .then(result => { if (changes.current !== seen) return; setSchedules(result.schedules); setError(null); })
    .catch(reason => { if (!signal?.aborted) setError(text(reason, "Could not load schedules.")); });
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    const timer = setInterval(() => load(), REFRESH_MS);
    return () => { controller.abort(); clearInterval(timer); };
  }, [load]);

  /** Any local change invalidates refreshes that started earlier, so they cannot bring back older rows. */
  const replace = (changed: Schedule) => { changes.current += 1; setSchedules(current => current?.map(item => item.id === changed.id ? changed : item) ?? null); };
  async function change(schedule: Schedule, action: () => Promise<{ schedule: Schedule }>) {
    setBusy(schedule.id); setError(null);
    try { replace((await action()).schedule); } catch (reason) { setError(text(reason, "Could not change the schedule.")); } finally { setBusy(null); }
  }
  async function remove() {
    const doomed = deleting;
    if (!doomed || removing) return;
    setRemoving(true); setError(null);
    try {
      await deleteSchedule(doomed.id);
      changes.current += 1;
      setSchedules(current => current?.filter(item => item.id !== doomed.id) ?? null);
      setHistory(current => current?.id === doomed.id ? null : current);
    } catch (reason) { setError(text(reason, "Could not delete the schedule.")); }
    finally { setRemoving(false); setDeleting(null); }
  }
  async function toggleHistory(schedule: Schedule) {
    if (history?.id === schedule.id) { setHistory(null); return; }
    setHistory({ id: schedule.id, runs: null });
    try {
      const { runs } = await getScheduleRuns(schedule.id);
      setHistory(current => current?.id === schedule.id ? { id: schedule.id, runs } : current);
    } catch (reason) { setHistory(null); setError(text(reason, "Could not load the run history.")); }
  }

  return <div className="flex flex-col gap-4">
    <div className="flex justify-end"><Button onClick={() => setCreating(true)}><Plus data-icon="inline-start" />New schedule</Button></div>
    {error && <Alert variant="destructive"><AlertTitle>Schedules</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
    {schedules === null && !error && <Skeleton className="h-32 w-full" />}
    {schedules?.length === 0 && <Empty className="border"><EmptyHeader><EmptyMedia variant="icon"><CalendarClock /></EmptyMedia><EmptyTitle>Schedules</EmptyTitle>
      <EmptyDescription>No schedules yet. Create one with New schedule, or set up Jira → Auto pull.</EmptyDescription></EmptyHeader></Empty>}
    {schedules && schedules.length > 0 && <Table>
      <TableHeader><TableRow><TableHead>On</TableHead><TableHead>Name</TableHead><TableHead>Runs</TableHead><TableHead>When</TableHead>
        <TableHead>Next run</TableHead><TableHead>Last run</TableHead><TableHead><span className="sr-only">Actions</span></TableHead></TableRow></TableHeader>
      <TableBody>{schedules.map(schedule => <Fragment key={schedule.id}>
        <TableRow>
          <TableCell><Switch aria-label={`Enabled: ${schedule.name}`} checked={schedule.enabled} disabled={busy === schedule.id}
            onCheckedChange={enabled => change(schedule, () => saveSchedule(schedule.id, { cron: schedule.cron, timezone: schedule.timezone, enabled, revision: schedule.revision }))} /></TableCell>
          <TableCell className="font-medium">{schedule.name}</TableCell>
          <TableCell>{TARGETS[schedule.target_kind] ?? schedule.target_kind}</TableCell>
          <TableCell><div>{schedule.description}</div><div className="text-xs text-muted-foreground">{schedule.timezone}</div></TableCell>
          <TableCell>{schedule.next_run_at ? when(schedule.next_run_at) : "Paused"}</TableCell>
          <TableCell><div className="flex flex-col items-start gap-1"><ScheduleStatus schedule={schedule} />
            {schedule.last_message && <span className="text-xs text-muted-foreground">{schedule.last_message}</span>}</div></TableCell>
          <TableCell><div className="flex justify-end gap-1">
            <Button size="sm" variant="outline" disabled={busy === schedule.id || schedule.running} onClick={() => change(schedule, () => runSchedule(schedule.id))}><Play data-icon="inline-start" />Run now</Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(schedule)}><Pencil data-icon="inline-start" />Edit</Button>
            <Button size="sm" variant="ghost" aria-expanded={history?.id === schedule.id} onClick={() => toggleHistory(schedule)}><History data-icon="inline-start" />History</Button>
            <Button size="sm" variant="ghost" onClick={() => { deletingName.current = schedule.name; setDeleting(schedule); }}><Trash2 data-icon="inline-start" />Delete</Button>
          </div></TableCell>
        </TableRow>
        {history?.id === schedule.id && <TableRow><TableCell colSpan={7}>
          {history.runs === null ? <Skeleton className="h-8 w-full" /> : history.runs.length === 0 ? <p className="text-sm text-muted-foreground">No runs yet.</p>
            : <ul className="flex flex-col gap-1 text-sm">{history.runs.map(run => <li key={run.id}>
              {when(run.started_at)} · {run.trigger === "manual" ? "Run now" : "Scheduled"} · {STATUS_LABELS[run.status]}{run.message ? ` · ${run.message}` : ""}</li>)}</ul>}
        </TableCell></TableRow>}
      </Fragment>)}</TableBody>
    </Table>}
    {creating && <NewScheduleSheet open taken={(schedules ?? []).map(item => item.target_kind)} onClose={() => setCreating(false)}
      onCreated={created => { changes.current += 1; setSchedules(current => [...(current ?? []), created].sort((a, b) => a.name.localeCompare(b.name))); setCreating(false); }} />}
    <AlertDialog open={deleting !== null} onOpenChange={open => { if (!open && !removing) setDeleting(null); }}>
      <AlertDialogContent>
        <AlertDialogHeader><AlertDialogTitle>Delete schedule {deletingName.current}?</AlertDialogTitle><AlertDialogDescription>Its history is deleted too.</AlertDialogDescription></AlertDialogHeader>
        <AlertDialogFooter><AlertDialogCancel disabled={removing}>Cancel</AlertDialogCancel>
          <AlertDialogAction disabled={removing} onClick={event => { event.preventDefault(); void remove(); }}>Delete</AlertDialogAction></AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
    <ScheduleEditor schedule={editing} onClose={() => setEditing(null)} onSaved={saved => { replace(saved); setEditing(null); }} />
  </div>;
}
