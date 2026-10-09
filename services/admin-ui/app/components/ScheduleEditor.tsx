import { useEffect, useState, type FormEvent } from "react";
import { ApiError } from "../lib/api";
import { PRESETS, previewSchedule, saveSchedule, timeZones, type Schedule } from "../lib/schedules";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";

const PREVIEW_DELAY_MS = 300;

export default function ScheduleEditor({ schedule, onClose, onSaved }: { schedule: Schedule | null; onClose: () => void; onSaved: (schedule: Schedule) => void }) {
  const [cron, setCron] = useState("");
  const [custom, setCustom] = useState(false);
  const [timezone, setTimezone] = useState("UTC");
  const [preview, setPreview] = useState<string[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!schedule) return;
    setCron(schedule.cron); setTimezone(schedule.timezone); setCustom(!PRESETS.some(preset => preset.cron === schedule.cron));
    setPreview(null); setProblem(null); setError(null);
  }, [schedule]);

  useEffect(() => {
    if (!schedule || !cron.trim()) return;
    let live = true;
    const timer = setTimeout(() => previewSchedule(cron.trim(), timezone)
      .then(result => { if (live) { setPreview(result.runs); setProblem(null); } })
      .catch(reason => { if (live) { setPreview(null); setProblem(reason instanceof ApiError ? reason.message : "Could not check the schedule."); } }), PREVIEW_DELAY_MS);
    return () => { live = false; clearTimeout(timer); };
  }, [schedule, cron, timezone]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!schedule) return;
    setSaving(true); setError(null);
    try { onSaved((await saveSchedule(schedule.id, { cron: cron.trim(), timezone, enabled: schedule.enabled, revision: schedule.revision })).schedule); }
    catch (reason) { setError(reason instanceof ApiError ? reason.message : "Could not save the schedule."); }
    finally { setSaving(false); }
  }

  return <Sheet open={schedule !== null} onOpenChange={open => { if (!open) onClose(); }}>
    <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-md">
      <SheetHeader><SheetTitle>Edit schedule</SheetTitle><SheetDescription>{schedule?.name}</SheetDescription></SheetHeader>
      <form onSubmit={save} className="flex flex-col gap-4">
        <FieldGroup>
          <Field><FieldLabel htmlFor="schedule-preset">How often</FieldLabel>
            <Select value={custom ? "custom" : cron} onValueChange={value => { if (value === "custom") setCustom(true); else { setCustom(false); setCron(value); } }}>
              <SelectTrigger id="schedule-preset" aria-label="How often" className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent><SelectGroup>{PRESETS.map(preset => <SelectItem key={preset.cron} value={preset.cron}>{preset.label}</SelectItem>)}<SelectItem value="custom">Custom</SelectItem></SelectGroup></SelectContent>
            </Select></Field>
          {custom && <Field><FieldLabel htmlFor="schedule-cron">Cron expression</FieldLabel>
            <Input id="schedule-cron" value={cron} maxLength={100} onChange={event => setCron(event.target.value)} />
            <FieldDescription>Five fields: minute, hour, day of month, month, day of week. For example <code>30 8 * * 1-5</code> runs at 08:30 on weekdays.</FieldDescription></Field>}
          <Field><FieldLabel htmlFor="schedule-timezone">Time zone</FieldLabel>
            <Select value={timezone} onValueChange={setTimezone}>
              <SelectTrigger id="schedule-timezone" aria-label="Time zone" className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent><SelectGroup>{timeZones(timezone).map(zone => <SelectItem key={zone} value={zone}>{zone}</SelectItem>)}</SelectGroup></SelectContent>
            </Select></Field>
          {problem ? <p role="alert" className="text-sm text-destructive">{problem}</p>
            : preview && <div className="flex flex-col gap-1"><p className="text-sm font-medium">Next runs</p>
              <ul className="text-sm text-muted-foreground">{preview.map(run => <li key={run}>{new Date(run).toLocaleString()}</li>)}</ul></div>}
        </FieldGroup>
        {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
        <SheetFooter><Button type="submit" disabled={saving || problem !== null || !cron.trim()}>{saving && <Spinner data-icon="inline-start" />}Save</Button></SheetFooter>
      </form>
    </SheetContent>
  </Sheet>;
}
