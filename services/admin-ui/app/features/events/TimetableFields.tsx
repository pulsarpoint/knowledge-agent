import { useEffect, useState } from "react";
import { ApiError } from "~/lib/api";
import { PRESETS, previewSchedule, timeZones } from "./schedules";
import { Field, FieldDescription, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";

const PREVIEW_DELAY_MS = 300;

export interface Timetable { cron: string; timezone: string }

/** How often (preset or custom cron), time zone and a preview of the next runs. Reports a server validation message
 * through onProblem (null when the timetable is valid). */
export default function TimetableFields({ id, value, onChange, onProblem }: { id: string; value: Timetable; onChange: (value: Timetable) => void; onProblem: (problem: string | null) => void }) {
  const [custom, setCustom] = useState(() => !PRESETS.some(preset => preset.cron === value.cron));
  const [preview, setPreview] = useState<string[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    const cron = value.cron.trim();
    if (!cron) return;
    let live = true;
    const timer = setTimeout(() => previewSchedule(cron, value.timezone)
      .then(result => { if (live) { setPreview(result.runs); setProblem(null); onProblem(null); } })
      .catch(reason => { if (!live) return; const message = reason instanceof ApiError ? reason.message : "Could not check the schedule.";
        setPreview(null); setProblem(message); onProblem(message); }), PREVIEW_DELAY_MS);
    return () => { live = false; clearTimeout(timer); };
  }, [value.cron, value.timezone]);  // onProblem is a setter from the parent

  return <>
    <Field><FieldLabel htmlFor={`${id}-preset`}>How often</FieldLabel>
      <Select value={custom ? "custom" : value.cron} onValueChange={choice => { if (choice === "custom") setCustom(true); else { setCustom(false); onChange({ ...value, cron: choice }); } }}>
        <SelectTrigger id={`${id}-preset`} aria-label="How often" className="w-full"><SelectValue /></SelectTrigger>
        <SelectContent><SelectGroup>{PRESETS.map(preset => <SelectItem key={preset.cron} value={preset.cron}>{preset.label}</SelectItem>)}<SelectItem value="custom">Custom</SelectItem></SelectGroup></SelectContent>
      </Select></Field>
    {custom && <Field><FieldLabel htmlFor={`${id}-cron`}>Cron expression</FieldLabel>
      <Input id={`${id}-cron`} value={value.cron} maxLength={100} onChange={event => onChange({ ...value, cron: event.target.value })} />
      <FieldDescription>Five fields: minute, hour, day of month, month, day of week. For example <code>30 8 * * 1-5</code> runs at 08:30 on weekdays.</FieldDescription></Field>}
    <Field><FieldLabel htmlFor={`${id}-timezone`}>Time zone</FieldLabel>
      <Select value={value.timezone} onValueChange={timezone => onChange({ ...value, timezone })}>
        <SelectTrigger id={`${id}-timezone`} aria-label="Time zone" className="w-full"><SelectValue /></SelectTrigger>
        <SelectContent><SelectGroup>{timeZones(value.timezone).map(zone => <SelectItem key={zone} value={zone}>{zone}</SelectItem>)}</SelectGroup></SelectContent>
      </Select></Field>
    {problem ? <p role="alert" className="text-sm text-destructive">{problem}</p>
      : preview && <div className="flex flex-col gap-1"><p className="text-sm font-medium">Next runs</p>
        <ul className="text-sm text-muted-foreground">{preview.map(run => <li key={run}>{new Date(run).toLocaleString()}</li>)}</ul></div>}
  </>;
}
