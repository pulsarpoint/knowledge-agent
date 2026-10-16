import { useEffect, useState } from "react";
import { ApiError } from "~/lib/api";
import { applyReview, getReviewSource, type InstructionChanges, type ReviewSource } from "~/features/agents/reviews";
import type { SessionReview } from "./api";
import Markdown from "~/components/Markdown";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Skeleton } from "~/components/ui/skeleton";
import { Textarea } from "~/components/ui/textarea";

/** Review a tuning suggestion's proposed texts, edit them, then write them into this agent or into a new clone. */
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
  const [prompt, setPrompt] = useState("");
  const [instructions, setInstructions] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reloadable, setReloadable] = useState(false);
  function prefill(value: ReviewSource) {
    setSource(value);
    setPrompt(value.changes.system_prompt ?? value.system_prompt);
    setInstructions(value.changes.instructions ?? value.instructions);
  }
  useEffect(() => {
    if (!review) return;
    let live = true;
    setSource(undefined);
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
  /** Fetch the agent's latest texts and revision; the user's edits stay. */
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
  function changes(value: ReviewSource): InstructionChanges {
    return {
      system_prompt: prompt === value.system_prompt ? null : prompt,
      instructions: instructions === value.instructions ? null : instructions,
    };
  }
  async function apply(target: "agent" | "clone") {
    if (!source || !review) return;
    setBusy(true);
    setError("");
    setReloadable(false);
    try {
      const result = await applyReview(agentId, review.id, source, target, changes(source));
      onApplied(target, result.agent_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not apply the suggestion.");
      setReloadable(reason instanceof ApiError && reason.status === 409);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Sheet
      open={review !== null}
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <SheetContent className="w-full overflow-y-auto sm:max-w-3xl">
        <SheetHeader>
          <SheetTitle>Apply suggestions</SheetTitle>
          <SheetDescription>
            Review the proposed texts. Apply them to this agent, or to a new clone to test them first. Only the prompt
            and instructions change.
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
          {!source ? (
            !error && <Skeleton className="h-48 w-full" />
          ) : (
            <FieldGroup>
              <details>
                <summary className="cursor-pointer text-sm font-medium">Reviewer’s suggestions</summary>
                <div className="mt-3">
                  <Markdown content={source.suggestion} />
                </div>
              </details>
              <Field>
                <FieldLabel htmlFor="apply-system-prompt">System prompt</FieldLabel>
                <Textarea
                  id="apply-system-prompt"
                  rows={10}
                  maxLength={100000}
                  value={prompt}
                  disabled={busy}
                  onChange={(event) => setPrompt(event.target.value)}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="apply-instructions">Additional instructions</FieldLabel>
                <Textarea
                  id="apply-instructions"
                  rows={7}
                  maxLength={40000}
                  value={instructions}
                  disabled={busy}
                  onChange={(event) => setInstructions(event.target.value)}
                />
              </Field>
              <div className="flex flex-wrap gap-2">
                <Button type="button" disabled={busy || !prompt.trim()} onClick={() => void apply("agent")}>
                  Apply to this agent
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy || !prompt.trim()}
                  onClick={() => void apply("clone")}
                >
                  Apply to a new clone
                </Button>
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
