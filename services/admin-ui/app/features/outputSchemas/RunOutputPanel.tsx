import { useEffect, useState } from "react";
import { ApiError } from "~/lib/api";
import { getRunOutput, type RunOutput } from "./api";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";

/** A run's Output, for a run started with an output schema: the JSON it finished with (Copy), or the validation errors
 * and raw answer of one that did not match. Read again whenever the run's status or turns change; not asked while the
 * run works (the API answers 409 then). */
export default function RunOutputPanel({
  sessionId,
  status,
  turns,
}: {
  sessionId: string;
  status: string;
  turns: number;
}) {
  const working = status === "starting" || status === "working" || status === "retrying";
  const [output, setOutput] = useState<RunOutput | null>(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (working) return;
    const controller = new AbortController();
    setError("");
    setCopied(false);
    getRunOutput(sessionId, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setOutput(value);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e instanceof ApiError ? e.message : "Could not load the output.");
      });
    return () => controller.abort();
  }, [sessionId, status, turns, working]);
  const text = output?.valid ? JSON.stringify(output.output, null, 2) : "";
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      setError("Could not copy the output.");
    }
  }
  return (
    <section aria-label="Output" className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Output</h2>
        {output?.valid && !working && (
          <Button type="button" size="sm" variant="outline" onClick={() => void copy()}>
            {copied ? "Copied" : "Copy"}
          </Button>
        )}
      </div>
      {output && (
        <p className="text-xs text-muted-foreground">
          {output.schema.name ?? output.schema.id} · v{output.schema.version}
        </p>
      )}
      {working ? (
        <p className="text-sm text-muted-foreground">The output appears when the run finishes.</p>
      ) : error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : !output ? null : output.valid ? (
        <pre
          aria-label="Output JSON"
          tabIndex={0}
          className="max-h-96 overflow-auto rounded-md bg-muted p-2 text-xs whitespace-pre-wrap break-words"
        >
          {text}
        </pre>
      ) : output.errors ? (
        <>
          <Alert variant="destructive">
            <AlertTitle>Output did not match the schema</AlertTitle>
            <AlertDescription>
              <ul className="list-disc pl-4">
                {output.errors.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
          <p className="text-xs font-medium">Raw answer</p>
          <pre
            aria-label="Raw answer"
            tabIndex={0}
            className="max-h-64 overflow-auto rounded-md border p-2 text-xs whitespace-pre-wrap break-words"
          >
            {output.raw ?? ""}
          </pre>
        </>
      ) : (
        <p className="text-sm text-muted-foreground">The run ended without an output.</p>
      )}
    </section>
  );
}
