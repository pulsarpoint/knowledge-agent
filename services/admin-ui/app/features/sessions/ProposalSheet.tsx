import { useRef, useState } from "react";
import { Link } from "react-router";
import { ApiError } from "~/lib/api";
import { acceptProposal, rejectProposal, type DocumentProposal, type Proposal } from "~/features/servers/discovery";
import type { ResourceType } from "./api";
import Markdown from "~/components/Markdown";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";

export const proposalLabel = (proposal: Proposal) =>
  `${proposal.document_id ? "New version of" : "New document"} ${proposal.payload.title}`;

/** Where an accepted document proposal stands: its draft waits to be published (brand-new) or promoted (a draft of a
 * live document), then the document is live; a discarded draft leaves nothing to open. */
export function AcceptedDocument({ proposal }: { proposal: DocumentProposal }) {
  if (proposal.draft_published) {
    return (
      <Link className="underline" to={`/documents/${proposal.document_id ?? proposal.result_id}`}>
        Open document
      </Link>
    );
  }
  if (!proposal.draft_id) return <span className="text-muted-foreground">The draft was discarded.</span>;
  const ofLive = proposal.document_id !== null;
  return (
    <>
      <span className="text-muted-foreground">{ofLive ? "Waiting to be promoted" : "Waiting to be published"}</span>{" "}
      <Link
        className="underline"
        to={ofLive ? `/documents/drafts/${proposal.draft_id}` : `/documents/${proposal.draft_id}`}
      >
        Open draft
      </Link>
    </>
  );
}

/** One document proposal in a wide side panel, with Accept and Reject while it is pending. `resourceType`: what the
 * run was started from (a document not about a service is about it). */
export default function ProposalSheet({
  proposal,
  resourceType = "server",
  message,
  onClose,
  onMessage,
  onReload,
}: {
  proposal: Proposal;
  resourceType?: ResourceType;
  message: string | null;
  onClose: () => void;
  onMessage: (message: string | null) => void;
  onReload: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const deciding = useRef(false);
  const pending = proposal.status === "pending";
  async function decide(action: "accept" | "reject") {
    if (deciding.current) return;
    deciding.current = true;
    setBusy(true);
    onMessage(null);
    try {
      if (action === "reject") await rejectProposal(proposal.id, proposal.revision);
      else await acceptProposal(proposal.id, proposal.revision);
      onReload();
    } catch (e) {
      // The reload may turn the proposal into an update (a new revision remounts this sheet): the page keeps the reason.
      onMessage(e instanceof ApiError ? e.message : "The request failed.");
      onReload();
    } finally {
      deciding.current = false;
      setBusy(false);
    }
  }
  const target = proposal.service_name ? `About service ${proposal.service_name}` : `About this ${resourceType}`;
  return (
    <Sheet
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <SheetContent className="w-full overflow-y-auto sm:max-w-3xl">
        <SheetHeader>
          <SheetTitle>{`${proposal.number} · ${proposalLabel(proposal)}`}</SheetTitle>
          <SheetDescription>
            {target}
            {proposal.current &&
              ` Accepting creates a draft of “${proposal.current.title}” (version ${proposal.current.version}) to promote.`}
          </SheetDescription>
        </SheetHeader>
        <div className="mt-4 flex flex-col gap-4">
          {!pending && (
            <div className="text-sm">
              <Badge variant="outline">{proposal.status === "accepted" ? "Accepted" : "Rejected"}</Badge>
              {proposal.status === "accepted" && proposal.result_id && (
                <>
                  {" "}
                  <AcceptedDocument proposal={proposal} />
                </>
              )}
            </div>
          )}
          <p className="text-sm text-muted-foreground">{proposal.payload.description}</p>
          <Markdown content={proposal.payload.markdown} />
          {(message || pending) && (
            <div
              role="group"
              aria-label="Decision"
              className="sticky bottom-0 flex flex-col gap-2 border-t bg-background py-3"
            >
              {message && (
                <Alert variant="destructive">
                  <AlertDescription>{message}</AlertDescription>
                </Alert>
              )}
              {pending && (
                <div className="flex justify-end gap-2">
                  <Button variant="outline" disabled={busy} onClick={() => void decide("reject")}>
                    Reject
                  </Button>
                  <Button disabled={busy} onClick={() => void decide("accept")}>
                    {busy && <Spinner data-icon="inline-start" />}Accept
                  </Button>
                </div>
              )}
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
