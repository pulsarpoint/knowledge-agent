import { useEffect, useRef, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { Pencil, Plus, Workflow } from "lucide-react";
import { useSearchParams } from "react-router";
import { toast } from "sonner";
import { ApiError } from "~/lib/api";
import { deleteHandler, boundToSchedule, getHandlers, saveHandler, toDraft, type EventHandler } from "./handlers";
import HandlerSheet from "./HandlerSheet";
import ConfirmRemove from "~/components/ConfirmRemove";
import DataTable, { sortHeader, sortRows, TableEmpty, type Sort } from "~/components/DataTable";
import RowMenu, { deleteMenuItem } from "~/components/RowMenu";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Switch } from "~/components/ui/switch";

const text = (reason: unknown, fallback: string) => (reason instanceof ApiError ? reason.message : fallback);
const when = (handler: EventHandler) =>
  `${handler.type_pattern} · ${handler.conditions.length} condition${handler.conditions.length === 1 ? "" : "s"}`;
const action = (handler: EventHandler) =>
  handler.action === "emit"
    ? `Emit ${handler.emit?.type ?? ""}`
    : handler.agent_from === "assignee"
      ? "Send to the assignee’s agent"
      : `Send to ${handler.agent_name ?? handler.agent_id}`;

type HandlerSort = "enabled" | "name" | "last";
const byName = (a: EventHandler, b: EventHandler) => a.name.localeCompare(b.name);
const HANDLER_ORDER: Record<HandlerSort, (a: EventHandler, b: EventHandler) => number> = {
  enabled: (a, b) => Number(b.enabled) - Number(a.enabled) || byName(a, b),
  name: byName,
  last: (a, b) => (a.last_run?.created_at ?? "").localeCompare(b.last_run?.created_at ?? "") || byName(a, b),
};

/** Handlers: a table (Enabled switch, name, when, action, last run; Edit and Delete in the row menu) with search and
 * sortable headers; New handler and Edit open a right Sheet. ?handler=<id> opens that handler (Jira → Settings →
 * Users and agents links here). */
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
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<Sort<HandlerSort>>({ key: "name", descending: false });

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

  const query = search.trim().toLowerCase();
  const shown = sortRows(
    (handlers ?? []).filter((handler) =>
      [handler.name, handler.type_pattern, action(handler)].some((value) => value.toLowerCase().includes(query)),
    ),
    sort,
    HANDLER_ORDER,
  );
  const newHandler = (
    <Button onClick={() => setEditing("new")}>
      <Plus data-icon="inline-start" />
      New handler
    </Button>
  );
  const columns: ColumnDef<EventHandler, unknown>[] = [
    {
      id: "enabled",
      header: () => sortHeader("Enabled", "enabled", sort, setSort),
      cell: ({ row: { original: handler } }) => (
        <Switch
          aria-label={`Enabled: ${handler.name}`}
          checked={handler.enabled}
          disabled={busy === handler.id}
          onCheckedChange={(enabled) => void toggle(handler, enabled)}
        />
      ),
    },
    {
      id: "name",
      header: () => sortHeader("Name", "name", sort, setSort),
      cell: ({ row: { original: handler } }) => <span className="font-medium">{handler.name}</span>,
    },
    {
      id: "when",
      header: "When",
      cell: ({ row: { original: handler } }) => <span className="font-mono text-xs">{when(handler)}</span>,
    },
    { id: "action", header: "Action", cell: ({ row: { original: handler } }) => action(handler) },
    {
      id: "last",
      header: () => sortHeader("Last run", "last", sort, setSort),
      cell: ({ row: { original: handler } }) =>
        handler.last_run ? (
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
        ),
    },
    {
      id: "actions",
      header: () => <span className="sr-only">Actions</span>,
      cell: ({ row: { original: handler } }) => (
        <div className="flex justify-end">
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
        </div>
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex justify-end">{newHandler}</div>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <DataTable
        columns={columns}
        data={shown}
        loading={handlers === null}
        rowId={(handler) => handler.id}
        search={{ plural: "handlers", value: search, onChange: setSearch }}
        sort={sort}
        resetKey={`${sort.key}\n${sort.descending}`}
        empty={
          handlers?.length === 0 ? (
            error ? (
              "Could not load handlers."
            ) : (
              <TableEmpty
                icon={Workflow}
                title="No handlers yet"
                description="A handler turns events into new events or agent runs."
              >
                {newHandler}
              </TableEmpty>
            )
          ) : (
            "No handlers match."
          )
        }
      />
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
