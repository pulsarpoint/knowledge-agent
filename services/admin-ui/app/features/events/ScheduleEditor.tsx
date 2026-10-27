import { useEffect, useState, type FormEvent } from "react";
import { ApiError } from "~/lib/api";
import { EMIT, saveSchedule, type Schedule } from "./schedules";
import TimetableFields, { complete, timetableOf, timing, type Timetable } from "./TimetableFields";
import JiraPullFields, { jiraPull, jiraPullParams, type JiraPull } from "./JiraPullFields";
import { useSheetGuard } from "~/components/LeaveGuard";
import { Alert, AlertDescription } from "~/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "~/components/ui/alert-dialog";
import { Button } from "~/components/ui/button";
import { Field, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";

function ScheduleForm({
  schedule,
  onSaved,
  onDirtyChange,
}: {
  schedule: Schedule;
  onSaved: (schedule: Schedule) => void;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const [name, setName] = useState(schedule.name);
  const [timetable, setTimetable] = useState<Timetable>(() => timetableOf(schedule));
  const [pull, setPull] = useState<JiraPull>(() => jiraPull(schedule.params));
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const values = JSON.stringify([name, timetable, pull]);
  const [initial] = useState(values);
  const dirty = values !== initial;
  useEffect(() => {
    onDirtyChange(dirty);
    return () => onDirtyChange(false);
  }, [dirty]);
  /** Handlers bound to the old name follow a rename (the list knows which those are). */
  const bound = schedule.handlers?.length ?? 0;
  const renaming = schedule.target_kind === EMIT && name.trim() !== schedule.name;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (renaming && bound > 0) setConfirming(true);
    else void save();
  }
  async function save() {
    setConfirming(false);
    setSaving(true);
    setError(null);
    try {
      onSaved(
        (
          await saveSchedule(schedule.id, {
            ...(schedule.target_kind === EMIT ? { name: name.trim() } : {}),
            ...timing(timetable),
            // a one-time schedule leaves it out: a new time turns it (back) on
            ...(timetable.once ? {} : { enabled: schedule.enabled }),
            revision: schedule.revision,
            ...(schedule.target_kind === "jira.pull" ? { params: jiraPullParams(pull) } : {}),
          })
        ).schedule,
      );
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Could not save the schedule.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <FieldGroup>
        {schedule.target_kind === EMIT && (
          <Field>
            <FieldLabel htmlFor="schedule-name">Name</FieldLabel>
            <Input id="schedule-name" maxLength={200} value={name} onChange={(event) => setName(event.target.value)} />
          </Field>
        )}
        {schedule.target_kind === "jira.pull" && <JiraPullFields id="schedule" value={pull} onChange={setPull} />}
        <TimetableFields
          id="schedule"
          value={timetable}
          canRunOnce={schedule.target_kind === EMIT}
          onChange={setTimetable}
          onProblem={setProblem}
        />
      </FieldGroup>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <SheetFooter>
        <Button type="submit" disabled={saving || problem !== null || !complete(timetable) || !name.trim()}>
          {saving && <Spinner data-icon="inline-start" />}Save
        </Button>
      </SheetFooter>
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Rename schedule to {name.trim()}?</AlertDialogTitle>
            <AlertDialogDescription>
              This also updates {bound === 1 ? "1 handler" : `${bound} handlers`}.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => void save()}>Rename</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </form>
  );
}

export default function ScheduleEditor({
  schedule,
  onClose,
  onSaved,
}: {
  schedule: Schedule | null;
  onClose: () => void;
  onSaved: (schedule: Schedule) => void;
}) {
  const [dirty, setDirty] = useState(false);
  const guard = useSheetGuard(dirty);
  return (
    <Sheet
      open={schedule !== null}
      onOpenChange={(open) => {
        if (!open) guard.close(onClose);
      }}
    >
      {guard.dialog}
      <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>Edit schedule</SheetTitle>
          <SheetDescription>{schedule?.name}</SheetDescription>
        </SheetHeader>
        {schedule && <ScheduleForm key={schedule.id} schedule={schedule} onSaved={onSaved} onDirtyChange={setDirty} />}
      </SheetContent>
    </Sheet>
  );
}
