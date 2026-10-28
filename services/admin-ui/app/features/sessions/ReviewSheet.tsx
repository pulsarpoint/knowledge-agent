import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import type { ChatScope } from "./api";
import { getReviews } from "./reviews";
import ChatColumn from "./ChatColumn";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Skeleton } from "~/components/ui/skeleton";

const LABELS = { list: "Reviews", empty: "No earlier reviews of this chat.", new: "New review" };
export const NOT_FOUND = "This review was not found for this chat. Showing a new review.";

/** The open review in the URL: `?review=new` (a new review) or `?review=<review session id>`; absent: the sheet is
 * closed. Setting it keeps the page's other params; null removes it. `replace` replaces the history entry (a new
 * review becoming its session once the first message starts it) instead of adding one. */
export function useReviewParam() {
  const [params, setParams] = useSearchParams();
  const set = (value: string | null, replace = false) =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (value === null) next.delete("review");
        else next.set("review", value);
        return next;
      },
      { replace },
    );
  return [params.get("review"), set] as const;
}

/** Improve agent: a wide right-side Sheet holding the shared agent chat (ChatColumn → AgentChat) for a review of the
 * run's session. It is open while the URL has `?review=` (useReviewParam), so a refresh or a link reopens it: `new`
 * shows a new review (nothing starts until the first message, the person's feedback; the param then becomes the
 * review's id, replacing the entry), an id that review with its whole history, ready for more messages. Reviews lists
 * the session's earlier reviews (GET …/reviews, newest first) and reopens one (its id goes in the URL); closing
 * removes the param. An id from the URL that is not one of this session's reviews shows an Alert and falls back to
 * new. The reviewer's messages, tool calls and results stream as in the agent chat, with Stop. onAgentChanged is
 * called after each of the reviewer's turns (it may have changed the agent's instructions). */
export default function ReviewSheet({
  runKey,
  agentName,
  onAgentChanged,
}: {
  runKey: string;
  agentName: string;
  onAgentChanged?: () => void;
}) {
  const scope: ChatScope = useMemo(() => ({ kind: "review", id: runKey, name: agentName }), [runKey, agentName]);
  const [review, setReview] = useReviewParam();
  const open = review !== null;
  const shown = review === null || review === "new" ? null : review;
  // The reviews known to be this session's: listed, picked or started here. Another id (a link, a refresh) is checked
  // against the session's reviews before the chat opens it.
  const known = useRef(new Set<string>());
  const [, setChecked] = useState(0);
  const [problem, setProblem] = useState("");
  const checking = shown !== null && !known.current.has(shown);
  const version = useRef("");
  useEffect(() => {
    if (!checking || shown === null) return;
    const id = shown;
    const controller = new AbortController();
    const settle = () => setChecked((n) => n + 1);
    getReviews(runKey, controller.signal)
      .then((list) => {
        if (controller.signal.aborted) return;
        for (const item of list) known.current.add(item.id);
        if (known.current.has(id)) settle();
        else {
          setProblem(NOT_FOUND);
          setReview("new", true);
        }
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        known.current.add(id); // the list failed: the chat itself refuses another chat's review
        settle();
      });
    return () => controller.abort();
  }, [checking, shown, runKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const select = (id: string | null, replace = false) => {
    if (id !== null) known.current.add(id);
    setProblem("");
    setReview(id ?? "new", replace);
  };
  const changeOpen = (value: boolean) => {
    if (value) return; // opened by the page (the param)
    setProblem("");
    setReview(null);
  };
  return (
    <Sheet open={open} onOpenChange={changeOpen}>
      <SheetContent className="flex w-full flex-col gap-3 sm:max-w-3xl">
        <SheetHeader>
          <SheetTitle>Improve agent</SheetTitle>
          <SheetDescription>
            {`Talk this chat through with a reviewer. It proposes changes to ${agentName}'s instructions, a new skill (created disabled) or a memory note, and makes one only when your latest message agrees.`}
          </SheetDescription>
        </SheetHeader>
        {open && (
          <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-2">
            {problem && (
              <Alert variant="destructive">
                <AlertDescription>{problem}</AlertDescription>
              </Alert>
            )}
            {checking ? (
              <Skeleton className="h-64 w-full" />
            ) : (
              <ChatColumn
                key={runKey}
                scope={scope}
                list={(signal) => getReviews(runKey, signal)}
                selection={[shown, select]}
                labels={LABELS}
                description="Earlier reviews of this chat, newest first."
                startFresh
                inSheet
                onSessionChange={(id, next) => {
                  if (id !== null && next !== version.current) {
                    version.current = next;
                    onAgentChanged?.();
                  }
                }}
              />
            )}
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
