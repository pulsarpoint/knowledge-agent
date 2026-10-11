import { Fragment, useRef, useState } from "react";
import { Link } from "react-router";
import { ApiError } from "~/lib/api";
import { acceptProposal, rejectProposal, SERVICE_FIELD_LABELS, type Proposal, type ServiceField, type ServiceProposal } from "~/lib/discovery";
import Markdown from "./Markdown";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

export const proposalLabel = (proposal: Proposal) => proposal.kind === "service"
  ? `${proposal.service_id ? "Update service" : "New service"} ${proposal.payload.name}`
  : `${proposal.document_id ? "New version of" : "New document"} ${proposal.payload.title}`;

const DEPLOYMENT: [keyof ServiceProposal["payload"]["deployment"], string][] = [["environment", "Environment"], ["runtime", "Runtime"],
  ["runtime_name", "Runtime name"], ["port", "Port"], ["log_location", "Logs"], ["deployed_version", "Version"]];
const shown = (value: unknown) => value === null || value === undefined || value === "" ? "—" : String(value);

function ServiceDetails({ proposal, kept, onKeep }: { proposal: ServiceProposal; kept: ServiceField[]; onKeep: (field: ServiceField, on: boolean) => void }) {
  const { payload, current } = proposal;
  return <div className="flex flex-col gap-4">
    {current ? <section aria-label="Changes"><h3 className="text-sm font-semibold">Changes</h3>
      {(proposal.changes ?? []).length === 0 ? <p className="text-sm text-muted-foreground">No field changes; accepting adds the deployment and connections.</p> :
        <Table><TableHeader><TableRow><TableHead>Keep</TableHead><TableHead>Field</TableHead><TableHead>Current</TableHead><TableHead>Proposed</TableHead></TableRow></TableHeader>
          <TableBody>{proposal.changes!.map(field => <TableRow key={field}>
            <TableCell><Checkbox aria-label={`Keep ${SERVICE_FIELD_LABELS[field]}`} checked={kept.includes(field)} disabled={proposal.status !== "pending"}
              onCheckedChange={on => onKeep(field, on === true)} /></TableCell>
            <TableCell>{SERVICE_FIELD_LABELS[field]}</TableCell>
            <TableCell className="whitespace-pre-wrap">{shown(current[field])}</TableCell>
            <TableCell className="whitespace-pre-wrap">{shown(payload[field])}</TableCell></TableRow>)}</TableBody></Table>}</section>
      : <section aria-label="Service"><h3 className="text-sm font-semibold">Service</h3><dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        <dt>Name</dt><dd>{payload.name}</dd>
        {(Object.keys(SERVICE_FIELD_LABELS) as ServiceField[]).map(field => <Fragment key={field}><dt>{SERVICE_FIELD_LABELS[field]}</dt><dd className="whitespace-pre-wrap">{shown(payload[field])}</dd></Fragment>)}
      </dl></section>}
    <section aria-label="Deployment"><h3 className="text-sm font-semibold">Deployment on this server</h3>
      <Table><TableHeader><TableRow><TableHead /><TableHead>{proposal.current_deployment ? "Current" : ""}</TableHead><TableHead>Proposed</TableHead></TableRow></TableHeader>
        <TableBody>{DEPLOYMENT.map(([key, label]) => <TableRow key={key}><TableCell>{label}</TableCell>
          <TableCell>{proposal.current_deployment ? shown(proposal.current_deployment[key as keyof typeof proposal.current_deployment]) : ""}</TableCell>
          <TableCell>{shown(payload.deployment[key])}</TableCell></TableRow>)}</TableBody></Table>
      <p className="text-xs text-muted-foreground">{proposal.current_deployment ? "Accepting updates this deployment with the proposed values that are filled in." : "Accepting adds this deployment."}</p></section>
    {(payload.depends_on.length > 0 || payload.built_from.length > 0) && <section aria-label="Connections"><h3 className="text-sm font-semibold">Connections to add</h3>
      <ul className="text-sm">{payload.depends_on.map(item => <li key={item.id}>Depends on {item.name}</li>)}
        {payload.built_from.map(item => <li key={item.id}>Built from {item.name}</li>)}</ul></section>}
  </div>;
}

