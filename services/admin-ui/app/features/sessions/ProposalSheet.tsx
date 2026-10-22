import { Fragment, useRef, useState } from "react";
import { Link } from "react-router";
import { ApiError } from "~/lib/api";
import {
  acceptProposal,
  rejectProposal,
  SERVICE_FIELD_LABELS,
  type DocumentProposal,
  type Proposal,
  type ServiceField,
  type ServiceProposal,
} from "~/features/servers/discovery";
import type { ResourceType } from "./api";
import Markdown from "~/components/Markdown";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

export const proposalLabel = (proposal: Proposal) =>
  proposal.kind === "service"
    ? `${proposal.service_id ? "Update service" : "New service"} ${proposal.payload.name}`
    : `${proposal.document_id ? "New version of" : "New document"} ${proposal.payload.title}`;

const DEPLOYMENT: [keyof ServiceProposal["payload"]["deployment"], string][] = [
  ["environment", "Environment"],
  ["runtime", "Runtime"],
  ["runtime_name", "Runtime name"],
  ["port", "Port"],
  ["log_location", "Logs"],
  ["deployed_version", "Version"],
];
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

const shown = (value: unknown) => (value === null || value === undefined || value === "" ? "—" : String(value));

const RELATIONS = { depends_on: "Depends on", part_of: "Part of", built_from: "Built from" } as const;

