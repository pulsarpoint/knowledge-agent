import { useEffect, useRef, useState } from "react";
import { Pencil, Plus, Workflow } from "lucide-react";
import { useSearchParams } from "react-router";
import { toast } from "sonner";
import { ApiError } from "~/lib/api";
import { deleteHandler, boundToSchedule, getHandlers, saveHandler, toDraft, type EventHandler } from "./handlers";
import HandlerSheet from "./HandlerSheet";
import ConfirmRemove from "~/components/ConfirmRemove";
import RowMenu, { deleteMenuItem } from "~/components/RowMenu";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "~/components/ui/empty";
import { Skeleton } from "~/components/ui/skeleton";
import { Switch } from "~/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

const text = (reason: unknown, fallback: string) => (reason instanceof ApiError ? reason.message : fallback);
const when = (handler: EventHandler) =>
  `${handler.type_pattern} · ${handler.conditions.length} condition${handler.conditions.length === 1 ? "" : "s"}`;
const action = (handler: EventHandler) =>
  handler.action === "emit"
    ? `Emit ${handler.emit?.type ?? ""}`
    : handler.agent_from === "assignee"
      ? "Send to the assignee’s agent"
      : `Send to ${handler.agent_name ?? handler.agent_id}`;

/** ?handler=<id> opens that handler (Jira → Settings → Users and agents links here). */
export default function HandlersView() {
  const [params, setParams] = useSearchParams();
  const linked = params.get("handler");
  const [handlers, setHandlers] = useState<EventHandler[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<EventHandler | "new" | null>(null);
  const [deleting, setDeleting] = useState<EventHandler | null>(null);
  const [removing, setRemoving] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const deletingName = useRef("");
  const deletingSchedule = useRef(false);

  useEffect(() => {
    getHandlers()
      .then((result) => {
        setHandlers(result.handlers);
        setError(null);
        const opened = result.handlers.find((item) => item.id === linked);
        if (opened) setEditing(opened);
      })
      .catch((reason) => {
        setHandlers([]);
        setError(text(reason, "Could not load handlers."));
      });
    // Only the first load opens the linked handler.
  }, []);

  const sorted = (rows: EventHandler[]) => [...rows].sort((a, b) => a.name.localeCompare(b.name));
  const replace = (saved: EventHandler) =>
    setHandlers((current) => sorted([...(current ?? []).filter((item) => item.id !== saved.id), saved]));
  function close() {
    setEditing(null);
    if (linked) setParams({}, { replace: true });
  }
  async function toggle(handler: EventHandler, enabled: boolean) {
    setBusy(handler.id);
    setError(null);
    try {
      replace((await saveHandler(handler.id, { ...toDraft(handler), enabled, revision: handler.revision })).handler);
      toast.success(`Handler “${handler.name}” ${enabled ? "enabled" : "disabled"}.`);
    } catch (reason) {
      setError(text(reason, "Could not change the handler."));
    } finally {
      setBusy(null);
    }
  }
  async function remove() {
    const doomed = deleting;
    if (!doomed || removing) return;
    setRemoving(true);
    setError(null);
    try {
      await deleteHandler(doomed.id);
      setHandlers((current) => current?.filter((item) => item.id !== doomed.id) ?? null);
      toast.success(`Handler “${doomed.name}” deleted.`);
    } catch (reason) {
      setError(text(reason, "Could not delete the handler."));
    } finally {
      setRemoving(false);
      setDeleting(null);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex justify-end">
        <Button onClick={() => setEditing("new")}>
          <Plus data-icon="inline-start" />
          New handler
        </Button>
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {handlers === null && <Skeleton className="h-32 w-full" />}
      {handlers?.length === 0 && (
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <Workflow />
            </EmptyMedia>
            <EmptyTitle>Handlers</EmptyTitle>
            <EmptyDescription>
              No handlers yet. Create one to turn events into new events or agent sessions.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      )}
      {handlers && handlers.length > 0 && (
        <div className="overflow-x-auto rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Enabled</TableHead>
                <TableHead>Name</TableHead>
                <TableHead>When</TableHead>
                <TableHead>Action</TableHead>
                <TableHead>Last run</TableHead>
                <TableHead>
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {handlers.map((handler) => (
                <TableRow key={handler.id}>
                  <TableCell>
                    <Switch
                      aria-label={`Enabled: ${handler.name}`}
                      checked={handler.enabled}
                      disabled={busy === handler.id}
                      onCheckedChange={(enabled) => void toggle(handler, enabled)}
                    />
                  </TableCell>
                  <TableCell className="font-medium">{handler.name}</TableCell>
                  <TableCell className="font-mono text-xs">{when(handler)}</TableCell>
                  <TableCell>{action(handler)}</TableCell>
                  <TableCell>
                    {handler.last_run ? (
                      <div className="flex flex-col items-start gap-1">
                        <Badge variant={handler.last_run.status === "failed" ? "destructive" : "secondary"}>
                          {handler.last_run.status === "failed" ? "Failed" : "Succeeded"}
                        </Badge>
                        <span className="text-xs text-muted-foreground">
                          {new Date(handler.last_run.created_at).toLocaleString()}
                        </span>
                      </div>
                    ) : (
                      <span className="text-sm text-muted-foreground">Never run</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <RowMenu
                      name={handler.name}
                      items={[
                        { label: "Edit", icon: Pencil, onSelect: () => setEditing(handler) },
                        deleteMenuItem(() => {
                          deletingName.current = handler.name;
                          deletingSchedule.current = boundToSchedule(handler);
                          setDeleting(handler);
                        }),
                      ]}
                    />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      <HandlerSheet
        open={editing !== null}
        handler={editing === "new" ? null : editing}
        onClose={close}
        onSaved={(saved) => {
          replace(saved);
          close();
          toast.success(`Handler “${saved.name}” saved.`);
        }}
      />
      <ConfirmRemove
        removal={
          deleting
            ? {
                title: `Delete “${deletingName.current}”?`,
                description: `Its run history stays on the events.${
                  deletingSchedule.current ? " The schedule keeps recording events." : ""
                }`,
                action: "Delete handler",
                run: remove,
              }
            : null
        }
        onClose={() => setDeleting(null)}
        busy={removing}
      />
    </div>
  );
}
