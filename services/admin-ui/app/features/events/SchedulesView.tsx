import { useCallback, useEffect, useRef, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { CalendarClock, History, Pencil, Play, Plus } from "lucide-react";
import { Link } from "react-router";
import { toast } from "sonner";
import { ApiError } from "~/lib/api";
import {
  EMIT,
  STATUS_LABELS,
  TARGETS,
  deleteSchedule,
  getScheduleRuns,
  getSchedules,
  runSchedule,
  saveSchedule,
  type Schedule,
  type ScheduleRun,
} from "./schedules";
import NewScheduleSheet from "./NewScheduleSheet";
import ScheduleEditor from "./ScheduleEditor";
import HandlerSheet from "./HandlerSheet";
import ConfirmRemove from "~/components/ConfirmRemove";
import DataTable, { sortHeader, sortRows, TableEmpty, type Sort } from "~/components/DataTable";
import RowMenu, { deleteMenuItem } from "~/components/RowMenu";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { Switch } from "~/components/ui/switch";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";

const REFRESH_MS = 10_000;
const when = (value: string | null) => (value ? new Date(value).toLocaleString() : "—");
const text = (reason: unknown, fallback: string) => (reason instanceof ApiError ? reason.message : fallback);
const HANDLERS = "/events/handlers";

/** The next run: a one-time schedule says when it runs, or when it ran once its time has passed. */
function nextRun(schedule: Schedule): string {
  if (schedule.run_at && schedule.enabled) return `Once at ${when(schedule.run_at)}`;
  if (schedule.run_at && schedule.last_finished_at && new Date(schedule.run_at) <= new Date())
    return `Ran once at ${when(schedule.last_finished_at)}`;
  return schedule.next_run_at ? when(schedule.next_run_at) : "Paused";
}

/** Which handlers run an Emit event schedule, or Add handler when none does. */
function HandledBy({ schedule, onAdd }: { schedule: Schedule; onAdd: () => void }) {
  const handlers = schedule.handlers ?? [];
  if (handlers.length === 0)
    return (
      <div className="flex flex-col items-start gap-1">
        <span className="text-xs text-muted-foreground">No handler runs this schedule.</span>
        <Button size="sm" variant="outline" onClick={onAdd}>
          <Plus data-icon="inline-start" />
          Add handler
        </Button>
      </div>
    );
  const [only] = handlers;
  return (
    <Link
      className="text-xs underline-offset-4 hover:underline"
      to={handlers.length === 1 ? `${HANDLERS}?handler=${encodeURIComponent(only.id)}` : HANDLERS}
    >
      {handlers.length === 1
        ? `Handled by: ${only.name}${only.enabled ? "" : " (disabled)"}`
        : `Handled by: ${handlers.length} handlers`}
    </Link>
  );
}

export function ScheduleStatus({ schedule }: { schedule: Schedule }) {
  if (schedule.running) return <Badge variant="secondary">Running</Badge>;
  if (!schedule.last_status) return <span className="text-sm text-muted-foreground">Never run</span>;
  const variant =
    schedule.last_status === "failed" ? "destructive" : schedule.last_status === "skipped" ? "outline" : "secondary";
  return <Badge variant={variant}>{STATUS_LABELS[schedule.last_status]}</Badge>;
}

type ScheduleSort = "enabled" | "name" | "next";
const byName = (a: Schedule, b: Schedule) => a.name.localeCompare(b.name);
const SCHEDULE_ORDER: Record<ScheduleSort, (a: Schedule, b: Schedule) => number> = {
  enabled: (a, b) => Number(b.enabled) - Number(a.enabled) || byName(a, b),
  name: byName,
  // Paused and one-time schedules that have run have no next run: last.
  next: (a, b) => (a.next_run_at ?? "~").localeCompare(b.next_run_at ?? "~") || byName(a, b),
};

/** Schedules: a table (Enabled switch, name, what it runs, when, next and last run; Run now, and Edit, History and
 * Delete in the row menu) with search and sortable headers; New schedule, Edit and History open right Sheets. */
export default function SchedulesView() {
  const [schedules, setSchedules] = useState<Schedule[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<Schedule | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<Schedule | null>(null);
  const [removing, setRemoving] = useState(false);
  const deletingName = useRef("");
  const deletingEmit = useRef(false);
  const [adding, setAdding] = useState<Schedule | null>(null);
  // The History sheet: the schedule's newest runs (null while loading).
  const [history, setHistory] = useState<{ id: string; name: string; runs: ScheduleRun[] | null } | null>(null);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<Sort<ScheduleSort>>({ key: "name", descending: false });

  const changes = useRef(0);

  const load = useCallback((signal?: AbortSignal) => {
    const seen = changes.current;
    return getSchedules(signal)
      .then((result) => {
        if (changes.current !== seen) return;
        setSchedules(result.schedules);
        setError(null);
      })
      .catch((reason) => {
        if (!signal?.aborted) setError(text(reason, "Could not load schedules."));
      });
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    const timer = setInterval(() => load(), REFRESH_MS);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [load]);

  /** Any local change invalidates refreshes that started earlier, so they cannot bring back older rows. Only the list
   * returns an emit schedule's handlers, so a changed row keeps the ones it had. */
  const replace = (changed: Schedule) => {
    changes.current += 1;
    setSchedules(
      (current) =>
        current?.map((item) =>
          item.id === changed.id ? { ...changed, handlers: changed.handlers ?? item.handlers } : item,
        ) ?? null,
    );
  };
  async function change(schedule: Schedule, action: () => Promise<{ schedule: Schedule }>, done: string) {
    setBusy(schedule.id);
    setError(null);
    try {
      replace((await action()).schedule);
      toast.success(done);
    } catch (reason) {
      setError(text(reason, "Could not change the schedule."));
    } finally {
      setBusy(null);
    }
  }
  async function remove() {
    const doomed = deleting;
    if (!doomed || removing) return;
    setRemoving(true);
    setError(null);
    try {
      await deleteSchedule(doomed.id);
      changes.current += 1;
      setSchedules((current) => current?.filter((item) => item.id !== doomed.id) ?? null);
      setHistory((current) => (current?.id === doomed.id ? null : current));
      toast.success(`Schedule “${doomed.name}” deleted.`);
    } catch (reason) {
      setError(text(reason, "Could not delete the schedule."));
    } finally {
      setRemoving(false);
      setDeleting(null);
    }
  }
  async function showHistory(schedule: Schedule) {
    setHistory({ id: schedule.id, name: schedule.name, runs: null });
    try {
      const { runs } = await getScheduleRuns(schedule.id);
      setHistory((current) => (current?.id === schedule.id ? { ...current, runs } : current));
    } catch (reason) {
      setHistory(null);
      setError(text(reason, "Could not load the run history."));
    }
  }

  const query = search.trim().toLowerCase();
  const shown = sortRows(
    (schedules ?? []).filter((schedule) =>
      [schedule.name, TARGETS[schedule.target_kind] ?? schedule.target_kind, schedule.description].some((value) =>
        (value ?? "").toLowerCase().includes(query),
      ),
    ),
    sort,
    SCHEDULE_ORDER,
  );
  const newSchedule = (
    <Button onClick={() => setCreating(true)}>
      <Plus data-icon="inline-start" />
      New schedule
    </Button>
  );
  const columns: ColumnDef<Schedule, unknown>[] = [
    {
      id: "enabled",
      header: () => sortHeader("Enabled", "enabled", sort, setSort),
      cell: ({ row: { original: schedule } }) => (
        <Switch
          aria-label={`Enabled: ${schedule.name}`}
          checked={schedule.enabled}
          disabled={busy === schedule.id}
          onCheckedChange={(enabled) =>
            change(
              schedule,
              () =>
                saveSchedule(schedule.id, {
                  ...(schedule.run_at ? { run_at: schedule.run_at } : { cron: schedule.cron ?? "" }),
                  timezone: schedule.timezone,
                  enabled,
                  revision: schedule.revision,
                }),
              `Schedule “${schedule.name}” ${enabled ? "enabled" : "disabled"}.`,
            )
          }
        />
      ),
    },
    {
      id: "name",
      header: () => sortHeader("Name", "name", sort, setSort),
      cell: ({ row: { original: schedule } }) => <span className="font-medium">{schedule.name}</span>,
    },
    {
      id: "runs",
      header: "Runs",
      cell: ({ row: { original: schedule } }) => (
        <div className="flex flex-col items-start gap-1">
          <span>{TARGETS[schedule.target_kind] ?? schedule.target_kind}</span>
          {schedule.target_kind === EMIT && <HandledBy schedule={schedule} onAdd={() => setAdding(schedule)} />}
        </div>
      ),
    },
    {
      id: "when",
      header: "When",
      cell: ({ row: { original: schedule } }) => (
        <>
          <div>{schedule.description}</div>
          <div className="text-xs text-muted-foreground">{schedule.timezone}</div>
        </>
      ),
    },
    {
      id: "next",
      header: () => sortHeader("Next run", "next", sort, setSort),
      cell: ({ row: { original: schedule } }) => nextRun(schedule),
    },
    {
      id: "last",
      header: "Last run",
      cell: ({ row: { original: schedule } }) => (
        <div className="flex flex-col items-start gap-1">
          <ScheduleStatus schedule={schedule} />
          {schedule.last_message && <span className="text-xs text-muted-foreground">{schedule.last_message}</span>}
        </div>
      ),
    },
    {
      id: "actions",
      header: () => <span className="sr-only">Actions</span>,
      cell: ({ row: { original: schedule } }) => (
        <div className="flex items-center justify-end gap-1">
          <Button
            size="sm"
            variant="ghost"
            disabled={busy === schedule.id || schedule.running}
            onClick={() => change(schedule, () => runSchedule(schedule.id), `Schedule “${schedule.name}” started.`)}
          >
            <Play data-icon="inline-start" />
            Run now
          </Button>
          <RowMenu
            name={schedule.name}
            items={[
              { label: "Edit", icon: Pencil, onSelect: () => setEditing(schedule) },
              { label: "History", icon: History, onSelect: () => void showHistory(schedule) },
              deleteMenuItem(() => {
                deletingName.current = schedule.name;
                deletingEmit.current = schedule.target_kind === EMIT;
                setDeleting(schedule);
              }),
            ]}
          />
        </div>
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex justify-end">{newSchedule}</div>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <DataTable
        columns={columns}
        data={shown}
        loading={schedules === null && !error}
        rowId={(schedule) => schedule.id}
        search={{ plural: "schedules", value: search, onChange: setSearch }}
        sort={sort}
        resetKey={`${sort.key}\n${sort.descending}`}
        empty={
          schedules?.length === 0 ? (
            <TableEmpty
              icon={CalendarClock}
              title="No schedules yet"
              description="A schedule pulls Jira tickets or emits an event on a timetable."
            >
              {newSchedule}
            </TableEmpty>
          ) : schedules ? (
            "No schedules match."
          ) : (
            "Could not load schedules."
          )
        }
      />
      <Sheet open={history !== null} onOpenChange={(open) => !open && setHistory(null)}>
        <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-md">
          <SheetHeader>
            <SheetTitle>History of {history?.name}</SheetTitle>
            <SheetDescription>The newest runs, scheduled or started with Run now.</SheetDescription>
          </SheetHeader>
          {history?.runs === null ? (
            <Skeleton className="h-8 w-full" />
          ) : history?.runs.length === 0 ? (
            <p className="text-sm text-muted-foreground">No runs yet.</p>
          ) : (
            <ul className="flex flex-col gap-1 text-sm">
              {history?.runs.map((run) => (
                <li key={run.id}>
                  {when(run.started_at)} · {run.trigger === "manual" ? "Run now" : "Scheduled"} ·{" "}
                  {STATUS_LABELS[run.status]}
                  {run.message ? ` · ${run.message}` : ""}
                </li>
              ))}
            </ul>
          )}
        </SheetContent>
      </Sheet>
      {creating && (
        <NewScheduleSheet
          open
          taken={(schedules ?? []).map((item) => item.target_kind)}
          onClose={() => setCreating(false)}
          onCreated={(created) => {
            changes.current += 1;
            setSchedules((current) => [...(current ?? []), created].sort((a, b) => a.name.localeCompare(b.name)));
            setCreating(false);
            toast.success(`Schedule “${created.name}” created.`);
          }}
        />
      )}
      <ConfirmRemove
        removal={
          deleting
            ? {
                title: `Delete “${deletingName.current}”?`,
                description: `Its history is deleted too.${
                  deletingEmit.current ? " Its handlers stay; they no longer match any event." : ""
                }`,
                action: "Delete schedule",
                run: remove,
              }
            : null
        }
        onClose={() => setDeleting(null)}
        busy={removing}
      />
      <HandlerSheet
        open={adding !== null}
        handler={null}
        schedule={adding ?? undefined}
        onClose={() => setAdding(null)}
        onSaved={(saved) => {
          setAdding(null);
          toast.success(`Handler “${saved.name}” saved.`);
          void load(); // the list is what knows which handlers run each schedule
        }}
      />
      <ScheduleEditor
        schedule={editing}
        onClose={() => setEditing(null)}
        onSaved={(saved) => {
          replace(saved);
          setEditing(null);
          toast.success(`Schedule “${saved.name}” saved.`);
        }}
      />
    </div>
  );
}
