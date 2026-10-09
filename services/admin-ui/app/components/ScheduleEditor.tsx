import { useState, type FormEvent } from "react";
import { ApiError } from "../lib/api";
import { saveSchedule, type Schedule } from "../lib/schedules";
import TimetableFields, { type Timetable } from "./TimetableFields";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { FieldGroup } from "~/components/ui/field";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";

function ScheduleForm({ schedule, onSaved }: { schedule: Schedule; onSaved: (schedule: Schedule) => void }) {
  const [timetable, setTimetable] = useState<Timetable>({ cron: schedule.cron, timezone: schedule.timezone });
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true); setError(null);
    try { onSaved((await saveSchedule(schedule.id, { cron: timetable.cron.trim(), timezone: timetable.timezone, enabled: schedule.enabled, revision: schedule.revision })).schedule); }
    catch (reason) { setError(reason instanceof ApiError ? reason.message : "Could not save the schedule."); }
    finally { setSaving(false); }
  }

  return <form onSubmit={save} className="flex flex-col gap-4">
    <FieldGroup><TimetableFields id="schedule" value={timetable} onChange={setTimetable} onProblem={setProblem} /></FieldGroup>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <SheetFooter><Button type="submit" disabled={saving || problem !== null || !timetable.cron.trim()}>{saving && <Spinner data-icon="inline-start" />}Save</Button></SheetFooter>
  </form>;
}

export default function ScheduleEditor({ schedule, onClose, onSaved }: { schedule: Schedule | null; onClose: () => void; onSaved: (schedule: Schedule) => void }) {
  return <Sheet open={schedule !== null} onOpenChange={open => { if (!open) onClose(); }}>
    <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-md">
      <SheetHeader><SheetTitle>Edit schedule</SheetTitle><SheetDescription>{schedule?.name}</SheetDescription></SheetHeader>
      {schedule && <ScheduleForm key={schedule.id} schedule={schedule} onSaved={onSaved} />}
    </SheetContent>
  </Sheet>;
}
