import { useEffect, useRef, useState } from "react";
import { Pencil, Plus, Trash2, Workflow } from "lucide-react";
import { ApiError } from "../lib/api";
import { deleteHandler, getHandlers, saveHandler, toDraft, type EventHandler } from "../lib/eventHandlers";
import HandlerSheet from "./HandlerSheet";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "~/components/ui/alert-dialog";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "~/components/ui/empty";
import { Skeleton } from "~/components/ui/skeleton";
import { Switch } from "~/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

const text = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback;
const when = (handler: EventHandler) => `${handler.type_pattern} · ${handler.conditions.length} condition${handler.conditions.length === 1 ? "" : "s"}`;
const action = (handler: EventHandler) => handler.action === "emit" ? `Emit ${handler.emit?.type ?? ""}` : `Send to ${handler.agent_name ?? handler.agent_id}`;

export default function HandlersView() {
  const [handlers, setHandlers] = useState<EventHandler[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<EventHandler | "new" | null>(null);
  const [deleting, setDeleting] = useState<EventHandler | null>(null);
  const [removing, setRemoving] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const deletingName = useRef("");

  useEffect(() => {
    getHandlers().then(result => { setHandlers(result.handlers); setError(null); })
      .catch(reason => { setHandlers([]); setError(text(reason, "Could not load handlers.")); });
  }, []);

  const sorted = (rows: EventHandler[]) => [...rows].sort((a, b) => a.name.localeCompare(b.name));
  const replace = (saved: EventHandler) => setHandlers(current => sorted([...(current ?? []).filter(item => item.id !== saved.id), saved]));
  async function toggle(handler: EventHandler, enabled: boolean) {
    setBusy(handler.id); setError(null);
    try { replace((await saveHandler(handler.id, { ...toDraft(handler), enabled, revision: handler.revision })).handler); }
    catch (reason) { setError(text(reason, "Could not change the handler.")); }
    finally { setBusy(null); }
  }
  async function remove() {
    const doomed = deleting;
    if (!doomed || removing) return;
    setRemoving(true); setError(null);
    try { await deleteHandler(doomed.id); setHandlers(current => current?.filter(item => item.id !== doomed.id) ?? null); }
    catch (reason) { setError(text(reason, "Could not delete the handler.")); }
    finally { setRemoving(false); setDeleting(null); }
  }

  return <div className="flex flex-col gap-4">
    <div className="flex justify-end"><Button onClick={() => setEditing("new")}><Plus data-icon="inline-start" />New handler</Button></div>
    {error && <Alert variant="destructive"><AlertTitle>Handlers</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
    {handlers === null && <Skeleton className="h-32 w-full" />}
    {handlers?.length === 0 && <Empty className="border"><EmptyHeader><EmptyMedia variant="icon"><Workflow /></EmptyMedia><EmptyTitle>Handlers</EmptyTitle>
      <EmptyDescription>No handlers yet. Create one to turn events into new events or agent sessions.</EmptyDescription></EmptyHeader></Empty>}
    {handlers && handlers.length > 0 && <div className="overflow-x-auto rounded-md border"><Table>
      <TableHeader><TableRow><TableHead>On</TableHead><TableHead>Name</TableHead><TableHead>When</TableHead><TableHead>Action</TableHead>
        <TableHead>Last run</TableHead><TableHead><span className="sr-only">Actions</span></TableHead></TableRow></TableHeader>
      <TableBody>{handlers.map(handler => <TableRow key={handler.id}>
        <TableCell><Switch aria-label={`Enabled: ${handler.name}`} checked={handler.enabled} disabled={busy === handler.id}
          onCheckedChange={enabled => void toggle(handler, enabled)} /></TableCell>
        <TableCell className="font-medium">{handler.name}</TableCell>
        <TableCell className="font-mono text-xs">{when(handler)}</TableCell>
        <TableCell>{action(handler)}</TableCell>
        <TableCell>{handler.last_run ? <div className="flex flex-col items-start gap-1">
          <Badge variant={handler.last_run.status === "failed" ? "destructive" : "secondary"}>{handler.last_run.status === "failed" ? "Failed" : "Succeeded"}</Badge>
          <span className="text-xs text-muted-foreground">{new Date(handler.last_run.created_at).toLocaleString()}</span></div>
          : <span className="text-sm text-muted-foreground">Never run</span>}</TableCell>
        <TableCell><div className="flex justify-end gap-1">
          <Button size="sm" variant="ghost" aria-label={`Edit ${handler.name}`} onClick={() => setEditing(handler)}><Pencil data-icon="inline-start" />Edit</Button>
          <Button size="sm" variant="ghost" aria-label={`Delete ${handler.name}`} onClick={() => { deletingName.current = handler.name; setDeleting(handler); }}><Trash2 data-icon="inline-start" />Delete</Button>
        </div></TableCell>
      </TableRow>)}</TableBody>
    </Table></div>}
    <HandlerSheet open={editing !== null} handler={editing === "new" ? null : editing} onClose={() => setEditing(null)}
      onSaved={saved => { replace(saved); setEditing(null); }} />
    <AlertDialog open={deleting !== null} onOpenChange={open => { if (!open && !removing) setDeleting(null); }}>
      <AlertDialogContent>
        <AlertDialogHeader><AlertDialogTitle>Delete handler {deletingName.current}?</AlertDialogTitle>
          <AlertDialogDescription>Its run history stays on the events.</AlertDialogDescription></AlertDialogHeader>
        <AlertDialogFooter><AlertDialogCancel disabled={removing}>Cancel</AlertDialogCancel>
          <AlertDialogAction disabled={removing} onClick={event => { event.preventDefault(); void remove(); }}>Delete</AlertDialogAction></AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  </div>;
}
