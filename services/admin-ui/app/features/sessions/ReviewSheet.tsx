import { useMemo, useRef, useState } from "react";
import type { ChatScope } from "./api";
import { getReviews } from "./reviews";
import ChatColumn from "./ChatColumn";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";

const LABELS = { list: "Reviews", empty: "No earlier reviews of this chat.", new: "New review" };

/** Improve agent: a wide right-side Sheet holding the shared agent chat (ChatColumn → AgentChat) for a review of the
 * run's session. It opens on a new review (nothing starts until the first message, the person's feedback);
 * Reviews lists the session's earlier reviews (GET …/reviews, newest first) and reopens one. The reviewer's
 * messages, tool calls and results stream as in the agent chat, with Stop. onAgentChanged is called after each of the
 * reviewer's turns (it may have changed the agent's instructions). */
export default function ReviewSheet({
  runKey,
  agentName,
  open,
  onOpenChange,
  onAgentChanged,
}: {
  runKey: string;
  agentName: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAgentChanged?: () => void;
}) {
  const scope: ChatScope = useMemo(() => ({ kind: "review", id: runKey, name: agentName }), [runKey, agentName]);
  const [shown, setShown] = useState<string | null>(null);
  const version = useRef("");
  const changeOpen = (value: boolean) => {
    if (!value) setShown(null); // the next Improve agent starts on a new review
    onOpenChange(value);
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
            <ChatColumn
              key={runKey}
              scope={scope}
              list={(signal) => getReviews(runKey, signal)}
              selection={[shown, setShown]}
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
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
