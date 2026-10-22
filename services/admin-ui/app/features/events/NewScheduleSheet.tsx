import { useState, type FormEvent } from "react";
import { ApiError } from "~/lib/api";
import { DEFAULT_CRON, EMIT, TARGETS, browserTimeZone, createSchedule, type Schedule } from "./schedules";
import TimetableFields, { complete, timing, type Timetable } from "./TimetableFields";
import JiraPullFields, { jiraPullParams, type JiraPull } from "./JiraPullFields";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
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
  // The Jira pull has one schedule; Emit event can have any number.
  const free = Object.keys(TARGETS).filter((kind) => kind === EMIT || !taken.includes(kind));
  const [target, setTarget] = useState(free[0]);
  const [name, setName] = useState("");
  const [timetable, setTimetable] = useState<Timetable>({
    cron: DEFAULT_CRON,
    timezone: browserTimeZone(),
    once: false,
    runAt: "",
  });
  const [enabled, setEnabled] = useState(true);
  const [pull, setPull] = useState<JiraPull>({ jql: "", limit: "" });
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
            ...(target === EMIT ? { name: name.trim() } : {}),
            ...timing(timetable),
            enabled,
            ...(target === "jira.pull" ? { params: jiraPullParams(pull) } : {}),
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
        <form onSubmit={create} className="flex flex-col gap-4">
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="new-schedule-target">What to run</FieldLabel>
              <Select
                value={target}
                onValueChange={(kind) => {
                  setTarget(kind);
                  if (kind !== EMIT) setTimetable((current) => ({ ...current, once: false })); // the Jira pull repeats
                }}
              >
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
            {target === EMIT && (
              <Field>
                <FieldLabel htmlFor="new-schedule-name">Name</FieldLabel>
                <Input
                  id="new-schedule-name"
                  maxLength={200}
                  placeholder="Morning summary"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                />
              </Field>
            )}
            {target === "jira.pull" && <JiraPullFields id="new-schedule" value={pull} onChange={setPull} />}
            <TimetableFields
              id="new-schedule"
              value={timetable}
              canRunOnce={target === EMIT}
              onChange={setTimetable}
              onProblem={setProblem}
            />
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
            <Button
              type="submit"
              disabled={saving || problem !== null || !complete(timetable) || (target === EMIT && !name.trim())}
            >
              {saving && <Spinner data-icon="inline-start" />}Create
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
