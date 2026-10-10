import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { ApiError } from "../lib/api";
import { getDocuments, type DocumentSummary } from "../lib/documents";
import { EDGE_LABELS, EDGE_TYPES, KIND_SUGGESTIONS, LAYER_LABELS, attachDocument, createEdge, createNode, deleteEdge, deleteNode,
  detachDocument, getNode, searchNodes, updateNode, type EdgeType, type Layer, type NodeDetail, type NodeHit } from "../lib/systemGraph";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Textarea } from "~/components/ui/textarea";

export type SheetTarget = { nodeId: string } | { create: { layer: Layer; parentId: string | null; parentName: string | null } };

interface Draft { kind: string; name: string; description: string; properties: string }
const EMPTY: Draft = { kind: "", name: "", description: "", properties: "" };
const say = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback;

/** Reads the Properties field: the object it holds, or what is wrong with it. */
function parseProperties(text: string): { value: Record<string, unknown> } | { problem: string } {
  if (!text.trim()) return { value: {} };
  let parsed: unknown;
  try { parsed = JSON.parse(text); } catch { return { problem: "Properties must be valid JSON." }; }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return { problem: 'Properties must be a JSON object, like {"port": 8766}.' };
  return { value: parsed as Record<string, unknown> };
}
const ready = (draft: Draft) => draft.kind.trim() !== "" && draft.name.trim() !== "" && "value" in parseProperties(draft.properties);
const propertiesOf = (draft: Draft) => { const parsed = parseProperties(draft.properties); return "value" in parsed ? parsed.value : {}; };

function DraftFields({ id, layer, draft, onChange, locked }: { id: string; layer: Layer; draft: Draft; onChange: (draft: Draft) => void; locked?: { label: string } }) {
  const parsed = parseProperties(draft.properties);
  const problem = "problem" in parsed ? parsed.problem : null;
  return <FieldGroup>
    <Field><FieldLabel htmlFor={`${id}-kind`}>Kind</FieldLabel>
      <Input id={`${id}-kind`} list={`${id}-kinds`} value={draft.kind} maxLength={60} readOnly={!!locked} autoComplete="off" onChange={event => onChange({ ...draft, kind: event.target.value })} />
      <datalist id={`${id}-kinds`}>{KIND_SUGGESTIONS[layer].map(kind => <option key={kind} value={kind} />)}</datalist></Field>
    <Field><FieldLabel htmlFor={`${id}-name`}>Name</FieldLabel>
      <Input id={`${id}-name`} value={draft.name} maxLength={160} readOnly={!!locked} onChange={event => onChange({ ...draft, name: event.target.value })} />
      {locked && <FieldDescription>Change it in {locked.label}.</FieldDescription>}</Field>
    <Field><FieldLabel htmlFor={`${id}-description`}>Description</FieldLabel>
      <Textarea id={`${id}-description`} value={draft.description} maxLength={4000} rows={3} onChange={event => onChange({ ...draft, description: event.target.value })} /></Field>
    <Field data-invalid={problem !== null || undefined}><FieldLabel htmlFor={`${id}-properties`}>Properties</FieldLabel>
      <Textarea id={`${id}-properties`} value={draft.properties} rows={4} spellCheck={false} className="font-mono text-xs" placeholder='{"port": 8766}'
        aria-invalid={problem !== null} onChange={event => onChange({ ...draft, properties: event.target.value })} />
      {problem ? <FieldError>{problem}</FieldError> : <FieldDescription>Optional JSON object with extra facts.</FieldDescription>}</Field>
  </FieldGroup>;
}

/** Search by name; each result gets one action button. */
function NodePicker({ label, layer, exclude, action, onPick }: { label: string; layer?: Layer; exclude: string; action: string; onPick: (hit: NodeHit) => void }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<NodeHit[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function find(event: FormEvent) {
    event.preventDefault(); setError(null);
    try { setHits((await searchNodes(q.trim(), layer)).nodes.filter(hit => hit.id !== exclude)); }
    catch (reason) { setError(say(reason, "Could not search.")); }
  }
  return <form onSubmit={find} className="flex flex-col gap-2">
    <div className="flex gap-2"><Input aria-label={label} value={q} placeholder="Search by name" onChange={event => setQ(event.target.value)} />
      <Button type="submit" variant="outline">Find</Button></div>
    {error && <p className="text-destructive text-sm">{error}</p>}
    {hits && (hits.length === 0 ? <p className="text-muted-foreground text-sm">No nodes found.</p>
      : <ul className="flex flex-col gap-1">{hits.map(hit => <li key={hit.id} className="flex items-center justify-between gap-2 text-sm">
        <span>{hit.path.length > 0 && <span className="text-muted-foreground">{hit.path.join(" › ")} › </span>}
          {layer ? hit.name : `${hit.name} · ${LAYER_LABELS[hit.layer]}`}</span>
        <Button type="button" size="sm" variant="outline" aria-label={`${action}: ${hit.name}`} onClick={() => onPick(hit)}>{action}</Button></li>)}</ul>)}
  </form>;
}

function DocumentPicker({ attached, onPick }: { attached: string[]; onPick: (document: DocumentSummary) => void }) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<DocumentSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function find(event: FormEvent) {
    event.preventDefault(); setError(null);
    try { setFound((await getDocuments(new URLSearchParams({ q: q.trim() }))).documents); }
    catch (reason) { setError(say(reason, "Could not search documents.")); }
  }
  const shown = found?.filter(document => !attached.includes(document.id));
  return <form onSubmit={find} className="flex flex-col gap-2">
    <div className="flex gap-2"><Input aria-label="Find a document" value={q} placeholder="Title or slug" onChange={event => setQ(event.target.value)} />
      <Button type="submit" variant="outline">Find</Button></div>
    {error && <p className="text-destructive text-sm">{error}</p>}
    {shown && (shown.length === 0 ? <p className="text-muted-foreground text-sm">No documents to attach.</p>
      : <ul className="flex flex-col gap-1">{shown.map(document => <li key={document.id} className="flex items-center justify-between gap-2 text-sm">
        <span>{document.title}</span>
        <Button type="button" size="sm" variant="outline" aria-label={`Attach: ${document.title}`} onClick={() => onPick(document)}>Attach</Button></li>)}</ul>)}
  </form>;
}