/** One proposal in a wide side panel, with Accept and Reject while it is pending. */
export default function ProposalSheet({ proposal, message, onClose, onMessage, onReload }: { proposal: Proposal; message: string | null; onClose: () => void;
  onMessage: (message: string | null) => void; onReload: () => void }) {
  const [kept, setKept] = useState<ServiceField[]>(proposal.kind === "service" ? proposal.changes ?? [] : []);
  const [busy, setBusy] = useState(false);
  const deciding = useRef(false);
  const pending = proposal.status === "pending";
  async function decide(action: "accept" | "reject") {
    if (deciding.current) return;
    deciding.current = true; setBusy(true); onMessage(null);
    try {
      if (action === "reject") {
        const result = await rejectProposal(proposal.id, proposal.revision);
        onMessage(result.rejected_documents ? `Rejected, with ${result.rejected_documents} document proposal${result.rejected_documents === 1 ? "" : "s"} about it.` : null);
      } else {
        const update = proposal.kind === "service" && proposal.current;
        const order = proposal.kind === "service" ? proposal.changes ?? [] : [];
        const result = await acceptProposal(proposal.id, proposal.revision,
          update ? [...kept].sort((a, b) => order.indexOf(a) - order.indexOf(b)) : undefined);
        onMessage(result.export_error);
      }
      onReload();
    } catch (e) {
      // The reload may turn the proposal into an update (a new revision remounts this sheet): the page keeps the reason.
      onMessage(e instanceof ApiError ? e.message : "The request failed.");
      onReload();
    } finally { deciding.current = false; setBusy(false); }
  }
  const target = proposal.kind === "document" ? (proposal.service_name ? `About service ${proposal.service_name}` : "About this server") : null;
  return <Sheet open onOpenChange={open => { if (!open && !busy) onClose(); }}>
    <SheetContent className="w-full overflow-y-auto sm:max-w-3xl">
      <SheetHeader><SheetTitle>{`${proposal.number} · ${proposalLabel(proposal)}`}</SheetTitle>
        <SheetDescription>{target ?? (proposal.current ? "Untick a change to keep the current value." : "A new service.")}
          {proposal.kind === "document" && proposal.current && ` Replaces version ${proposal.current.version} of “${proposal.current.title}”.`}</SheetDescription></SheetHeader>
      <div className="mt-4 flex flex-col gap-4">
        {message && <Alert variant="destructive"><AlertDescription>{message}</AlertDescription></Alert>}
        {!pending && <p className="text-sm"><Badge variant="outline">{proposal.status === "accepted" ? "Accepted" : "Rejected"}</Badge>
          {proposal.status === "accepted" && proposal.result_id && (proposal.kind === "document"
            ? <> <Link className="underline" to={`/documents/${proposal.result_id}`}>Open document</Link></>
            : <> <Link className="underline" to="/services">Open Services</Link></>)}</p>}
        {proposal.kind === "service"
          ? <ServiceDetails proposal={proposal} kept={kept} onKeep={(field, on) => setKept(list => on ? [...list, field] : list.filter(item => item !== field))} />
          : <><p className="text-sm text-muted-foreground">{proposal.payload.description}</p><Markdown content={proposal.payload.markdown} /></>}
        {pending && <div className="flex justify-end gap-2">
          <Button variant="outline" disabled={busy} onClick={() => void decide("reject")}>Reject</Button>
          <Button disabled={busy} onClick={() => void decide("accept")}>{busy && <Spinner data-icon="inline-start" />}Accept</Button></div>}
      </div>
    </SheetContent>
  </Sheet>;
}
