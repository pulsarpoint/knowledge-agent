import { useEffect, useRef, useState, type FormEvent } from "react";
import { Plus, X } from "lucide-react";
import { ApiError } from "../lib/api";
import { DEFAULT_SORT, NO_FILTERS, getEventFacets, getEvents, type EventRow } from "../lib/events";
import { FIELD_SUGGESTIONS, OPERATOR_LABELS, createHandler, getHandlerAgents, previewHandler, saveHandler,
  type EventHandler, type HandlerDraft, type Operator, type PreviewResult } from "../lib/eventHandlers";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Switch } from "~/components/ui/switch";
import { Textarea } from "~/components/ui/textarea";

interface ConditionRow { field: string; op: Operator; value: string }
interface Form { name: string; enabled: boolean; type_pattern: string; conditions: ConditionRow[]; action: "emit" | "agent";
  emit: { type: string; subject: string; title: string; payload: string }; agent_id: string; prompt: string }

const BLANK: Form = { name: "", enabled: true, type_pattern: "jira.ticket.*", conditions: [], action: "emit",
  emit: { type: "", subject: "", title: "", payload: "" }, agent_id: "", prompt: "" };
const HELP = "event.id, event.source, event.type, event.subject, event.title, event.occurred_at, event.payload.<field>, event.parent_id, event.root_id, event.depth";

function fromHandler(handler: EventHandler): Form {
  return { name: handler.name, enabled: handler.enabled, type_pattern: handler.type_pattern, action: handler.action,
    conditions: handler.conditions.map(c => ({ field: c.field, op: c.op, value: Array.isArray(c.value) ? c.value.join(", ") : c.value ?? "" })),
    emit: handler.emit ?? BLANK.emit, agent_id: handler.agent_id ?? "", prompt: handler.prompt ?? "" };
}
function toDraft(form: Form): HandlerDraft {
  return { name: form.name.trim(), enabled: form.enabled, type_pattern: form.type_pattern.trim(), action: form.action,
    conditions: form.conditions.map(c => ({ field: c.field.trim(), op: c.op,
      value: c.op === "exists" ? null : c.op === "in" ? c.value.split(",").map(item => item.trim()).filter(Boolean) : c.value })),
    emit: form.action === "emit" ? form.emit : null, agent_id: form.action === "agent" ? form.agent_id || null : null,
    prompt: form.action === "agent" ? form.prompt : null };
}