function ServiceDetails({
  proposal,
  kept,
  onKeep,
  related,
  onOpen,
}: {
  proposal: ServiceProposal;
  kept: ServiceField[];
  onKeep: (field: ServiceField, on: boolean) => void;
  related: Proposal[];
  onOpen?: (id: string) => void;
}) {
  const { payload, current } = proposal;
  const connections = payload.connections ?? [];
  const relations = (
    [
      ["depends_on", payload.depends_on],
      ["part_of", payload.part_of ?? []],
      ["built_from", payload.built_from],
    ] as const
  ).flatMap(([kind, items]) => items.map((item) => [kind, item] as const));
  // The run's document proposals about this service: a proposed service's by its proposal, a recorded one's by its id.
  const documents = related.filter(
    (item): item is DocumentProposal =>
      item.kind === "document" &&
      (item.service_proposal_id === proposal.id || (!!proposal.service_id && item.service_id === proposal.service_id)),
  );
  return (
    <div className="flex flex-col gap-4">
      {current ? (
        <section aria-label="Changes">
          <h3 className="text-sm font-semibold">Changes</h3>
          {(proposal.changes ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No field changes; accepting adds the deployment and connections.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Keep</TableHead>
                  <TableHead>Field</TableHead>
                  <TableHead>Current</TableHead>
                  <TableHead>Proposed</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {proposal.changes!.map((field) => (
                  <TableRow key={field}>
                    <TableCell>
                      <Checkbox
                        aria-label={`Keep ${SERVICE_FIELD_LABELS[field]}`}
                        checked={kept.includes(field)}
                        disabled={proposal.status !== "pending"}
                        onCheckedChange={(on) => onKeep(field, on === true)}
                      />
                    </TableCell>
                    <TableCell>{SERVICE_FIELD_LABELS[field]}</TableCell>
                    <TableCell className="whitespace-pre-wrap">{shown(current[field])}</TableCell>
                    <TableCell className="whitespace-pre-wrap">{shown(payload[field])}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </section>
      ) : (
        <section aria-label="Service">
          <h3 className="text-sm font-semibold">Service</h3>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            <dt>Name</dt>
            <dd>{payload.name}</dd>
            {(Object.keys(SERVICE_FIELD_LABELS) as ServiceField[]).map((field) => (
              <Fragment key={field}>
                <dt>{SERVICE_FIELD_LABELS[field]}</dt>
                <dd className="whitespace-pre-wrap">{shown(payload[field])}</dd>
              </Fragment>
            ))}
          </dl>
        </section>
      )}
      <section aria-label="Deployment">
        <h3 className="text-sm font-semibold">Deployment on this server</h3>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead />
              <TableHead>{proposal.current_deployment ? "Current" : ""}</TableHead>
              <TableHead>Proposed</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {DEPLOYMENT.map(([key, label]) => (
              <TableRow key={key}>
                <TableCell>{label}</TableCell>
                <TableCell>
                  {proposal.current_deployment
                    ? shown(proposal.current_deployment[key as keyof typeof proposal.current_deployment])
                    : ""}
                </TableCell>
                <TableCell>{shown(payload.deployment[key])}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <p className="text-xs text-muted-foreground">
          {proposal.current_deployment
            ? "Accepting updates this deployment with the proposed values that are filled in."
            : "Accepting adds this deployment."}
        </p>
      </section>
      {connections.length > 0 && (
        <section aria-label="Connections">
          <h3 className="text-sm font-semibold">Connections from this deployment</h3>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>To</TableHead>
                <TableHead>Port</TableHead>
                <TableHead>Protocol</TableHead>
                <TableHead>Description</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {connections.map((item, index) => (
                <TableRow key={index}>
                  <TableCell>{item.label}</TableCell>
                  <TableCell>{shown(item.port)}</TableCell>
                  <TableCell>{item.protocol}</TableCell>
                  <TableCell className="whitespace-pre-wrap">{item.description}</TableCell>
                  <TableCell>
                    <Badge variant="outline">{proposal.present?.connections[index] ? "Recorded" : "New"}</Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </section>
      )}
      {relations.length > 0 && (
        <section aria-label="Relations">
          <h3 className="text-sm font-semibold">Relations</h3>
          <ul className="text-sm">
            {relations.map(([kind, item]) => (
              <li key={`${kind}:${item.id}`}>
                {`${RELATIONS[kind]} ${item.name}`}
                {proposal.present?.relations.includes(`${kind}:${item.id}`) && (
                  <span className="text-muted-foreground"> (recorded)</span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
      {documents.length > 0 && (
        <section aria-label="Documents">
          <h3 className="text-sm font-semibold">Document proposals about this service</h3>
          <ul className="text-sm">
            {documents.map((item) => (
              <li key={item.id}>
                <Button variant="link" className="h-auto p-0" onClick={() => onOpen?.(item.id)}>
                  {`${item.number} · ${item.payload.title}`}
                </Button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

/** One proposal in a wide side panel, with Accept and Reject while it is pending. `resourceType`: what the run was
 * started from (a document not about a service is about it). `related`: the proposals of the same run, so a service
 * proposal lists the document proposals about it and a document proposal links to its service proposal; `onOpen`
 * opens one of them instead. */
export default function ProposalSheet({
  proposal,
  resourceType = "server",
  message,
  onClose,
  onMessage,
  onReload,
  related = [],
  onOpen,
}: {
  proposal: Proposal;
  resourceType?: ResourceType;
  message: string | null;
  onClose: () => void;
  onMessage: (message: string | null) => void;
  onReload: () => void;
  related?: Proposal[];
  onOpen?: (id: string) => void;
}) {
  const [kept, setKept] = useState<ServiceField[]>(proposal.kind === "service" ? (proposal.changes ?? []) : []);
  const [busy, setBusy] = useState(false);
  const deciding = useRef(false);
  const pending = proposal.status === "pending";
  // A document about a service proposed in this run is refused until that service is accepted.
  const waiting = pending && proposal.kind === "document" && proposal.service_proposal_status === "pending";
  async function decide(action: "accept" | "reject") {
    if (deciding.current) return;
    deciding.current = true;
    setBusy(true);
    onMessage(null);
    try {
      if (action === "reject") {
        const result = await rejectProposal(proposal.id, proposal.revision);
        onMessage(
          result.rejected_documents
            ? `Rejected, with ${result.rejected_documents} document proposal${result.rejected_documents === 1 ? "" : "s"} about it.`
            : null,
        );
      } else {
        const update = proposal.kind === "service" && proposal.current;
        const order = proposal.kind === "service" ? (proposal.changes ?? []) : [];
        await acceptProposal(
          proposal.id,
          proposal.revision,
          update ? [...kept].sort((a, b) => order.indexOf(a) - order.indexOf(b)) : undefined,
        );
      }
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
  const parent =
    proposal.kind === "document" && proposal.service_proposal_id
      ? related.find((item) => item.id === proposal.service_proposal_id)
      : undefined;
  const target =
    proposal.kind === "document"
      ? proposal.service_name
        ? `About service ${proposal.service_name}`
        : `About this ${resourceType}`
      : null;
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
            {target ?? (proposal.current ? "Untick a change to keep the current value." : "A new service.")}
            {proposal.kind === "document" &&
              proposal.current &&
              ` Accepting creates a draft of “${proposal.current.title}” (version ${proposal.current.version}) to promote.`}
          </SheetDescription>
        </SheetHeader>
        <div className="mt-4 flex flex-col gap-4">
          {!pending && (
            <div className="text-sm">
              <Badge variant="outline">{proposal.status === "accepted" ? "Accepted" : "Rejected"}</Badge>
              {proposal.status === "accepted" &&
                proposal.result_id &&
                (proposal.kind === "document" ? (
                  <>
                    {" "}
                    <AcceptedDocument proposal={proposal} />
                  </>
                ) : (
                  <>
                    {" "}
                    <Link className="underline" to="/services">
                      Open Services
                    </Link>
                  </>
                ))}
            </div>
          )}
          {proposal.kind === "service" ? (
            <ServiceDetails
              proposal={proposal}
              kept={kept}
              onKeep={(field, on) => setKept((list) => (on ? [...list, field] : list.filter((item) => item !== field)))}
              related={related}
              onOpen={onOpen}
            />
          ) : (
            <>
              {parent && (
                <p className="text-sm">
                  {"Service proposal: "}
                  <Button variant="link" className="h-auto p-0" onClick={() => onOpen?.(parent.id)}>
                    {`${parent.number} · ${proposalLabel(parent)}`}
                  </Button>
                </p>
              )}
              <p className="text-sm text-muted-foreground">{proposal.payload.description}</p>
              <Markdown content={proposal.payload.markdown} />
            </>
          )}
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
              {waiting && (
                <p className="text-sm text-muted-foreground">{`Accept the service proposal ${proposal.service_name} first.`}</p>
              )}
              {pending && (
                <div className="flex justify-end gap-2">
                  <Button variant="outline" disabled={busy} onClick={() => void decide("reject")}>
                    Reject
                  </Button>
                  <Button disabled={busy || waiting} onClick={() => void decide("accept")}>
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
