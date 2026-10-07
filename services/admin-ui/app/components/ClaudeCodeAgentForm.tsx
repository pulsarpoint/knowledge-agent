import { useState } from 'react';
import { ApiError } from '~/lib/api';
import { createClaudeCodeAgent, saveClaudeCodeAgent, type ClaudeCodeAgent } from '~/lib/claudeCode';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '~/components/ui/card';
import { Field, FieldDescription, FieldGroup, FieldLabel } from '~/components/ui/field';
import { Input } from '~/components/ui/input';
import { Switch } from '~/components/ui/switch';
import { Textarea } from '~/components/ui/textarea';

/** A Claude Code agent is a registration: a name, a description and whether it can start sessions. Model, folders and
 * prompt come with each session. On the agent's page its name is the heading, so the card has none. */
export default function ClaudeCodeAgentForm({ agent, onSaved, onCancel }: {
  agent?: ClaudeCodeAgent; onSaved: (saved: ClaudeCodeAgent) => void; onCancel?: () => void;
}) {
  const [name, setName] = useState(agent?.name ?? '');
  const [description, setDescription] = useState(agent?.description ?? '');
  const [enabled, setEnabled] = useState(agent?.enabled ?? true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function submit() {
    setBusy(true); setError('');
    try {
      const value = { name: name.trim(), description: description.trim() };
      if (agent) onSaved(await saveClaudeCodeAgent(agent.id, { ...value, enabled, revision: agent.revision }));
      else onSaved({ id: (await createClaudeCodeAgent(value)).id, ...value, enabled: true, revision: 1 });
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Could not save the agent.'); }
    finally { setBusy(false); }
  }
  return <Card>
    <CardHeader>{!agent && <CardTitle>New Claude Code agent</CardTitle>}
      <CardDescription>Runs with the Claude Agent SDK in its own container: read-only file tools and Bash. The model, the folders and the prompt are chosen when a session starts.</CardDescription></CardHeader>
    <CardContent><form id="claude-code-agent" onSubmit={e => { e.preventDefault(); void submit(); }}><FieldGroup>
      <Field><FieldLabel htmlFor="claude-code-agent-name">Name</FieldLabel><Input id="claude-code-agent-name" maxLength={100} value={name} onChange={e => setName(e.target.value)} /></Field>
      <Field><FieldLabel htmlFor="claude-code-agent-description">Description</FieldLabel><Textarea id="claude-code-agent-description" maxLength={4000} value={description} onChange={e => setDescription(e.target.value)} />
        <FieldDescription>Optional. Shown when you choose an agent for a session.</FieldDescription></Field>
      {agent && <Field orientation="horizontal"><Switch id="claude-code-agent-enabled" disabled={busy} checked={enabled} onCheckedChange={setEnabled} />
        <div><FieldLabel htmlFor="claude-code-agent-enabled">Enable agent</FieldLabel><FieldDescription>Disabled agents cannot start new sessions; their sessions and history are kept.</FieldDescription></div></Field>}
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    </FieldGroup></form></CardContent>
    <CardFooter className="gap-2"><Button type="submit" form="claude-code-agent" disabled={busy || !name.trim()}>{agent ? 'Save agent' : 'Create agent'}</Button>
      {onCancel && <Button type="button" variant="outline" disabled={busy} onClick={onCancel}>Cancel</Button>}</CardFooter>
  </Card>;
}