function HandlerForm({ handler, onSaved }: { handler: EventHandler | null; onSaved: (handler: EventHandler) => void }) {
  const [form, setForm] = useState<Form>(handler ? fromHandler(handler) : BLANK);
  const [agents, setAgents] = useState<{ id: string; name: string }[]>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [recent, setRecent] = useState<EventRow[]>([]);
  const [previewEvent, setPreviewEvent] = useState("");
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const previewRun = useRef(0);

  useEffect(() => () => { previewRun.current += 1; }, []);
  useEffect(() => {
    getHandlerAgents().then(result => setAgents(result.agents)).catch(() => setAgents([]));
    getEventFacets().then(result => setTypes(result.types)).catch(() => setTypes([]));
    getEvents(NO_FILTERS, DEFAULT_SORT).then(result => setRecent(result.events)).catch(() => setRecent([]));
  }, []);

  const update = (changes: Partial<Form>) => setForm(current => ({ ...current, ...changes }));
  const setCondition = (index: number, changes: Partial<ConditionRow>) =>
    update({ conditions: form.conditions.map((row, at) => at === index ? { ...row, ...changes } : row) });
  const setEmit = (field: keyof Form["emit"], value: string) => update({ emit: { ...form.emit, [field]: value } });
  const message = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback;

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError(null);
    try {
      const draft = toDraft(form);
      onSaved((handler ? await saveHandler(handler.id, { ...draft, revision: handler.revision }) : await createHandler(draft)).handler);
    } catch (reason) { setError(message(reason, "Could not save the handler.")); }
    finally { setBusy(false); }
  }
  async function runPreview() {
    if (!previewEvent) return;
    const run = ++previewRun.current;
    setPreview(null); setError(null); setPreviewing(true);
    try {
      const result = await previewHandler(toDraft(form), Number(previewEvent));
      if (run === previewRun.current) setPreview(result);
    } catch (reason) {
      if (run === previewRun.current) setError(message(reason, "Could not preview the handler."));
    } finally { if (run === previewRun.current) setPreviewing(false); }
  }

  return <form onSubmit={save} className="flex flex-col gap-4 px-4 pb-4">
    <FieldGroup>
      <Field><FieldLabel htmlFor="handler-name">Name</FieldLabel><Input id="handler-name" value={form.name} onChange={e => update({ name: e.target.value })} /></Field>
      <Field orientation="horizontal"><Switch id="handler-enabled" checked={form.enabled} onCheckedChange={enabled => update({ enabled })} />
        <FieldLabel htmlFor="handler-enabled">Enabled</FieldLabel></Field>
      <Field><FieldLabel htmlFor="handler-pattern">Event type</FieldLabel>
        <Input id="handler-pattern" list="handler-types" value={form.type_pattern} onChange={e => update({ type_pattern: e.target.value })} />
        <datalist id="handler-types">{["*", "jira.ticket.*", ...types].map(type => <option key={type} value={type} />)}</datalist>
        <FieldDescription>Use * for any part, e.g. jira.ticket.* or jira.*.created.</FieldDescription></Field>
      <FieldSet><FieldLegend variant="label">Conditions</FieldLegend>
        <FieldDescription>All conditions must hold.</FieldDescription>
        <datalist id="handler-fields">{FIELD_SUGGESTIONS.map(field => <option key={field} value={field} />)}</datalist>
        {form.conditions.map((row, index) => <div key={index} className="flex flex-wrap items-center gap-2">
          <Input aria-label={`Condition ${index + 1} field`} list="handler-fields" className="w-40" value={row.field} onChange={e => setCondition(index, { field: e.target.value })} />
          <Select value={row.op} onValueChange={op => setCondition(index, { op: op as Operator })}>
            <SelectTrigger aria-label={`Condition ${index + 1} operator`} className="w-36"><SelectValue /></SelectTrigger>
            <SelectContent><SelectGroup>{(Object.keys(OPERATOR_LABELS) as Operator[]).map(op => <SelectItem key={op} value={op}>{OPERATOR_LABELS[op]}</SelectItem>)}</SelectGroup></SelectContent>
          </Select>
          {row.op !== "exists" && <Input aria-label={`Condition ${index + 1} value`} className="w-40" value={row.value}
            placeholder={row.op === "in" ? "a, b, c" : ""} onChange={e => setCondition(index, { value: e.target.value })} />}
          <Button type="button" variant="ghost" size="icon" aria-label={`Remove condition ${index + 1}`}
            onClick={() => update({ conditions: form.conditions.filter((_, at) => at !== index) })}><X /></Button>
        </div>)}
        {form.conditions.length < 20 && <Button type="button" variant="outline" size="sm" className="self-start"
          onClick={() => update({ conditions: [...form.conditions, { field: "", op: "equals", value: "" }] })}><Plus data-icon="inline-start" />Add condition</Button>}
      </FieldSet>
      <Field><FieldLabel htmlFor="handler-action">Action</FieldLabel>
        <Select value={form.action} onValueChange={action => update({ action: action as Form["action"] })}>
          <SelectTrigger id="handler-action" aria-label="Action" className="w-full"><SelectValue /></SelectTrigger>
          <SelectContent><SelectGroup><SelectItem value="emit">Emit event</SelectItem><SelectItem value="agent">Send to agent</SelectItem></SelectGroup></SelectContent>
        </Select></Field>
      {form.action === "emit" ? <>
        <Field><FieldLabel htmlFor="emit-type">New event type</FieldLabel><Input id="emit-type" placeholder="triage.requested" value={form.emit.type} onChange={e => setEmit("type", e.target.value)} /></Field>
        <Field><FieldLabel htmlFor="emit-subject">Subject</FieldLabel><Input id="emit-subject" placeholder="{{ event.subject }}" value={form.emit.subject} onChange={e => setEmit("subject", e.target.value)} /></Field>
        <Field><FieldLabel htmlFor="emit-title">Title</FieldLabel><Input id="emit-title" placeholder="{{ event.title }}" value={form.emit.title} onChange={e => setEmit("title", e.target.value)} /></Field>
        <Field><FieldLabel htmlFor="emit-payload">Payload (JSON template)</FieldLabel>
          <Textarea id="emit-payload" rows={5} className="font-mono text-xs" placeholder="{{ event.payload | tojson }}" value={form.emit.payload} onChange={e => setEmit("payload", e.target.value)} /></Field>
      </> : <>
        <Field><FieldLabel htmlFor="handler-agent">Agent</FieldLabel>
          <Select value={form.agent_id} onValueChange={agent_id => update({ agent_id })}>
            <SelectTrigger id="handler-agent" aria-label="Agent" className="w-full"><SelectValue placeholder="Choose an agent" /></SelectTrigger>
            <SelectContent><SelectGroup>{agents.map(agent => <SelectItem key={agent.id} value={agent.id}>{agent.name}</SelectItem>)}</SelectGroup></SelectContent>
          </Select>
          <FieldDescription>Enabled agents with fixed resources. Each matching event starts a new session.</FieldDescription></Field>
        <Field><FieldLabel htmlFor="handler-prompt">Prompt</FieldLabel>
          <Textarea id="handler-prompt" rows={5} value={form.prompt} placeholder="Triage {{ event.subject }}: {{ event.payload.summary }}" onChange={e => update({ prompt: e.target.value })} /></Field>
      </>}
      <Alert><AlertDescription><span className="font-medium">Template help.</span> Fields: {HELP}. In the payload, write values with
        {" "}<code>{"{{ event.payload.summary | tojson }}"}</code> so quotes stay valid JSON.</AlertDescription></Alert>
      <FieldSet><FieldLegend variant="label">Preview</FieldLegend>
        <div className="flex flex-wrap items-center gap-2">
          <Select value={previewEvent} onValueChange={setPreviewEvent}>
            <SelectTrigger aria-label="Preview event" className="w-64"><SelectValue placeholder="Choose a recent event" /></SelectTrigger>
            <SelectContent><SelectGroup>{recent.map(event => <SelectItem key={event.id} value={String(event.id)}>{event.subject} · {event.type}</SelectItem>)}</SelectGroup></SelectContent>
          </Select>
          <Button type="button" variant="outline" disabled={!previewEvent || previewing} onClick={() => void runPreview()}>{previewing && <Spinner data-icon="inline-start" />}Preview</Button>
        </div>
        {preview && <div className="flex flex-col gap-2 text-sm">
          <p className={preview.matches ? "font-medium" : "text-muted-foreground"}>{preview.matches ? "Matches" : `Doesn’t match: ${preview.reason}`}</p>
          {"error" in preview.output ? <Alert variant="destructive"><AlertDescription>{preview.output.error}</AlertDescription></Alert>
            : <pre className="max-h-64 overflow-auto rounded-md bg-muted p-3 text-xs">{"prompt" in preview.output ? preview.output.prompt : JSON.stringify(preview.output, null, 2)}</pre>}
        </div>}
      </FieldSet>
    </FieldGroup>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <SheetFooter className="p-0"><Button type="submit" disabled={busy}>{busy && <Spinner data-icon="inline-start" />}Save</Button></SheetFooter>
  </form>;
}

export default function HandlerSheet({ handler, open, onClose, onSaved }: { handler: EventHandler | null; open: boolean;
  onClose: () => void; onSaved: (handler: EventHandler) => void }) {
  return <Sheet open={open} onOpenChange={next => { if (!next) onClose(); }}>
    <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-xl">
      <SheetHeader><SheetTitle>{handler ? "Edit handler" : "New handler"}</SheetTitle>
        <SheetDescription>When an event matches, emit a new event or send it to an agent.</SheetDescription></SheetHeader>
      {open && <HandlerForm key={handler?.id ?? "new"} handler={handler} onSaved={onSaved} />}
    </SheetContent>
  </Sheet>;
}
