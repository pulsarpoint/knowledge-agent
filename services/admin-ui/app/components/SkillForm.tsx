import { useState, type FormEvent } from 'react';
import { ApiError } from '../lib/api';
import { saveSkill, type Skill } from '../lib/skills';
import Markdown from './Markdown';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '~/components/ui/card';
import { Field, FieldContent, FieldDescription, FieldGroup, FieldLabel, FieldSet } from '~/components/ui/field';
import { Input } from '~/components/ui/input';
import { Spinner } from '~/components/ui/spinner';
import { Switch } from '~/components/ui/switch';
import { Textarea } from '~/components/ui/textarea';

export default function SkillForm({ skill, onSaved, onClose }: {
  skill: Skill | null; onSaved: (skill: Skill) => void; onClose: () => void;
}) {
  const [name, setName] = useState(skill?.name ?? '');
  const [description, setDescription] = useState(skill?.description ?? '');
  const [instructions, setInstructions] = useState(skill?.instructions ?? '');
  const [enabled, setEnabled] = useState(skill?.enabled ?? true);
  const [preview, setPreview] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (saving) return;
    if (!name.trim() || !description.trim() || !instructions.trim()) {
      setError('Enter a name, description, and instructions.'); return;
    }
    setSaving(true); setError(null);
    try {
      onSaved(await saveSkill({ name, description, instructions, enabled, ...(skill ? { revision: skill.revision } : {}) }, skill?.id));
    } catch (error) {
      setError(error instanceof ApiError ? error.message : 'Could not save this skill. Your changes are still in the editor.');
    } finally { setSaving(false); }
  }

  return <Card>
    <CardHeader><CardTitle>{skill ? `Edit ${skill.name}` : 'Add skill'}</CardTitle>
      <CardDescription>Describe when to use this skill and the steps an agent should follow.</CardDescription></CardHeader>
    <form onSubmit={submit} className="flex flex-col gap-6">
      <CardContent className="flex flex-col gap-4">
        {error && <Alert variant="destructive"><AlertDescription role="alert">{error}</AlertDescription></Alert>}
        <FieldSet disabled={saving}><FieldGroup>
          <Field><FieldLabel htmlFor="skill-name">Skill name</FieldLabel>
            <Input id="skill-name" required maxLength={64} pattern="[a-z0-9]+(-[a-z0-9]+)*" value={name} onChange={(event) => setName(event.target.value)} placeholder="inspect-service-logs" />
            <FieldDescription>Use lowercase letters, numbers, and single hyphens. Names must be unique.</FieldDescription></Field>
          <Field><FieldLabel htmlFor="skill-description">Description</FieldLabel>
            <Textarea id="skill-description" required rows={2} maxLength={1024} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Use when investigating errors or unexpected behavior in a running service." />
            <FieldDescription>Explain when an agent should use this skill.</FieldDescription></Field>
          <Field><FieldLabel htmlFor="skill-instructions">Instructions (Markdown)</FieldLabel>
            <Textarea id="skill-instructions" required rows={14} maxLength={100000} value={instructions} onChange={(event) => setInstructions(event.target.value)} placeholder={'# Inspect service logs\n\n1. Identify the service and time window.\n2. Review relevant logs.\n3. Summarize findings with evidence.'} />
            <FieldDescription>Write the skill’s steps, examples, and expected output.</FieldDescription></Field>
          <Field orientation="horizontal" data-disabled={saving}>
            <FieldContent><FieldLabel htmlFor="skill-enabled">Enabled</FieldLabel><FieldDescription>Keep this skill available for agent configuration. Disabled skills remain editable.</FieldDescription></FieldContent>
            <Switch id="skill-enabled" checked={enabled} disabled={saving} onCheckedChange={setEnabled} />
          </Field>
        </FieldGroup></FieldSet>
        <div><Button type="button" variant="outline" disabled={!instructions.trim()} aria-expanded={preview} aria-controls="skill-preview" onClick={() => setPreview((value) => !value)}>{preview ? 'Hide preview' : 'Preview instructions'}</Button></div>
        {preview && <section id="skill-preview" aria-label="Instructions preview" className="min-w-0 rounded-lg border p-4"><Markdown content={instructions} /></section>}
      </CardContent>
      <CardFooter className="gap-2"><Button disabled={saving}>{saving && <Spinner data-icon="inline-start" />}{skill ? 'Save changes' : 'Create skill'}</Button>
        <Button type="button" variant="outline" disabled={saving} onClick={onClose}>Cancel</Button></CardFooter>
    </form>
  </Card>;
}
