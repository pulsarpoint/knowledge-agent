import { Fragment, useEffect, useState, type ReactNode } from "react";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import { Input } from "~/components/ui/input";
import { Label } from "~/components/ui/label";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Skeleton } from "~/components/ui/skeleton";
import {
  fieldKind,
  formArgs,
  getExploreMethods,
  runExplore,
  type DigestResult,
  type ExploreMethod,
  type ExploreResult,
  type JsonSchema,
} from "./explore";

const message = (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback);
const number = (value: number) => value.toLocaleString("en-US");
const CALL_ID = /("call_id": ")([^"]+)(")/g;

/** Pretty JSON where each call id is a button that opens it in tool_output. */
function ResultJson({ value, onCall }: { value: unknown; onCall: (callId: string) => void }) {
  const text = JSON.stringify(value, null, 2);
  const parts: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(CALL_ID)) {
    const start = match.index + match[1].length;
    parts.push(text.slice(last, start));
    parts.push(
      <button
        key={start}
        type="button"
        className="underline"
        title="Open this call's output"
        onClick={() => onCall(match[2])}
      >
        {match[2]}
      </button>,
    );
    last = start + match[2].length;
  }
  parts.push(text.slice(last));
  return (
    <Block>
      {parts.map((part, index) => (typeof part === "string" ? <Fragment key={index}>{part}</Fragment> : part))}
    </Block>
  );
}

function Block({ children }: { children: ReactNode }) {
  return (
    <pre className="max-h-[40rem] overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted p-3 text-xs">
      {children}
    </pre>
  );
}

/** One argument of the form, from its JSON schema: text, number, checkbox, or checkboxes for a list of choices. */
function ArgumentField({
  name,
  schema,
  value,
  onChange,
}: {
  name: string;
  schema: JsonSchema;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const { kind, choices } = fieldKind(schema);
  const id = `explore-${name}`;
  const help = schema.description ? <p className="text-xs text-muted-foreground">{schema.description}</p> : null;
  if (kind === "checkbox")
    return (
      <div className="flex flex-col gap-1">
        <div className="flex items-center gap-2">
          <Checkbox id={id} checked={value === true} onCheckedChange={(checked) => onChange(checked === true)} />
          <Label htmlFor={id}>{name}</Label>
        </div>
        {help}
      </div>
    );
  if (kind === "choices") {
    const picked = Array.isArray(value) ? (value as string[]) : [];
    return (
      <fieldset className="flex flex-col gap-1">
        <legend className="text-sm font-medium">{name}</legend>
        <div className="flex flex-wrap gap-3">
          {choices.map((choice) => (
            <div key={choice} className="flex items-center gap-1.5">
              <Checkbox
                id={`${id}-${choice}`}
                checked={picked.includes(choice)}
                onCheckedChange={(checked) =>
                  onChange(checked === true ? [...picked, choice] : picked.filter((item) => item !== choice))
                }
              />
              <Label htmlFor={`${id}-${choice}`} className="font-normal">
                {choice}
              </Label>
            </div>
          ))}
        </div>
        {help}
      </fieldset>
    );
  }
  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor={id}>{name}</Label>
      <Input
        id={id}
        type={kind === "number" ? "number" : "text"}
        value={typeof value === "string" ? value : ""}
        onChange={(event) => onChange(event.target.value)}
      />
      {help}
    </div>
  );
}

/** The Debug sub-page of a run: the session explorer's methods (the tools a reviewer model gets) run on this record.
 * At the top the digest's size against the full run's; then a method picker, a form generated from the method's
 * schema, Run, the result with its size and the schema the model sees. A call id in a result opens tool_output. */
export default function RunDebugView({ runKey }: { runKey: string }) {
  const [methods, setMethods] = useState<ExploreMethod[] | null>(null);
  const [digest, setDigest] = useState<ExploreResult | null>(null);
  const [name, setName] = useState("overview");
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [result, setResult] = useState<{ method: string; value: ExploreResult } | null>(null);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    getExploreMethods(runKey, controller.signal)
      .then((data) => !controller.signal.aborted && setMethods(data.methods))
      .catch((e) => !controller.signal.aborted && setError(message(e, "Could not load the explorer.")));
    runExplore(runKey, "digest", {}, controller.signal)
      .then((data) => !controller.signal.aborted && setDigest(data))
      .catch(() => {});
    return () => controller.abort();
  }, [runKey]);

  if (!methods)
    return error ? (
      <Alert variant="destructive">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    ) : (
      <Skeleton className="h-64 w-full" />
    );
  const method = methods.find((item) => item.method === name) ?? methods[0];
  const pick = (next: string, nextValues: Record<string, unknown> = {}) => {
    setName(next);
    setValues(nextValues);
    setError("");
  };
  const run = async () => {
    setRunning(true);
    setError("");
    try {
      setResult({
        method: method.method,
        value: await runExplore(runKey, method.method, formArgs(method.parameters, values)),
      });
    } catch (e) {
      setError(message(e, "The method failed."));
    } finally {
      setRunning(false);
    }
  };
  const digestValue = digest?.result as DigestResult | undefined;
  const shown = result?.value.result as DigestResult | undefined;
  return (
    <div className="flex flex-col gap-4">
      {digestValue && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border p-3 text-sm">
          <span className="font-medium">Digest</span>
          <span className="text-muted-foreground">
            {number(digestValue.size_chars)} chars of {number(digestValue.full_chars)} in the full run (
            {Math.round((100 * digestValue.size_chars) / Math.max(1, digestValue.full_chars))}%) · ~
            {number(digest!.approx_tokens)} tokens
            {digestValue.shortened_turns.length > 0 && ` · ${digestValue.shortened_turns.length} turns shortened`}
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              pick("digest");
              setResult({ method: "digest", value: digest! });
            }}
          >
            Show digest
          </Button>
        </div>
      )}
      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <Label htmlFor="explore-method">Method</Label>
          <Select value={method.method} onValueChange={(next) => pick(next)}>
            <SelectTrigger id="explore-method" className="w-full sm:w-72">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                {methods.map((item) => (
                  <SelectItem key={item.method} value={item.method}>
                    {item.method}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
          <p className="text-sm text-muted-foreground">{method.description}</p>
        </div>
        {Object.entries(method.parameters.properties ?? {}).map(([field, schema]) => (
          <ArgumentField
            key={`${method.method}-${field}`}
            name={field}
            schema={schema}
            value={values[field]}
            onChange={(value) => setValues((current) => ({ ...current, [field]: value }))}
          />
        ))}
        <Button className="self-start" onClick={run} disabled={running}>
          Run
        </Button>
        <details>
          <summary className="cursor-pointer text-sm">Tool schema the LLM sees</summary>
          <Block>
            {JSON.stringify(
              { name: method.tool, description: method.description, parameters: method.parameters },
              null,
              2,
            )}
          </Block>
        </details>
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {result && (
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2 text-sm">
            <span className="font-medium">{result.method}</span>
            <Badge variant="secondary">
              {number(result.value.size_chars)} chars · ~{number(result.value.approx_tokens)} tokens
            </Badge>
          </div>
          {result.method === "digest" && typeof shown?.text === "string" ? (
            <Block>{shown.text}</Block>
          ) : (
            <ResultJson value={result.value.result} onCall={(callId) => pick("tool_output", { call_id: callId })} />
          )}
        </div>
      )}
    </div>
  );
}
