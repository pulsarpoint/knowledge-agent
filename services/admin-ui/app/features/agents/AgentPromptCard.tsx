import { useEffect, useState } from 'react';
import { saveAgentPrompt, type AgentProfile } from './api';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '~/components/ui/card';
import { Field, FieldDescription, FieldLabel } from '~/components/ui/field';
import { Spinner } from '~/components/ui/spinner';
import { Textarea } from '~/components/ui/textarea';

export const PROMPT_LIMIT = 100000;

/** The agent's system prompt: one text, saved with the agent's revision. What is saved here is what runs. */
export default function AgentPromptCard({ profile, onSaved, onDirtyChange }: { profile: AgentProfile; onSaved: (profile: AgentProfile) => void; onDirtyChange?: (dirty: boolean) => void }) {
  const [text, setText] = useState(profile.system_prompt);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  useEffect(() => { setText(profile.system_prompt); }, [profile.id, profile.system_prompt]);
  const dirty = text !== profile.system_prompt;
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  async function save() {
    setBusy(true); setError(''); setNotice('');
    try { onSaved(await saveAgentPrompt(profile.id, profile.revision, text)); setNotice('System prompt saved.'); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save the system prompt.'); }
    finally { setBusy(false); }
  }
  return <Card><form onSubmit={event => { event.preventDefault(); if (dirty && text.trim() && !busy) void save(); }}>
    <CardHeader><CardTitle>System prompt</CardTitle><CardDescription>Required. What this agent runs with: changes apply to new conversations and runs; running conversations keep the configuration they started with.</CardDescription></CardHeader>
    <CardContent className="flex flex-col gap-3">
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      <Field><FieldLabel htmlFor="agent-system-prompt">System prompt</FieldLabel>
        <Textarea id="agent-system-prompt" rows={12} maxLength={PROMPT_LIMIT} value={text} disabled={busy}
          onChange={event => { setText(event.target.value); setNotice(''); }} />
        <FieldDescription>{text.length.toLocaleString()} / {PROMPT_LIMIT.toLocaleString()} characters</FieldDescription></Field>
      {notice && <p role="status" className="text-sm text-muted-foreground">{notice}</p>}
    </CardContent>
    <CardFooter className="flex-wrap gap-2">
      <Button disabled={busy || !dirty || !text.trim()}>{busy && <Spinner data-icon="inline-start" />}Save prompt</Button>
      <Button type="button" variant="outline" disabled={busy || !dirty} onClick={() => { setText(profile.system_prompt); setError(''); }}>Discard prompt changes</Button>
    </CardFooter>
  </form></Card>;
}
