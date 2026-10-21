import { useEffect, useRef, useState, type FormEvent } from "react";
import { Plus, X } from "lucide-react";
import { ApiError } from "~/lib/api";
import { DEFAULT_SORT, NO_FILTERS, TYPE_LABELS, getEventFacets, getEvents, typeGroups, type EventRow } from "./api";
import { EMIT, getSchedules } from "./schedules";
import {
  FIELD_SUGGESTIONS,
  OPERATOR_LABELS,
  SCHEDULE_FIELD,
  SCHEDULE_FIRED,
  forJira,
  createHandler,
  getHandlerAgents,
  previewHandler,
  saveHandler,
  type EventHandler,
  type HandlerDraft,
  type Operator,
  type PreviewResult,
} from "./handlers";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Switch } from "~/components/ui/switch";
import { Textarea } from "~/components/ui/textarea";

interface ConditionRow {
  field: string;
  op: Operator;
  value: string;
}
interface Form {
  name: string;
  enabled: boolean;
  type_pattern: string;
  conditions: ConditionRow[];
  action: "emit" | "agent";
  emit: { type: string; subject: string; title: string; payload: string };
  /** The agent select's value: an agent id, or ASSIGNEE for the agent mapped to the assignee. */
  agent_id: string;
  prompt: string;
  reply_to_jira: boolean;
  give_back: boolean;
}

const ASSIGNEE = "@assignee";
const ANY_EVENT = "*";
const CUSTOM = "@custom";
const FOR_ASSIGNEE: ConditionRow = { field: "payload.for_assignee", op: "equals", value: "true" };
const BLANK: Form = {
  name: "",
  enabled: true,
  type_pattern: "jira.ticket.*",
  conditions: [],
  action: "emit",
  emit: { type: "", subject: "", title: "", payload: "" },
  agent_id: "",
  prompt: "",
  reply_to_jira: false,
  give_back: false,
};
/** Send to agent on a pattern like * or jira.ticket.* can run the agent many times at once (the first Jira pull records every ticket). */
const broad = (pattern: string) => pattern === "*" || pattern.endsWith(".*");
/** Give the ticket back is offered only for a Jira reply from the assignee's agent. */
/** The assignee's agent, as the agent select holds it, counts only on a Jira pattern (otherwise it is hidden). */
const toAssignee = (form: Form) => form.action === "agent" && form.agent_id === ASSIGNEE && forJira(form.type_pattern);
/** Reply in Jira is offered (and saved) only for Send to agent on a Jira pattern. */
const repliesInJira = (form: Form) => form.action === "agent" && forJira(form.type_pattern) && form.reply_to_jira;
const offersGiveBack = (form: Form) => toAssignee(form) && repliesInJira(form);
/** Run a schedule: type schedule.fired and the one subject equals <name> condition (other conditions stay). */
const scheduleConditions = (form: Form, name: string): Partial<Form> => ({
  type_pattern: SCHEDULE_FIRED,
  conditions: [
    ...form.conditions.filter((c) => !(c.field.trim() === SCHEDULE_FIELD && c.op === "equals")),
    { field: SCHEDULE_FIELD, op: "equals", value: name },
  ],
});
/** Add handler on Schedules: a Send to agent handler for that schedule; the person picks the agent and writes the task. */
const scheduleForm = (schedule: { id: string; name: string }): Form => ({
  ...BLANK,
  name: schedule.name,
  ...scheduleConditions(BLANK, schedule.name),
  action: "agent",
});
const HELP =
  "event.id, event.source, event.type, event.subject, event.title, event.occurred_at, event.payload.<field>, event.parent_id, event.root_id, event.depth";

