import { useEffect, useState } from "react";
import { ApiError } from "~/lib/api";
import {
  applyReview,
  getReviewSource,
  type ApplyResult,
  type ProposedNote,
  type ReviewSource,
} from "~/features/agents/reviews";
import { memoryHref } from "~/features/memory/api";
import type { SessionReview } from "./api";
import Markdown from "~/components/Markdown";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import { Field, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Skeleton } from "~/components/ui/skeleton";
import { Textarea } from "~/components/ui/textarea";

type Note = ProposedNote & { ticked: boolean };
/** What an apply did; `target` is null when the instructions were kept. */
type Done = ApplyResult & { target: "agent" | "clone" | null };

/** Review a tuning suggestion: edit the proposed instructions and write them into this agent or a new clone, and save
 * the ticked memory notes as new shared memory entries. A review never changes the system prompt. */
export default function SuggestionApply({
  agentId,
  review,
  onClose,
  onApplied,
}: {
  agentId: string;
  review: SessionReview | null;
  onClose: () => void;
  onApplied: (target: "agent" | "clone", agentId: string) => void;
}) {
  const [source, setSource] = useState<ReviewSource>();
  const [instructions, setInstructions] = useState("");
  const [applyInstructions, setApplyInstructions] = useState(false);
  const [notes, setNotes] = useState<Note[]>([]);
  const [done, setDone] = useState<Done>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reloadable, setReloadable] = useState(false);
  function prefill(value: ReviewSource) {
    setSource(value);
    setInstructions(value.changes.instructions ?? value.instructions);
    setApplyInstructions(value.changes.instructions !== null);
    setNotes(value.memory_notes.map((note) => ({ ...note, ticked: !note.exists })));
  }
  useEffect(() => {
    if (!review) return;
    let live = true;
    setSource(undefined);
    setDone(undefined);
    setError("");
    setReloadable(false);
    getReviewSource(agentId, review.id)
      .then((value) => {
        if (live) prefill(value);
      })
      .catch((reason) => {
        if (live) {
          setError(reason instanceof Error ? reason.message : "Could not load the suggestion.");
          setReloadable(true);
        }
      });
    return () => {
      live = false;
    };
  }, [agentId, review]);
  /** Fetch the agent's latest instructions and revision; the user's edits stay. */
  async function reload() {
    if (!review) return;
    setBusy(true);
    setError("");
    setReloadable(false);
    try {
      const value = await getReviewSource(agentId, review.id);
      if (!source) prefill(value);
      else setSource(value);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load the suggestion.");
      setReloadable(true);
    } finally {
      setBusy(false);
    }
  }
  const ticked = notes.filter((note) => note.ticked);
  const ready =
    (applyInstructions || ticked.length > 0) && ticked.every((note) => note.name.trim() && note.content.trim());
  function edit(index: number, change: Partial<Note>) {
    setNotes((current) => current.map((note, at) => (at === index ? { ...note, ...change } : note)));
  }
  async function apply(target: "agent" | "clone") {
    if (!source || !review) return;
    setBusy(true);
    setError("");
    setReloadable(false);
    try {
      const sent = ticked.map(({ name, content, metadata }) => ({ name, content, metadata }));
      const result = await applyReview(
        agentId,
        review.id,
        source,
        target,
        applyInstructions ? instructions : null,
        sent,
      );
      if (sent.length === 0) onApplied(target, result.agent_id);
      else setDone({ ...result, target: applyInstructions ? target : null });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not apply the suggestion.");
      setReloadable(reason instanceof ApiError && reason.status === 409);
    } finally {
      setBusy(false);
    }
  }
  /** Close after an apply that saved notes: the instructions' target is reported like a plain apply. */
  function finish(value: Done) {
    if (value.target) onApplied(value.target, value.agent_id);
    else onClose();
  }
  return (
    <Sheet
      open={review !== null}
      onOpenChange={(open) => {
        if (open || busy) return;
        if (done) finish(done);
        else onClose();
      }}
    >
      <SheetContent className="w-full overflow-y-auto sm:max-w-3xl">
        <SheetHeader>
          <SheetTitle>Apply suggestions</SheetTitle>
          <SheetDescription>
            Apply the proposed instructions to this agent, or to a new clone to test them first, and save the ticked
            memory notes as new shared memory entries every agent can read.
          </SheetDescription>
        </SheetHeader>
        <div className="mt-6 flex flex-col gap-5">
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          {reloadable && (
            <div>
              <Button type="button" variant="outline" disabled={busy} onClick={() => void reload()}>
                Reload latest
              </Button>
            </div>
          )}
          {done ? (
            <div className="flex flex-col gap-4 text-sm">
              {done.target && (
                <p>{done.target === "clone" ? "Instructions applied to a new clone." : "Instructions applied."}</p>
              )}
              {done.saved.length > 0 && (
                <div>
                  <p className="font-medium">Saved to memory</p>
                  <ul className="mt-1 list-disc pl-5">
                    {done.saved.map((entry) => (
                      <li key={entry.id}>
                        <a className="underline" href={memoryHref(entry.id)} target="_blank" rel="noreferrer">
                          {entry.name}
                        </a>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {done.refused.length > 0 && (
                <Alert variant="destructive">
                  <AlertDescription>
                    <p>Not saved:</p>
                    <ul className="list-disc pl-5">
                      {done.refused.map((entry) => (
                        <li key={entry.name}>{entry.message}</li>
                      ))}
                    </ul>
                  </AlertDescription>
                </Alert>
              )}
              <div>
                <Button type="button" onClick={() => finish(done)}>
                  Done
                </Button>
              </div>
            </div>
          ) : !source ? (
            !error && <Skeleton className="h-48 w-full" />
          ) : (
            <FieldGroup>
              <details>
                <summary className="cursor-pointer text-sm font-medium">Reviewer’s suggestions</summary>
                <div className="mt-3">
                  <Markdown content={source.suggestion} />
                </div>
              </details>
              <FieldSet>
                <FieldLegend>Instructions</FieldLegend>
                <Field orientation="horizontal">
                  <Checkbox
                    id="apply-instructions-check"
                    checked={applyInstructions}
                    disabled={busy}
                    onCheckedChange={(checked) => setApplyInstructions(checked === true)}
                  />
                  <FieldLabel htmlFor="apply-instructions-check">Apply instructions</FieldLabel>
                </Field>
                <details>
                  <summary className="cursor-pointer text-sm text-muted-foreground">Current instructions</summary>
                  <pre className="mt-2 whitespace-pre-wrap rounded-md bg-muted p-3 text-sm">
                    {source.instructions || "(none)"}
                  </pre>
                </details>
                <Field>
                  <FieldLabel htmlFor="apply-instructions">Instructions</FieldLabel>
                  <Textarea
                    id="apply-instructions"
                    rows={7}
                    maxLength={40000}
                    value={instructions}
                    disabled={busy || !applyInstructions}
                    onChange={(event) => setInstructions(event.target.value)}
                  />
                </Field>
              </FieldSet>
              <FieldSet>
                <FieldLegend>Memory notes</FieldLegend>
                {notes.length === 0 && <p className="text-sm text-muted-foreground">No memory notes proposed.</p>}
                {notes.map((note, index) => (
                  <div key={index} className="flex flex-col gap-3 rounded-md border p-3">
                    <Field orientation="horizontal">
                      <Checkbox
                        id={`apply-note-${index}`}
                        checked={note.ticked}
                        disabled={busy}
                        onCheckedChange={(checked) => edit(index, { ticked: checked === true })}
                      />
                      <FieldLabel htmlFor={`apply-note-${index}`}>{`Save note ${index + 1}`}</FieldLabel>
                      {note.exists && note.memory_id && (
                        <a
                          className="ml-auto text-sm underline"
                          href={memoryHref(note.memory_id)}
                          target="_blank"
                          rel="noreferrer"
                        >
                          Already exists — open in Memory
                        </a>
                      )}
                    </Field>
                    <Field>
                      <FieldLabel htmlFor={`apply-note-${index}-name`}>{`Note ${index + 1} name`}</FieldLabel>
                      <Input
                        id={`apply-note-${index}-name`}
                        value={note.name}
                        disabled={busy || !note.ticked}
                        onChange={(event) => edit(index, { name: event.target.value })}
                      />
                    </Field>
                    <Field>
                      <FieldLabel htmlFor={`apply-note-${index}-content`}>{`Note ${index + 1} content`}</FieldLabel>
                      <Textarea
                        id={`apply-note-${index}-content`}
                        rows={4}
                        value={note.content}
                        disabled={busy || !note.ticked}
                        onChange={(event) => edit(index, { content: event.target.value })}
                      />
                    </Field>
                    {Object.keys(note.metadata).length > 0 && (
                      <p className="text-xs text-muted-foreground">
                        {Object.entries(note.metadata)
                          .map(([key, value]) => `${key} = ${value}`)
                          .join(" · ")}
                      </p>
                    )}
                  </div>
                ))}
              </FieldSet>
              <div className="flex flex-wrap gap-2">
                {applyInstructions ? (
                  <>
                    <Button type="button" disabled={busy || !ready} onClick={() => void apply("agent")}>
                      Apply to this agent
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      disabled={busy || !ready}
                      onClick={() => void apply("clone")}
                    >
                      Apply to a new clone
                    </Button>
                  </>
                ) : (
                  <Button type="button" disabled={busy || !ready} onClick={() => void apply("agent")}>
                    Save memory notes
                  </Button>
                )}
                <Button type="button" variant="ghost" disabled={busy} onClick={onClose}>
                  Cancel
                </Button>
              </div>
            </FieldGroup>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
