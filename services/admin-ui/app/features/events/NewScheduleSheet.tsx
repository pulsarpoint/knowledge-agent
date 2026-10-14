import { useState, type FormEvent } from "react";
import { ApiError } from "~/lib/api";
import { DEFAULT_CRON, TARGETS, browserTimeZone, createSchedule, type Schedule } from "./schedules";
import TimetableFields, { type Timetable } from "./TimetableFields";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Switch } from "~/components/ui/switch";

export default function NewScheduleSheet({
  open,
  taken,
  onClose,
  onCreated,
}: {
  open: boolean;
  taken: string[];
  onClose: () => void;
  onCreated: (schedule: Schedule) => void;
}) {
  const free = Object.keys(TARGETS).filter((kind) => !taken.includes(kind));
  const [target, setTarget] = useState(free[0] ?? "");
  const [timetable, setTimetable] = useState<Timetable>({ cron: DEFAULT_CRON, timezone: browserTimeZone() });
  const [enabled, setEnabled] = useState(true);
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function create(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      onCreated(
        (
          await createSchedule({
            target_kind: target,
            cron: timetable.cron.trim(),
            timezone: timetable.timezone,
            enabled,
          })
        ).schedule,
      );
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Could not create the schedule.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>New schedule</SheetTitle>
          <SheetDescription>Choose what to run and when.</SheetDescription>
        </SheetHeader>
        {free.length === 0 ? (
          <p className="text-sm text-muted-foreground">Every target already has a schedule.</p>
        ) : (
          <form onSubmit={create} className="flex flex-col gap-4">
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="new-schedule-target">What to run</FieldLabel>
                <Select value={target} onValueChange={setTarget}>
                  <SelectTrigger id="new-schedule-target" aria-label="What to run" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {free.map((kind) => (
                        <SelectItem key={kind} value={kind}>
                          {TARGETS[kind]}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </Field>
              <TimetableFields id="new-schedule" value={timetable} onChange={setTimetable} onProblem={setProblem} />
              <Field orientation="horizontal">
                <Switch id="new-schedule-enabled" checked={enabled} onCheckedChange={setEnabled} />
                <FieldLabel htmlFor="new-schedule-enabled">Enabled</FieldLabel>
              </Field>
            </FieldGroup>
            {error && (
              <Alert variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
            <SheetFooter>
              <Button type="submit" disabled={saving || problem !== null || !timetable.cron.trim() || !target}>
                {saving && <Spinner data-icon="inline-start" />}Create
              </Button>
            </SheetFooter>
          </form>
        )}
      </SheetContent>
    </Sheet>
  );
}
