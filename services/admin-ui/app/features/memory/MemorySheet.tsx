import { useEffect, useState, type FormEvent } from "react";
import { Plus, X } from "lucide-react";
import { ApiError } from "~/lib/api";
import {
  createMemory,
  deleteMemory,
  getMemory,
  restoreMemory,
  saveMemory,
  isEmbedded,
  savedBy,
  STALE_MESSAGE,
  type MemoryDraft,
  type MemoryEntry,
  type MemoryVersion,
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
import { Button, buttonVariants } from "~/components/ui/button";
import { Field, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Skeleton } from "~/components/ui/skeleton";
import { Spinner } from "~/components/ui/spinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { Textarea } from "~/components/ui/textarea";

const when = (value: string) => new Date(value).toLocaleString();
const stale = (reason: unknown) =>
  reason instanceof ApiError && reason.status === 409 && reason.message === STALE_MESSAGE;
const text = (reason: unknown, fallback: string) => (reason instanceof ApiError ? reason.message : fallback);

const MAX_DIFF_LINES = 2000;
/** Whether either text is too long for the quadratic line diff. */
export const tooLongToCompare = (before: string, after: string) =>
  before.split("\n").length > MAX_DIFF_LINES || after.split("\n").length > MAX_DIFF_LINES;

export interface DiffLine {
  type: "same" | "add" | "del";
  text: string;
}
/** A line diff from `before` to `after` (longest common subsequence): "add" lines are only in after, "del" only in before. */
export function lineDiff(before: string, after: string): DiffLine[] {
  const a = before.split("\n");
  const b = after.split("\n");
  const table = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--)
    for (let j = b.length - 1; j >= 0; j--)
      table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
  const lines: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) (lines.push({ type: "same", text: a[i++] }), j++);
    else if (table[i + 1][j] >= table[i][j + 1]) lines.push({ type: "del", text: a[i++] });
    else lines.push({ type: "add", text: b[j++] });
  }
  while (i < a.length) lines.push({ type: "del", text: a[i++] });
  while (j < b.length) lines.push({ type: "add", text: b[j++] });
  return lines;
}

interface Row {
  key: string;
  value: string;
}
const toRows = (metadata: Record<string, string>): Row[] =>
  Object.entries(metadata).map(([key, value]) => ({ key, value }));
const toDraft = (name: string, rows: Row[], content: string): MemoryDraft => ({
  name: name.trim(),
  metadata: Object.fromEntries(rows.filter((row) => row.key.trim()).map((row) => [row.key.trim(), row.value])),
  content,
});

function Diff({ from, to }: { from: string; to: string }) {
  if (tooLongToCompare(from, to))
    return (
      <div className="flex flex-col gap-1">
        <p className="text-xs text-muted-foreground">Too long to compare line by line.</p>
        <pre className="max-h-48 overflow-auto rounded-md bg-muted p-2 text-xs">{to}</pre>
      </div>
    );
  return (
    <pre className="max-h-48 overflow-auto rounded-md bg-muted p-2 text-xs">
      {lineDiff(from, to).map((line, index) => (
        <div
          key={index}
          className={
            line.type === "add"
              ? "text-green-700 dark:text-green-400"
              : line.type === "del"
                ? "text-red-700 dark:text-red-400"
                : "text-muted-foreground"
          }
        >
          {line.type === "add" ? "+ " : line.type === "del" ? "− " : "  "}
          {line.text}
        </div>
      ))}
    </pre>
  );
}

