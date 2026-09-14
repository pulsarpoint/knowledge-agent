import { useEffect, useState, type FormEvent } from 'react';
import { getCoordinatorSettings, saveCoordinatorSettings, type CoordinatorSettings as Settings } from '../lib/board';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '~/components/ui/card';
import { Field, FieldDescription, FieldGroup, FieldLabel } from '~/components/ui/field';
import { Input } from '~/components/ui/input';
import { Skeleton } from '~/components/ui/skeleton';
import { Spinner } from '~/components/ui/spinner';
import { Textarea } from '~/components/ui/textarea';

export default function CoordinatorSettings() {
  const [settings, setSettings] = useState<Settings>();
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let mounted = true;
    getCoordinatorSettings().then(value => {
      if (mounted) { setSettings(value); setError(''); setDirty(false); }
    }).catch(reason => { if (mounted) setError(reason.message); });
    return () => { mounted = false; };
  }, [reload]);
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!settings || saving) return;
    setSaving(true); setError(''); setNotice('');
    try {
      const { notes, summary_prompt, max_turns, max_subtasks, timeout_seconds, revision } = settings;
      setSettings(await saveCoordinatorSettings({ notes, summary_prompt, max_turns, max_subtasks, timeout_seconds, revision }));
      setDirty(false); setNotice('Coordinator settings saved. New tasks use these settings.');
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save coordinator settings.'); }
    finally { setSaving(false); }
  }
  return <Card>
    <CardHeader><CardTitle>Execution and final summary</CardTitle><CardDescription>
      Configure how much work the coordinator can perform per task. Changes apply to new tasks.
    </CardDescription></CardHeader>
    <form onSubmit={event => void save(event)} onChange={() => { setDirty(true); setNotice(''); }}>
      <CardContent className="pb-6">
        {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
        {notice && <p role="status" className="mb-4 text-sm">{notice}</p>}
        {!settings ? <Skeleton className="h-64 w-full" /> : <FieldGroup>
          <Field><FieldLabel htmlFor="coordinator-turns">Maximum model turns</FieldLabel>
            <Input id="coordinator-turns" type="number" required min={1} max={30} disabled={saving} value={settings.max_turns} onChange={event => setSettings({ ...settings, max_turns: Number(event.target.value) })} />
            <FieldDescription>Limits coordinator model calls. The final summary uses one additional call within the task timeout.</FieldDescription>
          </Field>
          <Field><FieldLabel htmlFor="coordinator-subtasks">Maximum subagent calls per task</FieldLabel>
            <Input id="coordinator-subtasks" type="number" required min={1} max={50} disabled={saving} value={settings.max_subtasks} onChange={event => setSettings({ ...settings, max_subtasks: Number(event.target.value) })} />
            <FieldDescription>Total delegation calls, including follow-up calls to the same specialist.</FieldDescription>
          </Field>
          <Field><FieldLabel htmlFor="coordinator-timeout">Task timeout (seconds)</FieldLabel>
            <Input id="coordinator-timeout" type="number" required min={30} max={3600} disabled={saving} value={settings.timeout_seconds} onChange={event => setSettings({ ...settings, timeout_seconds: Number(event.target.value) })} />
          </Field>
          <Field><FieldLabel htmlFor="coordinator-notes">Notes and additional instructions</FieldLabel>
            <Textarea id="coordinator-notes" rows={8} maxLength={40000} disabled={saving} value={settings.notes}
              onChange={event => setSettings({ ...settings, notes: event.target.value })} />
            <FieldDescription>Included with the active system prompt when the coordinator runs. Manage system prompt versions in the Prompts tab.</FieldDescription>
          </Field>
          <Field><FieldLabel htmlFor="coordinator-summary">Final summary prompt</FieldLabel>
            <Textarea id="coordinator-summary" rows={8} required maxLength={40000} disabled={saving} value={settings.summary_prompt}
              onChange={event => setSettings({ ...settings, summary_prompt: event.target.value })} />
            <FieldDescription>The summary agent uses the coordinator’s selected model and receives its conversation and specialist results.</FieldDescription>
          </Field>
        </FieldGroup>}
      </CardContent>
      <CardFooter className="gap-3"><Button disabled={saving || !settings || !dirty}>{saving && <Spinner data-icon="inline-start" />}Save coordinator settings</Button>
        <Button type="button" variant="outline" disabled={saving} onClick={() => { setNotice(''); setReload(value => value + 1); }}>{dirty ? 'Discard changes' : 'Refresh settings'}</Button>
      </CardFooter>
    </form>
  </Card>;
}