function fromHandler(handler: EventHandler): Form {
  return {
    name: handler.name,
    enabled: handler.enabled,
    type_pattern: handler.type_pattern,
    action: handler.action,
    conditions: handler.conditions.map((c) => ({
      field: c.field,
      op: c.op,
      value: Array.isArray(c.value) ? c.value.join(", ") : (c.value ?? ""),
    })),
    emit: handler.emit ?? BLANK.emit,
    agent_id: handler.agent_from === "assignee" ? ASSIGNEE : (handler.agent_id ?? ""),
    prompt: handler.prompt ?? "",
    reply_to_jira: handler.reply_to_jira,
    give_back: handler.give_back,
  };
}
function toDraft(form: Form): HandlerDraft {
  return {
    name: form.name.trim(),
    enabled: form.enabled,
    type_pattern: form.type_pattern.trim(),
    action: form.action,
    conditions: form.conditions.map((c) => ({
      field: c.field.trim(),
      op: c.op,
      value:
        c.op === "exists"
          ? null
          : c.op === "in"
            ? c.value
                .split(",")
                .map((item) => item.trim())
                .filter(Boolean)
            : c.value,
    })),
    emit: form.action === "emit" ? form.emit : null,
    agent_from: toAssignee(form) ? "assignee" : "fixed",
    agent_id: form.action === "agent" && form.agent_id !== ASSIGNEE ? form.agent_id || null : null,
    prompt: form.action === "agent" ? form.prompt : null,
    reply_to_jira: repliesInJira(form),
    give_back: offersGiveBack(form) && form.give_back,
  };
}

