import { useState } from "react";
import { Link } from "react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { ArrowLeft, ArrowRight, Pencil, Plus } from "lucide-react";
import type { Connection } from "./inventory";
import { useServicePage } from "./ServiceLayout";
import { servicePath } from "./ServicePanel";
import { draftOf } from "./rows";
import {
  ALL,
  connectionTable,
  visibleConnections,
  type ConnectionRow,
  type ConnectionSort,
  type ConnectionView,
} from "./connectionTable";
import AddConnectionSheet from "./AddConnectionSheet";
import { DraftBadge, DraftRowMenu } from "./ConfirmDraftSheet";
import DataTable, { FILTER_WIDTH, sortHeader as header, type Sort } from "~/components/DataTable";
import { deleteMenuItem } from "~/components/RowMenu";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";

const NONE = "—";

/** The network connections of the service's deployments in a table: direction (out or in), the other end's server and
 * service (a link, with the connection's description under it), port, protocol and, when the service has more than one
 * deployment, its own deployment's server; each row with Edit, Remove (asked first) and, for a draft, its badge with
 * Confirm / Discard. Search, Direction and Protocol filters, sortable headers. Add connection starts from one of its
 * deployments (it needs one). */
export default function ServiceConnections() {
  const { service, services, deployments, connections, changed, conflict, ask } = useServicePage();
  const [open, setOpen] = useState<{ connection?: Connection } | null>(null);
  const [view, setView] = useState<ConnectionView>({
    search: "",
    direction: ALL,
    protocol: ALL,
    sort: "direction",
    descending: false,
  });
  const from = deployments.map((item) => item.id);
  const rows = connectionTable(services, connections, from, service.id);
  const shown = visibleConnections(rows, view);
  const change = (next: Partial<ConnectionView>) => setView((current) => ({ ...current, ...next }));
  const done = () => {
    setOpen(null);
    changed();
  };

  const sortHeader = (label: string, key: ConnectionSort) =>
    header(label, key, { key: view.sort, descending: view.descending }, (next: Sort<ConnectionSort>) =>
      change({ sort: next.key, descending: next.descending }),
    );
  const columns: ColumnDef<ConnectionRow, unknown>[] = [
    {
      id: "direction",
      header: () => sortHeader("Direction", "direction"),
      cell: ({ row: { original: row } }) => (
        <Badge variant="outline">
          {row.direction === "out" ? <ArrowRight aria-hidden /> : <ArrowLeft aria-hidden />}
          {row.direction === "out" ? "Out" : "In"}
        </Badge>
      ),
    },
    {
      id: "server",
      header: () => sortHeader("Server", "server"),
      cell: ({ row: { original: row } }) => <span className="[overflow-wrap:anywhere]">{row.server ?? NONE}</span>,
    },
    {
      id: "service",
      header: () => sortHeader("Service", "service"),
      cell: ({ row: { original: row } }) => (
        <div className="flex min-w-0 flex-col gap-0.5">
          {row.serviceId ? (
            <Link className="font-medium underline-offset-4 hover:underline" to={servicePath(row.serviceId)}>
              {row.service ?? "an unknown service"}
            </Link>
          ) : (
            <span>{NONE}</span>
          )}
          {row.item.description && (
            <span className="text-sm text-muted-foreground [overflow-wrap:anywhere]">{row.item.description}</span>
          )}
        </div>
      ),
    },
    {
      id: "port",
      header: () => sortHeader("Port", "port"),
      cell: ({ row: { original: row } }) => row.port ?? NONE,
    },
    {
      id: "protocol",
      header: () => sortHeader("Protocol", "protocol"),
      cell: ({ row: { original: row } }) => row.protocol,
    },
    ...(from.length > 1
      ? [
          {
            id: "deployment",
            header: () => sortHeader("Deployment", "deployment"),
            cell: ({ row: { original: row } }) => row.deployment ?? NONE,
          } satisfies ColumnDef<ConnectionRow, unknown>,
        ]
      : []),
    {
      id: "actions",
      header: () => <span className="sr-only">Actions</span>,
      cell: ({ row: { original: row } }) => {
        const draft = draftOf("connection", row.item);
        return (
          <div className="flex items-center justify-end gap-2">
            {draft && <DraftBadge />}
            <DraftRowMenu
              name={row.name}
              draft={draft && { confirm: draft, onDone: changed }}
              items={[
                { label: "Edit", icon: Pencil, onSelect: () => setOpen({ connection: row.item }) },
                deleteMenuItem(
                  () =>
                    ask(
                      "connections",
                      row.item,
                      `the connection ${row.name}`,
                      "The services stay; only this connection goes.",
                    ),
                  "Remove",
                ),
              ]}
            />
          </div>
        );
      },
    },
  ];

  const protocols = [
    ...new Set([...rows.map((row) => row.protocol), ...(view.protocol === ALL ? [] : [view.protocol])]),
  ].sort();
  const toolbar = (
    <>
      <Select value={view.direction} onValueChange={(direction) => change({ direction })}>
        <SelectTrigger className={FILTER_WIDTH} aria-label="Direction">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectItem value={ALL}>All directions</SelectItem>
            <SelectItem value="out">Out</SelectItem>
            <SelectItem value="in">In</SelectItem>
          </SelectGroup>
        </SelectContent>
      </Select>
      <Select value={view.protocol} onValueChange={(protocol) => change({ protocol })}>
        <SelectTrigger className={FILTER_WIDTH} aria-label="Protocol">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectItem value={ALL}>All protocols</SelectItem>
            {protocols.map((protocol) => (
              <SelectItem key={protocol} value={protocol}>
                {protocol}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
    </>
  );

  return (
    <section aria-label="Connections" className="flex min-w-0 flex-col gap-3">
      <DataTable
        columns={columns}
        data={shown}
        search={{ plural: "connections", value: view.search, onChange: (search) => change({ search }) }}
        sort={{ key: view.sort, descending: view.descending }}
        resetKey={`${view.direction}\n${view.protocol}\n${view.sort}\n${view.descending}`}
        toolbar={toolbar}
        actions={
          from.length > 0 && (
            <Button type="button" variant="outline" onClick={() => setOpen({})}>
              <Plus data-icon="inline-start" />
              Add connection
            </Button>
          )
        }
        rowId={(row) => row.key}
        empty={
          rows.length > 0
            ? "No connections match."
            : from.length > 0
              ? "No connections."
              : "No connections. A connection starts from a deployment."
        }
      />
      {open && (
        <AddConnectionSheet
          from={from}
          services={services}
          connection={open.connection}
          onSaved={done}
          onConflict={() => {
            setOpen(null);
            conflict();
          }}
          onClose={() => setOpen(null)}
        />
      )}
    </section>
  );
}
