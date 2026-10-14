import { useEffect, useState } from "react";
import { Copy } from "lucide-react";
import { reviewSession, type SessionDetail, type SessionReview } from "./api";
import Markdown from "~/components/Markdown";
import SuggestionApply from "./SuggestionApply";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Separator } from "~/components/ui/separator";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Textarea } from "~/components/ui/textarea";

/** A saved agent's run: feedback on an answer (turn) asks the reviewer for suggestions; the saved suggestions (all of the
 * run's, or those about the turn) can be copied, or reviewed and applied to the agent or a clone. */
export default function RunSuggestions({
  agentId,
  session,
  turn,
  open,
  onOpenChange,
  onReview,
  onApplied,
}: {
  agentId: string;
  session: SessionDetail;
  turn: number | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onReview: (review: SessionReview) => void;
  onApplied: (target: "agent" | "clone", id: string) => void;
}) {
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [applying, setApplying] = useState<SessionReview | null>(null);
  const working = session.status === "starting" || session.status === "working";
  useEffect(() => {
    if (open) {
      setError("");
      setInfo("");
      if (turn !== null) setComment("");
    }
  }, [open, turn]);

  async function act(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setInfo("");
    try {
      await action();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The operation failed.");
    } finally {
      setBusy(false);
    }
  }
  const shownReviews = (session.reviews ?? []).filter(
    (review) => turn === null || !review.response_turn || review.response_turn === turn,
  );

  return (
    <>
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
          <SheetHeader>
            <SheetTitle>{turn !== null ? "Improve this response" : "Saved suggestions"}</SheetTitle>
            <SheetDescription>
              Optional feedback for the reviewer. It reads the whole session and its configuration; changes are never
              applied automatically.
            </SheetDescription>
          </SheetHeader>
          <div className="mt-6 flex flex-col gap-6">
            {error && (
              <Alert variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
            {info && (
              <p role="status" className="text-sm text-muted-foreground">
                {info}
              </p>
            )}
            {turn !== null && (
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  if (comment.trim() && !busy && !working)
                    void act(async () => {
                      const review = await reviewSession(session.id, comment, turn);
                      onReview(review);
                      setComment("");
                    });
                }}
              >
                <FieldGroup>
                  <Field>
                    <FieldLabel htmlFor="agent-feedback">Your feedback</FieldLabel>
                    <Textarea
                      id="agent-feedback"
                      rows={4}
                      maxLength={20000}
                      disabled={busy}
                      value={comment}
                      onChange={(event) => setComment(event.target.value)}
                      placeholder="What should the agent do differently or remember?"
                    />
                    <FieldDescription>{`About the answer of turn ${turn}. Describe what a better response would do.`}</FieldDescription>
                  </Field>
                  <Button className="self-start" disabled={busy || working || !comment.trim()}>
                    {busy && <Spinner data-icon="inline-start" />}Suggest improvements
                  </Button>
                </FieldGroup>
              </form>
            )}
            {shownReviews.map((review) => (
              <div key={review.id} className="flex flex-col gap-4">
                <Separator />
                <p className="text-xs text-muted-foreground">
                  {new Date(review.created_at).toLocaleString()}
                  {review.response_turn ? ` · turn ${review.response_turn}` : ""}
                </p>
                <p className="whitespace-pre-wrap text-sm">
                  <strong>Your feedback:</strong> {review.comment}
                </p>
                <Markdown content={review.suggestion} />
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() =>
                      void act(async () => {
                        await navigator.clipboard.writeText(review.suggestion);
                        setInfo("Suggestions copied.");
                      })
                    }
                  >
                    <Copy data-icon="inline-start" />
                    Copy suggestions
                  </Button>
                  <Button size="sm" onClick={() => setApplying(review)}>
                    Review and apply
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </SheetContent>
      </Sheet>
      <SuggestionApply
        agentId={agentId}
        review={applying}
        onClose={() => setApplying(null)}
        onApplied={(target, id) => {
          setApplying(null);
          onOpenChange(false);
          onApplied(target, id);
        }}
      />
    </>
  );
}
