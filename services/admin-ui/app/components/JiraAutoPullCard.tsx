import { useEffect, useState } from "react";
import { Link } from "react-router";
import { ApiError } from "../lib/api";
import { DEFAULT_CRON, PRESETS, browserTimeZone, getAutoPull, saveAutoPull, type AutoPull } from "../lib/schedules";
import { ScheduleStatus } from "./SchedulesView";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "~/components/ui/card";
import { Field, FieldLabel } from "~/components/ui/field";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Switch } from "~/components/ui/switch";

const when = (value: string) => new Date(value).toLocaleString();

/** Jira → Tickets: switch the scheduled pull on or off and pick how often (a custom cron is edited on Schedules). */
export default function JiraAutoPullCard({ refreshKey }: { refreshKey: string | null }) {
  const [state, setState] = useState<AutoPull | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    getAutoPull(controller.signal).then(next => { setState(next); setError(null); })
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof ApiError ? reason.message : "Could not load the auto pull settings."); });
    return () => controller.abort();
  }, [refreshKey]);

  if (state === null || typeof state.limit !== "number") return error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : null;
  const schedule = state.schedule ?? null;
  const cron = schedule?.cron ?? DEFAULT_CRON;
  const custom = !PRESETS.some(preset => preset.cron === cron);

  async function save(enabled: boolean, nextCron: string) {
    setSaving(true); setError(null);
    try { setState(await saveAutoPull({ enabled, cron: nextCron, timezone: schedule?.timezone ?? browserTimeZone() })); }
    catch (reason) { setError(reason instanceof ApiError ? reason.message : "Could not save the auto pull settings."); }
    finally { setSaving(false); }
  }

  return <Card>
    <CardHeader><CardTitle>Auto pull</CardTitle>
      <CardDescription>Uses the query and limit of the last fetch: <code>{state.jql}</code> · up to {state.limit} tickets</CardDescription></CardHeader>
    <CardContent className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-4">
        <Field orientation="horizontal" className="w-auto"><Switch id="jira-auto-pull" checked={schedule?.enabled ?? false} disabled={saving}
          onCheckedChange={enabled => save(enabled, cron)} /><FieldLabel htmlFor="jira-auto-pull">Pull tickets automatically</FieldLabel></Field>
        {custom ? <span className="text-sm">{schedule?.description} · <Link className="underline" to="/schedules">Edit in Schedules</Link></span>
          : <Select value={cron} disabled={saving} onValueChange={value => save(schedule?.enabled ?? false, value)}>
            <SelectTrigger aria-label="How often" className="w-48"><SelectValue /></SelectTrigger>
            <SelectContent><SelectGroup>{PRESETS.map(preset => <SelectItem key={preset.cron} value={preset.cron}>{preset.label}</SelectItem>)}</SelectGroup></SelectContent>
          </Select>}
      </div>
      {schedule && <div role="status" className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        <ScheduleStatus schedule={schedule} />
        <span>{schedule.last_finished_at ? `Last pull ${when(schedule.last_finished_at)}${schedule.last_message ? ` · ${schedule.last_message}` : ""}` : "Not pulled automatically yet"}</span>
        {schedule.next_run_at && <span>· Next pull {when(schedule.next_run_at)}</span>}
      </div>}
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    </CardContent>
  </Card>;
}
