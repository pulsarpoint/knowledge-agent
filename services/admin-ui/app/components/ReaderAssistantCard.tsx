import { useEffect, useState } from 'react';
import { getReaderSettings, saveReaderSettings, type ReaderSettings } from '../lib/reader';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '~/components/ui/card';
import { Field, FieldDescription, FieldLabel } from '~/components/ui/field';

export default function ReaderAssistantCard() {
  const [settings, setSettings] = useState<ReaderSettings | null>(null);
  const [choice, setChoice] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  useEffect(() => {
    let live = true;
    getReaderSettings().then(data => {
      if (live && Array.isArray(data?.candidates)) { setSettings(data); setChoice(data.knowledge_agent_id ?? ''); }
    }).catch(() => undefined);
    return () => { live = false; };
  }, []);
  if (!settings) return null;
  async function save() {
    setBusy(true); setError(''); setNotice('');
    try { const saved = await saveReaderSettings(choice, settings!.revision); setSettings(saved); setNotice('Saved.'); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not save the Reader agent.'); }
    finally { setBusy(false); }
  }
  return <Card>
    <CardHeader>
      <CardTitle>Reader assistant</CardTitle>
      <CardDescription>The agent that answers questions in the Reader. It must use only documents, skills and services, with “Answer only from documents” on.</CardDescription>
    </CardHeader>
    <CardContent className="flex flex-col gap-3">
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      <Field>
        <FieldLabel htmlFor="reader-agent">Reader agent</FieldLabel>
        <select id="reader-agent" className="h-9 rounded-md border bg-background px-2 text-sm" value={choice} disabled={busy} onChange={event => setChoice(event.target.value)}>
          {settings.knowledge_agent_id === null && <option value="">Not configured</option>}
          {settings.candidates.map(agent => <option key={agent.id} value={agent.id} disabled={agent.violations.length > 0}>
            {agent.violations.length ? `${agent.name} — ${agent.violations.join(', ')}` : agent.name}
          </option>)}
        </select>
        <FieldDescription>Changing the agent affects new Reader conversations; existing ones keep their agent.</FieldDescription>
      </Field>
      <div className="flex items-center gap-3">
        <Button type="button" size="sm" disabled={busy || !choice || choice === settings.knowledge_agent_id} onClick={() => void save()}>Save Reader agent</Button>
        {notice && <span role="status" className="text-sm text-muted-foreground">{notice}</span>}
      </div>
    </CardContent>
  </Card>;
}