function CreateNode({ layer, parentId, parentName, onCreated }: { layer: Layer; parentId: string | null; parentName: string | null; onCreated: () => void }) {
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault(); setSaving(true); setError(null);
    try {
      await createNode({ layer, parent_id: parentId, kind: draft.kind.trim(), name: draft.name.trim(), description: draft.description, properties: propertiesOf(draft) });
      onCreated();
    } catch (reason) { setError(say(reason, "Could not create the node.")); setSaving(false); }
  }
  return <>
    <SheetHeader><SheetTitle>New node</SheetTitle>
      <SheetDescription>In the {LAYER_LABELS[layer]} layer, {parentName ? `inside ${parentName}` : "at the top level"}.</SheetDescription></SheetHeader>
    <form onSubmit={submit} className="flex flex-col gap-4 px-4 pb-4">
      <DraftFields id="new-node" layer={layer} draft={draft} onChange={setDraft} />
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      <SheetFooter><Button type="submit" disabled={saving || !ready(draft)}>{saving && <Spinner data-icon="inline-start" />}Create</Button></SheetFooter>
    </form>
  </>;
}

function NodeDetails({ nodeId, onChanged, onDeleted }: { nodeId: string; onChanged: () => void; onDeleted: () => void }) {
  const [node, setNode] = useState<NodeDetail | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [edgeType, setEdgeType] = useState<EdgeType>("uses");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  // The form is filled from the node on the first load and after Save; other reloads keep what was typed.
  const load = useCallback(async (resetDraft: boolean) => {
    try {
      const { node: loaded } = await getNode(nodeId);
      setNode(loaded);
      if (resetDraft) setDraft({ kind: loaded.kind, name: loaded.name, description: loaded.description,
        properties: Object.keys(loaded.properties).length ? JSON.stringify(loaded.properties, null, 2) : "" });
    } catch (reason) { setError(say(reason, "Could not load the node.")); }
  }, [nodeId]);
  useEffect(() => { void load(true); }, [load]);

  async function act(work: () => Promise<unknown>, fallback: string, resetDraft = false) {
    setBusy(true); setError(null);
    try { await work(); onChanged(); await load(resetDraft); }
    catch (reason) { setError(say(reason, fallback)); }
    finally { setBusy(false); }
  }
  async function remove() {
    setBusy(true); setError(null);
    try { await deleteNode(nodeId); onDeleted(); }
    catch (reason) { setError(say(reason, "Could not delete the node.")); setBusy(false); setConfirming(false); }
  }

  if (!node) return <>
    <SheetHeader><SheetTitle>Node</SheetTitle><SheetDescription>Loading…</SheetDescription></SheetHeader>
    <div className="px-4">{error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : <Spinner />}</div>
  </>;
  // Moving keeps the saved fields; unsaved edits in the form stay in the form.
  const moveTo = (parentId: string | null) => act(() => updateNode(node.id, { kind: node.kind, name: node.name, description: node.description,
    properties: node.properties, parent_id: parentId, revision: node.revision }), "Could not move the node.");
  const save = (event: FormEvent) => {
    event.preventDefault();
    void act(() => updateNode(node.id, { kind: draft.kind.trim(), name: draft.name.trim(), description: draft.description,
      properties: propertiesOf(draft), parent_id: node.parent_id, revision: node.revision }), "Could not save the node.", true);
  };
  const noEdges = node.outgoing.length === 0 && node.incoming.length === 0;
  return <>
    <SheetHeader><SheetTitle>{node.name}</SheetTitle>
      <SheetDescription>{LAYER_LABELS[node.layer]} · {node.breadcrumb.map(crumb => crumb.name).join(" › ")}</SheetDescription></SheetHeader>
    {node.source && <p className="px-4 text-sm"><span>From {node.source.label}</span> · <Link to={node.source.page} className="underline">Open {node.source.label}</Link></p>}
    <div className="flex flex-col gap-6 px-4 pb-4">
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      <form onSubmit={save} className="flex flex-col gap-4">
        <DraftFields id="node" layer={node.layer} draft={draft} onChange={setDraft} locked={node.source ?? undefined} />
        <div><Button type="submit" disabled={busy || !ready(draft)}>Save</Button></div>
      </form>
      {!node.source && <section aria-label="Move" className="flex flex-col gap-2">
        <h3 className="text-sm font-medium">Move</h3>
        <NodePicker label="New parent" layer={node.layer} exclude={node.id} action="Move here" onPick={hit => void moveTo(hit.id)} />
        {node.parent_id && <div><Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void moveTo(null)}>Move to the top level</Button></div>}
      </section>}
      <section aria-label="Edges" className="flex flex-col gap-2">
        <h3 className="text-sm font-medium">Edges</h3>
        {noEdges ? <p className="text-muted-foreground text-sm">No edges yet.</p> : <ul className="flex flex-col gap-1 text-sm">
          {node.outgoing.map(edge => <li key={edge.id} className="flex items-center justify-between gap-2">
            <span>{`${EDGE_LABELS[edge.type]} → ${edge.node.name}`}</span>
            {edge.synced ? <Badge variant="secondary">synced</Badge>
              : <Button type="button" size="sm" variant="ghost" aria-label={`Remove ${EDGE_LABELS[edge.type]} edge to ${edge.node.name}`}
                onClick={() => void act(() => deleteEdge(edge.id), "Could not remove the edge.")}>Remove</Button>}</li>)}
          {node.incoming.map(edge => <li key={edge.id} className="flex items-center justify-between gap-2">
            <span>{`${edge.node.name} → ${EDGE_LABELS[edge.type]}`}</span>
            {edge.synced ? <Badge variant="secondary">synced</Badge>
              : <Button type="button" size="sm" variant="ghost" aria-label={`Remove ${EDGE_LABELS[edge.type]} edge from ${edge.node.name}`}
                onClick={() => void act(() => deleteEdge(edge.id), "Could not remove the edge.")}>Remove</Button>}</li>)}
        </ul>}
        <Field><FieldLabel htmlFor="node-edge-type">New edge</FieldLabel>
          <Select value={edgeType} onValueChange={value => setEdgeType(value as EdgeType)}>
            <SelectTrigger id="node-edge-type" aria-label="Edge type" className="w-full"><SelectValue /></SelectTrigger>
            <SelectContent><SelectGroup>{EDGE_TYPES.map(type => <SelectItem key={type} value={type}>{EDGE_LABELS[type]}</SelectItem>)}</SelectGroup></SelectContent>
          </Select></Field>
        <NodePicker label="Target" exclude={node.id} action="Add edge"
          onPick={hit => void act(() => createEdge({ from_id: node.id, to_id: hit.id, type: edgeType, description: "" }), "Could not add the edge.")} />
      </section>
      <section aria-label="Documents" className="flex flex-col gap-2">
        <h3 className="text-sm font-medium">Documents</h3>
        {node.documents.length === 0 ? <p className="text-muted-foreground text-sm">No documents attached.</p> : <ul className="flex flex-col gap-1 text-sm">
          {node.documents.map(document => <li key={document.id} className="flex items-center justify-between gap-2">
            <Link to={`/documents/${document.id}`} className="underline">{document.title}</Link>
            <Button type="button" size="sm" variant="ghost" aria-label={`Detach ${document.title}`}
              onClick={() => void act(() => detachDocument(node.id, document.id), "Could not detach the document.")}>Detach</Button></li>)}
        </ul>}
        <DocumentPicker attached={node.documents.map(document => document.id)}
          onPick={document => void act(() => attachDocument(node.id, document.id), "Could not attach the document.")} />
      </section>
      {!node.source && <section aria-label="Delete node" className="flex flex-wrap items-center gap-2">
        {confirming ? <>
          <span className="text-sm">Delete {node.name}? Its edges and document links go with it.</span>
          <Button type="button" variant="destructive" disabled={busy} onClick={() => void remove()}>Delete node</Button>
          <Button type="button" variant="outline" onClick={() => setConfirming(false)}>Cancel</Button>
        </> : <Button type="button" variant="outline" onClick={() => setConfirming(true)}>Delete</Button>}
      </section>}
    </div>
  </>;
}

export default function NodeSheet({ target, onClose, onChanged }: { target: SheetTarget; onClose: () => void; onChanged: () => void }) {
  return <Sheet open onOpenChange={next => { if (!next) onClose(); }}>
    <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-xl">
      {"create" in target
        ? <CreateNode {...target.create} onCreated={() => { onChanged(); onClose(); }} />
        : <NodeDetails nodeId={target.nodeId} onChanged={onChanged} onDeleted={() => { onChanged(); onClose(); }} />}
    </SheetContent>
  </Sheet>;
}
