import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { History } from "lucide-react";
import { ApiError } from "~/lib/api";
import {
  checkOutputSchema,
  createOutputSchema,
  deleteOutputSchema,
  getOutputSchema,
  getOutputSchemaVersion,
  parseJson,
  saveOutputSchema,
  type OutputSchema,
} from "./api";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "~/components/ui/alert-dialog";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button, buttonVariants } from "~/components/ui/button";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Skeleton } from "~/components/ui/skeleton";
import { Spinner } from "~/components/ui/spinner";
import { Textarea } from "~/components/ui/textarea";

const pretty = (value: unknown) => JSON.stringify(value, null, 2);
const STARTER = pretty({
  type: "object",
  properties: { summary: { type: "string", description: "One sentence." } },
  required: ["summary"],
  additionalProperties: false,
});
const when = (value: string) => new Date(value).toLocaleString();
const message = (reason: unknown, fallback: string) => (reason instanceof ApiError ? reason.message : fallback);

/** A check's outcome: accepted (ok, with its text) or the lines that explain why not. */
type Outcome = { ok: true; text: string } | { ok: false; lines: string[] };

function OutcomeView({ label, outcome }: { label: string; outcome: Outcome | null }) {
  if (!outcome) return null;
  return outcome.ok ? (
    <p aria-label={label} className="text-sm text-green-700 dark:text-green-400">
      {outcome.text}
    </p>
  ) : (
    <Alert variant="destructive" aria-label={label}>
      <AlertDescription>
        <ul className="list-disc pl-4">
          {outcome.lines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  );
}

/** The editor of one output schema (/output-schemas/new: a new one): name, description, the JSON with Check, a sample
 * with Validate sample, the versions in a side sheet, delete with a confirmation. */
export default function OutputSchemaEditorView() {
  const { schemaId } = useParams();
  const navigate = useNavigate();
  const [entry, setEntry] = useState<OutputSchema | null>(null);
  const [loading, setLoading] = useState(Boolean(schemaId));
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [schemaText, setSchemaText] = useState(STARTER);
  const [sampleText, setSampleText] = useState("");
  const [check, setCheck] = useState<Outcome | null>(null);
  const [sample, setSample] = useState<Outcome | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [viewed, setViewed] = useState<{ version: number; text: string } | null>(null);

  const show = (value: OutputSchema) => {
    setEntry(value);
    setName(value.name);
    setDescription(value.description);
    setSchemaText(pretty(value.schema));
  };
  useEffect(() => {
    if (!schemaId) return;
    const controller = new AbortController();
    setLoading(true);
    getOutputSchema(schemaId, controller.signal)
      .then((result) => {
        show(result.schema);
        setError("");
        setLoading(false);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setError(message(reason, "Could not load the output schema."));
          setLoading(false);
        }
      });
    return () => controller.abort();
  }, [schemaId]);

  async function run(action: () => Promise<void>, fallback: string) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (reason) {
      setError(message(reason, fallback));
    } finally {
      setBusy(false);
    }
  }
  function parsedSchema(): unknown | undefined {
    const parsed = parseJson(schemaText);
    if ("error" in parsed) {
      setCheck({ ok: false, lines: [parsed.error] });
      return undefined;
    }
    return parsed.value;
  }
  const runCheck = () => {
    setSample(null);
    const schema = parsedSchema();
    if (schema === undefined) return;
    void run(async () => {
      const result = await checkOutputSchema(schema);
      setCheck(
        result.problems.length ? { ok: false, lines: result.problems } : { ok: true, text: "The schema is accepted." },
      );
    }, "Could not check the schema.");
  };
  const runSample = () => {
    setCheck(null);
    const schema = parsedSchema();
    if (schema === undefined) return;
    const parsed = parseJson(sampleText);
    if ("error" in parsed) {
      setSample({ ok: false, lines: [parsed.error] });
      return;
    }
    void run(async () => {
      const result = await checkOutputSchema(schema, parsed);
      if (result.problems.length) {
        setCheck({ ok: false, lines: result.problems });
        setSample({ ok: false, lines: ["Fix the schema first."] });
      } else if (result.sample_errors?.length) setSample({ ok: false, lines: result.sample_errors });
      else setSample({ ok: true, text: "The sample matches the schema." });
    }, "Could not validate the sample.");
  };
  function save(event: FormEvent) {
    event.preventDefault();
    const schema = parsedSchema();
    if (schema === undefined) return;
    const draft = { name: name.trim(), description: description.trim(), schema };
    void run(async () => {
      if (entry) show((await saveOutputSchema(entry.id, draft, entry.revision)).schema);
      else {
        const created = (await createOutputSchema(draft)).schema;
        navigate(`/output-schemas/${encodeURIComponent(created.id)}`, { replace: true });
      }
    }, "Could not save the output schema.");
  }
  const remove = () =>
    entry &&
    void run(async () => {
      await deleteOutputSchema(entry.id, entry.revision);
      navigate("/output-schemas");
    }, "Could not delete the output schema.");
  const view = (version: number) =>
    entry &&
    void run(async () => {
      const result = await getOutputSchemaVersion(entry.id, version);
      setViewed({ version, text: pretty(result.version.schema) });
    }, "Could not load the version.");

  if (loading) return <Skeleton className="h-64 w-full" />;
  return (
    <form onSubmit={save} className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-col gap-1">
          <h2 className="text-xl font-semibold">{entry ? entry.name : "New output schema"}</h2>
          {entry && (
            <p className="text-sm text-muted-foreground">
              Version {entry.current_version} · Used by: {entry.used_by.join(", ") || "no handlers"}
            </p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="ghost" asChild>
            <Link to="/output-schemas">All schemas</Link>
          </Button>
          {entry && (
            <Button type="button" variant="outline" onClick={() => setVersionsOpen(true)}>
              <History data-icon="inline-start" />
              Versions ({entry.versions.length})
            </Button>
          )}
        </div>
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <FieldGroup>
        <Field>
          <FieldLabel htmlFor="schema-name">Name</FieldLabel>
          <Input id="schema-name" maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field>
          <FieldLabel htmlFor="schema-description">Description</FieldLabel>
          <Input
            id="schema-description"
            maxLength={2000}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </Field>
        <div className="grid gap-4 lg:grid-cols-2">
          <Field>
            <FieldLabel htmlFor="schema-json">Schema (JSON)</FieldLabel>
            <Textarea
              id="schema-json"
              rows={20}
              spellCheck={false}
              className="font-mono text-xs"
              value={schemaText}
              onChange={(e) => {
                setSchemaText(e.target.value);
                setCheck(null);
              }}
            />
            <FieldDescription>
              The root is an object; every object lists all its properties in required and sets additionalProperties to
              false. Types: object, array, string, number, integer, boolean, null; enum and description. Saving a
              changed schema adds a version.
            </FieldDescription>
            <div>
              <Button type="button" variant="outline" disabled={busy} onClick={runCheck}>
                Check
              </Button>
            </div>
            <OutcomeView label="Check result" outcome={check} />
          </Field>
          <Field>
            <FieldLabel htmlFor="schema-sample">Sample (JSON)</FieldLabel>
            <Textarea
              id="schema-sample"
              rows={20}
              spellCheck={false}
              className="font-mono text-xs"
              placeholder='{"summary": "The deploy failed on a missing variable."}'
              value={sampleText}
              onChange={(e) => {
                setSampleText(e.target.value);
                setSample(null);
              }}
            />
            <FieldDescription>
              An answer a run could give; Validate sample checks it against the schema.
            </FieldDescription>
            <div>
              <Button type="button" variant="outline" disabled={busy || !sampleText.trim()} onClick={runSample}>
                Validate sample
              </Button>
            </div>
            <OutcomeView label="Sample result" outcome={sample} />
          </Field>
        </div>
      </FieldGroup>
      <div className="flex justify-between gap-2">
        <Button type="submit" disabled={busy}>
          {busy && <Spinner data-icon="inline-start" />}
          Save
        </Button>
        {entry && (
          <Button type="button" variant="destructive" disabled={busy} onClick={() => setConfirmDelete(true)}>
            Delete
          </Button>
        )}
      </div>
      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {entry?.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              Its versions are deleted too. Runs that used it keep their output. A schema a handler uses cannot be
              deleted.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction className={buttonVariants({ variant: "destructive" })} onClick={remove}>
              Delete schema
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      {entry && (
        <Sheet
          open={versionsOpen}
          onOpenChange={(open) => {
            setVersionsOpen(open);
            if (!open) setViewed(null);
          }}
        >
          <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
            <SheetHeader>
              <SheetTitle>Versions of {entry.name}</SheetTitle>
              <SheetDescription>
                Every saved change is a version. Runs keep the version they started with.
              </SheetDescription>
            </SheetHeader>
            <ul className="flex flex-col gap-2 px-4 pb-4">
              {entry.versions.map((item) => (
                <li key={item.version} className="flex flex-col gap-2 rounded-md border p-3 text-sm">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span>
                      v{item.version} · {when(item.created_at)}
                      {item.created_by_email && ` · ${item.created_by_email}`}
                      {item.version === entry.current_version && (
                        <Badge variant="secondary" className="ml-2">
                          Current
                        </Badge>
                      )}
                    </span>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={busy}
                      aria-label={`View version ${item.version}`}
                      onClick={() => view(item.version)}
                    >
                      View
                    </Button>
                  </div>
                  {viewed?.version === item.version && (
                    <>
                      <pre
                        aria-label={`Version ${item.version} JSON`}
                        tabIndex={0}
                        className="max-h-80 overflow-auto rounded-md bg-muted p-2 text-xs"
                      >
                        {viewed.text}
                      </pre>
                      <div>
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          onClick={() => {
                            setSchemaText(viewed.text);
                            setCheck(null);
                            setVersionsOpen(false);
                            setViewed(null);
                          }}
                        >
                          Copy to editor
                        </Button>
                      </div>
                    </>
                  )}
                </li>
              ))}
            </ul>
          </SheetContent>
        </Sheet>
      )}
    </form>
  );
}