function HandlerForm({
  handler,
  schedule,
  onSaved,
}: {
  handler: EventHandler | null;
  schedule?: { id: string; name: string };
  onSaved: (handler: EventHandler) => void;
}) {
  const [form, setForm] = useState<Form>(handler ? fromHandler(handler) : schedule ? scheduleForm(schedule) : BLANK);
  const [schedules, setSchedules] = useState<{ id: string; name: string }[]>([]);
  const [agents, setAgents] = useState<{ id: string; name: string }[] | null>(null);
  const [types, setTypes] = useState<string[]>([]);
  const [customPattern, setCustomPattern] = useState(false);
  const [recent, setRecent] = useState<EventRow[]>([]);
  const [previewEvent, setPreviewEvent] = useState("");
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const previewRun = useRef(0);

  useEffect(
    () => () => {
      previewRun.current += 1;
    },
    [],
  );
  useEffect(() => {
    getHandlerAgents()
      .then((result) => setAgents(result.agents))
      .catch(() => setAgents([]));
    getEventFacets()
      .then((result) => setTypes(result.types))
      .catch(() => setTypes([]));
    getSchedules()
      .then((result) => setSchedules(result.schedules.filter((item) => item.target_kind === EMIT)))
      .catch(() => setSchedules([]));
    getEvents(NO_FILTERS, DEFAULT_SORT)
      .then((result) => setRecent(result.events))
      .catch(() => setRecent([]));
  }, []);

  // A saved handler keeps its agent even when it is no longer offered (deleted or disabled): show it so the
  // handler can still be turned off or edited.
  const storedAgent =
    handler?.agent_id && !agents?.some((agent) => agent.id === handler.agent_id)
      ? [{ id: handler.agent_id, name: `${handler.agent_name ?? handler.agent_id}${agents ? " (not available)" : ""}` }]
      : [];
  // Give the ticket back clears when it stops being offered and, for a new handler, starts on when it is offered.
  const update = (changes: Partial<Form>) =>
    setForm((current) => {
      const next = { ...current, ...changes };
      const offered = offersGiveBack(next);
      return { ...next, give_back: offered && (offersGiveBack(current) ? next.give_back : !handler) };
    });
  const setCondition = (index: number, changes: Partial<ConditionRow>) =>
    update({ conditions: form.conditions.map((row, at) => (at === index ? { ...row, ...changes } : row)) });
  const setEmit = (field: keyof Form["emit"], value: string) => update({ emit: { ...form.emit, [field]: value } });
  const pickedSchedule =
    form.type_pattern.trim() === SCHEDULE_FIRED
      ? (form.conditions.find((c) => c.field.trim() === SCHEDULE_FIELD && c.op === "equals")?.value ?? "")
      : "";
  const groups = typeGroups(types);
  const listed =
    form.type_pattern === ANY_EVENT ||
    groups.some((g) => g.pattern === form.type_pattern) ||
    types.includes(form.type_pattern);
  const custom = customPattern || !listed;
  const jira = forJira(form.type_pattern);
  const missingForAssignee =
    toAssignee(form) && !form.conditions.some((row) => row.field.trim() === FOR_ASSIGNEE.field);
  const message = (reason: unknown, fallback: string) => (reason instanceof ApiError ? reason.message : fallback);

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const draft = toDraft(form);
      onSaved(
        (handler ? await saveHandler(handler.id, { ...draft, revision: handler.revision }) : await createHandler(draft))
          .handler,
      );
    } catch (reason) {
      setError(message(reason, "Could not save the handler."));
    } finally {
      setBusy(false);
    }
  }
  async function runPreview() {
    if (!previewEvent) return;
    const run = ++previewRun.current;
    setPreview(null);
    setError(null);
    setPreviewing(true);
    try {
      const result = await previewHandler(toDraft(form), Number(previewEvent));
      if (run === previewRun.current) setPreview(result);
    } catch (reason) {
      if (run === previewRun.current) setError(message(reason, "Could not preview the handler."));
    } finally {
      if (run === previewRun.current) setPreviewing(false);
    }
  }

  return (
    <form onSubmit={save} className="flex flex-col gap-4 px-4 pb-4">
      <FieldGroup>
        <Field>
          <FieldLabel htmlFor="handler-name">Name</FieldLabel>
          <Input id="handler-name" value={form.name} onChange={(e) => update({ name: e.target.value })} />
        </Field>
        <Field orientation="horizontal">
          <Switch id="handler-enabled" checked={form.enabled} onCheckedChange={(enabled) => update({ enabled })} />
          <FieldLabel htmlFor="handler-enabled">Enabled</FieldLabel>
        </Field>
        <Field>
          <FieldLabel htmlFor="handler-pattern">Event type</FieldLabel>
          <Select
            value={custom ? CUSTOM : form.type_pattern}
            onValueChange={(value) => {
              if (!value) return; // Radix reports "" while the value is not among the items yet
              setCustomPattern(value === CUSTOM);
              if (value !== CUSTOM) update({ type_pattern: value });
            }}
          >
            <SelectTrigger id="handler-pattern" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                <SelectItem value={ANY_EVENT}>Any event</SelectItem>
                {groups.map((group) => (
                  <SelectItem key={group.pattern} value={group.pattern}>
                    {group.label}
                  </SelectItem>
                ))}
              </SelectGroup>
              <SelectGroup>
                {types.map((type) => (
                  <SelectItem key={type} value={type}>
                    {TYPE_LABELS[type] ?? type}
                  </SelectItem>
                ))}
              </SelectGroup>
              <SelectGroup>
                <SelectItem value={CUSTOM}>Custom pattern…</SelectItem>
              </SelectGroup>
            </SelectContent>
          </Select>
          {custom && (
            <>
              <Input
                aria-label="Event type pattern"
                value={form.type_pattern}
                onChange={(e) => update({ type_pattern: e.target.value })}
              />
              <FieldDescription>Use * for any part, e.g. jira.ticket.* or jira.*.created.</FieldDescription>
            </>
          )}
          {schedules.length > 0 && (
            <Select
              value={schedules.some((item) => item.name === pickedSchedule) ? pickedSchedule : ""}
              onValueChange={(name) => update(scheduleConditions(form, name))}
            >
              <SelectTrigger aria-label="Schedule" className="w-full">
                <SelectValue placeholder="Or run a schedule" />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {schedules.map((item) => (
                    <SelectItem key={item.id} value={item.name}>
                      Schedule: {item.name}
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
          )}
        </Field>
        <FieldSet>
          <FieldLegend variant="label">Conditions</FieldLegend>
          <FieldDescription>
            All conditions must hold. For “is one of”, separate the values with commas.
          </FieldDescription>
          <datalist id="handler-fields">
            {FIELD_SUGGESTIONS.map((field) => (
              <option key={field} value={field} />
            ))}
          </datalist>
          {form.conditions.map((row, index) => (
            <div key={index} className="flex flex-wrap items-center gap-2">
              <Input
                aria-label={`Condition ${index + 1} field`}
                list="handler-fields"
                className="w-40"
                value={row.field}
                onChange={(e) => setCondition(index, { field: e.target.value })}
              />
              <Select value={row.op} onValueChange={(op) => setCondition(index, { op: op as Operator })}>
                <SelectTrigger aria-label={`Condition ${index + 1} operator`} className="w-36">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    {(Object.keys(OPERATOR_LABELS) as Operator[]).map((op) => (
                      <SelectItem key={op} value={op}>
                        {OPERATOR_LABELS[op]}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
              {row.op !== "exists" && (
                <Input
                  aria-label={`Condition ${index + 1} value`}
                  className="w-40"
                  value={row.value}
                  placeholder={row.op === "in" ? "a, b, c" : ""}
                  onChange={(e) => setCondition(index, { value: e.target.value })}
                />
              )}
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={`Remove condition ${index + 1}`}
                onClick={() => update({ conditions: form.conditions.filter((_, at) => at !== index) })}
              >
                <X />
              </Button>
            </div>
          ))}
          {form.conditions.length < 20 && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="self-start"
              onClick={() => update({ conditions: [...form.conditions, { field: "", op: "equals", value: "" }] })}
            >
              <Plus data-icon="inline-start" />
              Add condition
            </Button>
          )}
        </FieldSet>
        <Field>
          <FieldLabel htmlFor="handler-action">Action</FieldLabel>
          <Select value={form.action} onValueChange={(action) => update({ action: action as Form["action"] })}>
            <SelectTrigger id="handler-action" aria-label="Action" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                <SelectItem value="emit">Emit event</SelectItem>
                <SelectItem value="agent">Send to agent</SelectItem>
              </SelectGroup>
            </SelectContent>
          </Select>
          {form.action === "agent" && broad(form.type_pattern.trim()) && (
            <FieldDescription className="text-amber-700 dark:text-amber-400">
              Runs the agent once for every matching event.
              {repliesInJira(form) &&
                " The agent also comments on every matching Jira ticket: the first scheduled pull records every ticket as created."}
            </FieldDescription>
          )}
        </Field>
        {form.action === "emit" ? (
          <>
            <Field>
              <FieldLabel htmlFor="emit-type">New event type</FieldLabel>
              <Input
                id="emit-type"
                placeholder="triage.requested"
                value={form.emit.type}
                onChange={(e) => setEmit("type", e.target.value)}
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="emit-subject">Subject</FieldLabel>
              <Input
                id="emit-subject"
                placeholder="{{ event.subject }}"
                value={form.emit.subject}
                onChange={(e) => setEmit("subject", e.target.value)}
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="emit-title">Title</FieldLabel>
              <Input
                id="emit-title"
                placeholder="{{ event.title }}"
                value={form.emit.title}
                onChange={(e) => setEmit("title", e.target.value)}
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="emit-payload">Payload (JSON template)</FieldLabel>
              <Textarea
                id="emit-payload"
                rows={5}
                className="font-mono text-xs"
                placeholder="{{ event.payload | tojson }}"
                value={form.emit.payload}
                onChange={(e) => setEmit("payload", e.target.value)}
              />
            </Field>
          </>
        ) : (
          <>
            <Field>
              <FieldLabel htmlFor="handler-agent">Agent</FieldLabel>
              <Select value={form.agent_id} onValueChange={(agent_id) => update({ agent_id })}>
                <SelectTrigger id="handler-agent" aria-label="Agent" className="w-full">
                  <SelectValue placeholder="Choose an agent" />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    {[...storedAgent, ...(agents ?? [])].map((agent) => (
                      <SelectItem key={agent.id} value={agent.id}>
                        {agent.name}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                  {jira && (
                    <SelectGroup>
                      <SelectItem value={ASSIGNEE}>Agent mapped to the assignee</SelectItem>
                    </SelectGroup>
                  )}
                </SelectContent>
              </Select>
              <FieldDescription>
                Enabled agents with fixed resources. The agent answers once, without a conversation. The answer goes to
                Notifications.
              </FieldDescription>
              {toAssignee(form) && (
                <FieldDescription>
                  Jira events only: runs the agent mapped to the ticket&apos;s assignee in Jira → Settings → Users and
                  agents. A ticket that is unassigned or assigned to an unmapped user starts no run.
                </FieldDescription>
              )}
              {missingForAssignee && (
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="text-muted-foreground">
                    Usually with a condition, so the agent starts only when the assignee has something new:
                  </span>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={form.conditions.length >= 20}
                    onClick={() => update({ conditions: [...form.conditions, FOR_ASSIGNEE] })}
                  >
                    <Plus data-icon="inline-start" />
                    Add payload.for_assignee equals true
                  </Button>
                </div>
              )}
            </Field>
            <Field>
              <FieldLabel htmlFor="handler-prompt">Prompt</FieldLabel>
              <Textarea
                id="handler-prompt"
                rows={5}
                value={form.prompt}
                placeholder="Triage {{ event.subject }}: {{ event.payload.summary }}"
                onChange={(e) => update({ prompt: e.target.value })}
              />
            </Field>
            {jira && (
              <Field orientation="horizontal">
                <Checkbox
                  id="handler-reply-to-jira"
                  checked={form.reply_to_jira}
                  onCheckedChange={(checked) => update({ reply_to_jira: checked === true })}
                />
                <div>
                  <FieldLabel htmlFor="handler-reply-to-jira">
                    Also post the answer as a comment on the Jira ticket
                  </FieldLabel>
                  <FieldDescription>
                    Jira events only. Posted as the Jira connection&apos;s user, which needs the Add Comments
                    permission.
                  </FieldDescription>
                </div>
              </Field>
            )}
            {offersGiveBack(form) && (
              <Field orientation="horizontal">
                <Checkbox
                  id="handler-give-back"
                  checked={form.give_back}
                  onCheckedChange={(checked) => update({ give_back: checked === true })}
                />
                <div>
                  <FieldLabel htmlFor="handler-give-back">Give the ticket back after replying</FieldLabel>
                  <FieldDescription>
                    After the comment is posted, the ticket is assigned to its previous assignee, else its reporter (not
                    to a user mapped to an agent). Skipped when someone else took the ticket meanwhile. The
                    connection&apos;s user needs the Assign Issues permission.
                  </FieldDescription>
                </div>
              </Field>
            )}
          </>
        )}
        <Alert>
          <AlertDescription>
            <span className="font-medium">Template help.</span> Fields: {HELP}. In the payload, write values with{" "}
            <code>{"{{ event.payload.summary | tojson }}"}</code> so quotes stay valid JSON.
          </AlertDescription>
        </Alert>
        <FieldSet>
          <FieldLegend variant="label">Preview</FieldLegend>
          <div className="flex flex-wrap items-center gap-2">
            <Select value={previewEvent} onValueChange={setPreviewEvent}>
              <SelectTrigger aria-label="Preview event" className="w-64">
                <SelectValue placeholder="Choose a recent event" />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {recent.map((event) => (
                    <SelectItem key={event.id} value={String(event.id)}>
                      {event.subject} · {event.type}
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
            <Button
              type="button"
              variant="outline"
              disabled={!previewEvent || previewing}
              onClick={() => void runPreview()}
            >
              {previewing && <Spinner data-icon="inline-start" />}Preview
            </Button>
          </div>
          {preview && (
            <div className="flex flex-col gap-2 text-sm">
              <p className={preview.matches ? "font-medium" : "text-muted-foreground"}>
                {preview.matches ? "Matches" : `Doesn’t match: ${preview.reason}`}
              </p>
              {preview.target && (
                <p>
                  {"error" in preview.target ? `No run: ${preview.target.error}` : `Runs ${preview.target.agent_name}`}
                </p>
              )}
              {"error" in preview.output ? (
                <Alert variant="destructive">
                  <AlertDescription>{preview.output.error}</AlertDescription>
                </Alert>
              ) : (
                <pre className="max-h-64 overflow-auto rounded-md bg-muted p-3 text-xs">
                  {"prompt" in preview.output ? preview.output.prompt : JSON.stringify(preview.output, null, 2)}
                </pre>
              )}
            </div>
          )}
        </FieldSet>
      </FieldGroup>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <SheetFooter className="p-0">
        <Button type="submit" disabled={busy}>
          {busy && <Spinner data-icon="inline-start" />}Save
        </Button>
      </SheetFooter>
    </form>
  );
}

/** schedule: a new handler starts prefilled to run that Emit event schedule (Schedules → Add handler). */
export default function HandlerSheet({
  handler,
  schedule,
  open,
  onClose,
  onSaved,
}: {
  handler: EventHandler | null;
  schedule?: { id: string; name: string };
  open: boolean;
  onClose: () => void;
  onSaved: (handler: EventHandler) => void;
}) {
  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>{handler ? "Edit handler" : "New handler"}</SheetTitle>
          <SheetDescription>When an event matches, emit a new event or send it to an agent.</SheetDescription>
        </SheetHeader>
        {open && (
          <HandlerForm
            key={handler?.id ?? `new-${schedule?.id ?? ""}`}
            handler={handler}
            schedule={schedule}
            onSaved={onSaved}
          />
        )}
      </SheetContent>
    </Sheet>
  );
}
