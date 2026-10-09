import { useState, type FormEvent } from "react";
import { ApiError } from "../lib/api";
import { createEvent, type EventDetail, type NewEventInput } from "../lib/events";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Textarea } from "~/components/ui/textarea";

export interface Prefill { source?: "manual" | "jira"; type?: string; subject?: string; title?: string; payload?: Record<string, unknown> }

/** Reads the Payload field: the object it holds, or what is wrong with it. */
function parsePayload(text: string): { value: Record<string, unknown> } | { problem: string } {
  if (!text.trim()) return { value: {} };
  let parsed: unknown;
  try { parsed = JSON.parse(text); } catch { return { problem: "The payload is not valid JSON." }; }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return { problem: "The payload must be a JSON object, like {\"key\": \"value\"}." };
  return { value: parsed as Record<string, unknown> };
}

export default function NewEventSheet({ prefill, types, onClose, onCreated }: { prefill?: Prefill; types: string[]; onClose: () => void; onCreated: (event: EventDetail) => void }) {
  const [source, setSource] = useState<NewEventInput["source"]>(prefill?.source ?? "manual");
  const [type, setType] = useState(prefill?.type ?? "");
  const [subject, setSubject] = useState(prefill?.subject ?? "");
  const [title, setTitle] = useState(prefill?.title ?? "");
  const [payload, setPayload] = useState(prefill?.payload ? JSON.stringify(prefill.payload, null, 2) : "");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const parsed = parsePayload(payload);
  const problem = "problem" in parsed ? parsed.problem : null;

  async function create(event: FormEvent) {
    event.preventDefault();
    if ("problem" in parsed) return;
    setSaving(true); setError(null);
    try { onCreated((await createEvent({ source, type: type.trim(), subject: subject.trim(), title, payload: parsed.value })).event); }
    catch (reason) { setError(reason instanceof ApiError ? reason.message : "Could not create the event."); setSaving(false); }
  }

  return <Sheet open onOpenChange={next => { if (!next) onClose(); }}>
    <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-lg">
      <SheetHeader><SheetTitle>New event</SheetTitle>
        <SheetDescription>Make an event by hand to try your handlers. It is processed like any other event.</SheetDescription></SheetHeader>
      <form onSubmit={create} className="flex flex-col gap-4 px-4 pb-4">
        <FieldGroup>
          <Field><FieldLabel htmlFor="new-event-source">Source</FieldLabel>
            <Select value={source} onValueChange={value => setSource(value as NewEventInput["source"])}>
              <SelectTrigger id="new-event-source" aria-label="Source" className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent><SelectGroup><SelectItem value="manual">Manual</SelectItem><SelectItem value="jira">Jira (simulated)</SelectItem></SelectGroup></SelectContent>
            </Select>
            {source === "jira" && <FieldDescription>Handlers with a “source equals jira” condition match. No Jira connection is involved.</FieldDescription>}</Field>
          <Field><FieldLabel htmlFor="new-event-type">Type</FieldLabel>
            <Input id="new-event-type" list="new-event-types" value={type} placeholder="jira.ticket.created" autoComplete="off" onChange={event => setType(event.target.value)} />
            <datalist id="new-event-types">{types.map(known => <option key={known} value={known} />)}</datalist></Field>
          <Field><FieldLabel htmlFor="new-event-subject">Subject</FieldLabel>
            <Input id="new-event-subject" value={subject} maxLength={500} placeholder="DEMO-1" onChange={event => setSubject(event.target.value)} /></Field>
          <Field><FieldLabel htmlFor="new-event-title">Title</FieldLabel>
            <Input id="new-event-title" value={title} maxLength={500} onChange={event => setTitle(event.target.value)} /></Field>
          <Field data-invalid={problem !== null || undefined}><FieldLabel htmlFor="new-event-payload">Payload</FieldLabel>
            <Textarea id="new-event-payload" value={payload} rows={10} spellCheck={false} className="font-mono text-xs" placeholder='{"labels": ["agent"]}'
              aria-invalid={problem !== null} onChange={event => setPayload(event.target.value)} />
            {problem ? <FieldError>{problem}</FieldError> : <FieldDescription>JSON object. Handlers read it as payload.&lt;field&gt;.</FieldDescription>}</Field>
        </FieldGroup>
        {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
        <SheetFooter><Button type="submit" disabled={saving || problem !== null || !type.trim() || !subject.trim()}>{saving && <Spinner data-icon="inline-start" />}Create</Button></SheetFooter>
      </form>
    </SheetContent>
  </Sheet>;
}