function SheetBody({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const [entryId, setEntryId] = useState<string | null>(id === "new" ? null : id);
  const [entry, setEntry] = useState<MemoryEntry | null>(null);
  const [versions, setVersions] = useState<MemoryVersion[]>([]);
  const [loading, setLoading] = useState(id !== "new");
  const [name, setName] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [content, setContent] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [reload, setReload] = useState(0);
  const [currentProfile, setCurrentProfile] = useState<string | null>(null);

  // Loads the entry into the form (again after a save, a restore or Load current).
  useEffect(() => {
    if (!entryId) return;
    const controller = new AbortController();
    setLoading(true);
    getMemory(entryId, controller.signal)
      .then(({ memory, versions: earlier, current_profile }) => {
        setEntry(memory);
        setCurrentProfile(current_profile);
        setVersions(earlier);
        setName(memory.name);
        setRows(toRows(memory.metadata));
        setContent(memory.content);
        setError(null);
        setConflict(false);
        setLoading(false);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setError(text(reason, "Could not load the entry."));
          setLoading(false);
        }
      });
    return () => controller.abort();
  }, [entryId, reload]);

  const setRow = (index: number, changes: Partial<Row>) =>
    setRows((current) => current.map((row, at) => (at === index ? { ...row, ...changes } : row)));

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setConflict(false);
    try {
      const draft = toDraft(name, rows, content);
      const saved = entry ? await saveMemory(entry.id, draft, entry.revision) : await createMemory(draft);
      onChanged();
      setEntryId(saved.memory.id);
      setReload((count) => count + 1);
    } catch (reason) {
      setError(text(reason, "Could not save the entry."));
      setConflict(stale(reason));
    } finally {
      setBusy(false);
    }
  }
  async function restore(version: MemoryVersion) {
    if (!entry) return;
    setBusy(true);
    setError(null);
    setConflict(false);
    try {
      await restoreMemory(entry.id, version.revision, entry.revision);
      onChanged();
      setReload((count) => count + 1);
    } catch (reason) {
      setError(text(reason, "Could not restore the version."));
      setConflict(stale(reason));
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    if (!entry) return;
    setBusy(true);
    try {
      await deleteMemory(entry.id, entry.revision);
      onChanged();
      onClose();
    } catch (reason) {
      setError(text(reason, "Could not delete the entry."));
      setBusy(false);
    }
  }

  const errorAlert = error && (
    <Alert variant="destructive">
      <AlertDescription className="flex flex-wrap items-center gap-2">
        {error}
        {conflict && (
          <Button type="button" size="sm" variant="outline" onClick={() => setReload((count) => count + 1)}>
            Load current
          </Button>
        )}
      </AlertDescription>
    </Alert>
  );
  const form = (
    <form onSubmit={save} className="flex flex-col gap-4">
      <FieldGroup>
        <Field>
          <FieldLabel htmlFor="memory-name">Name</FieldLabel>
          <Input id="memory-name" value={name} onChange={(event) => setName(event.target.value)} />
        </Field>
        <FieldSet>
          <FieldLegend variant="label">Metadata</FieldLegend>
          {rows.map((row, index) => (
            <div key={index} className="flex items-center gap-2">
              <Input
                aria-label="Key"
                placeholder="key"
                value={row.key}
                onChange={(event) => setRow(index, { key: event.target.value })}
              />
              <Input
                aria-label="Value"
                placeholder="value"
                value={row.value}
                onChange={(event) => setRow(index, { value: event.target.value })}
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label="Remove metadata"
                onClick={() => setRows((current) => current.filter((_, at) => at !== index))}
              >
                <X />
              </Button>
            </div>
          ))}
          <div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setRows((r) => [...r, { key: "", value: "" }])}
            >
              <Plus data-icon="inline-start" />
              Add metadata
            </Button>
          </div>
        </FieldSet>
        <Field>
          <FieldLabel htmlFor="memory-content">Content</FieldLabel>
          <Textarea
            id="memory-content"
            rows={12}
            value={content}
            onChange={(event) => setContent(event.target.value)}
          />
        </Field>
      </FieldGroup>
      <SheetFooter className="flex-row justify-between p-0">
        <Button type="submit" disabled={busy}>
          {busy && <Spinner data-icon="inline-start" />}
          Save
        </Button>
        {entry && (
          <Button type="button" variant="destructive" disabled={busy} onClick={() => setConfirmDelete(true)}>
            Delete
          </Button>
        )}
      </SheetFooter>
    </form>
  );

  return (
    <>
      <SheetHeader>
        <SheetTitle>{entry ? entry.name : "New entry"}</SheetTitle>
        <SheetDescription>
          {entry
            ? `Saved by ${savedBy(entry)} · revision ${entry.revision}${isEmbedded(entry, currentProfile) ? "" : " · Not embedded"}`
            : "Saved for all agents."}
        </SheetDescription>
      </SheetHeader>
      <div className="flex flex-col gap-4 px-4 pb-4">
        {errorAlert}
        {loading ? (
          <Skeleton className="h-48 w-full" />
        ) : entry ? (
          <Tabs defaultValue="entry">
            <TabsList>
              <TabsTrigger value="entry">Entry</TabsTrigger>
              <TabsTrigger value="history">History</TabsTrigger>
            </TabsList>
            <TabsContent value="entry" className="pt-2">
              {form}
            </TabsContent>
            <TabsContent value="history" className="flex flex-col gap-4 pt-2">
              {versions.length === 0 && <p className="text-sm text-muted-foreground">No earlier versions.</p>}
              {versions.map((version) => (
                <div key={version.revision} className="flex flex-col gap-2 rounded-md border p-3 text-sm">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span>
                      Revision {version.revision} · {version.by_agent_name ?? version.by_email ?? "—"} ·{" "}
                      {when(version.changed_at)}
                    </span>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={busy}
                      aria-label={`Restore revision ${version.revision}`}
                      onClick={() => void restore(version)}
                    >
                      Restore
                    </Button>
                  </div>
                  <Diff from={entry.content} to={version.content} />
                </div>
              ))}
              {versions.length > 0 && (
                <p className="text-xs text-muted-foreground">
                  Lines show what restoring a version changes in the current text.
                </p>
              )}
            </TabsContent>
          </Tabs>
        ) : (
          form
        )}
      </div>
      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {entry?.name}?</AlertDialogTitle>
            <AlertDialogDescription>Delete {entry?.name}? Its history is deleted too.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction className={buttonVariants({ variant: "destructive" })} onClick={() => void remove()}>
              Delete entry
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

/** The side sheet of one memory entry ("new": a new one): form, history with restore, delete. */
export default function MemorySheet({
  id,
  onClose,
  onChanged,
}: {
  id: string | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  return (
    <Sheet
      open={id !== null}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-xl">
        {id !== null && <SheetBody key={id} id={id} onClose={onClose} onChanged={onChanged} />}
      </SheetContent>
    </Sheet>
  );
}
