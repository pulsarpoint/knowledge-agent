import { useState } from "react";
import { Plus } from "lucide-react";
import type { Connection } from "./inventory";
import { useServicePage } from "./ServiceLayout";
import { connectionRows, servicePath } from "./ServicePanel";
import { draftOf, List, Row } from "./rows";
import AddConnectionDialog from "./AddConnectionDialog";
import { Button } from "~/components/ui/button";

/** The network connections of the service's deployments, out (→) and in (←): each links to the other service, with
 * Edit, Remove (asked first) and, for a draft, its badge with Confirm / Discard. Add connection starts from one of its
 * deployments (it needs one). */
export default function ServiceConnections() {
  const { service, services, deployments, connections, changed, conflict, ask } = useServicePage();
  const [open, setOpen] = useState<{ connection?: Connection } | null>(null);
  const from = deployments.map((item) => item.id);
  const rows = connectionRows(services, connections, new Set(from), service.id);
  const done = () => {
    setOpen(null);
    changed();
  };

  return (
    <section aria-label="Connections" className="flex min-w-0 flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-lg font-semibold">Connections</h3>
        {from.length > 0 && (
          <Button type="button" size="sm" variant="outline" onClick={() => setOpen({})}>
            <Plus data-icon="inline-start" />
            Add connection
          </Button>
        )}
      </div>
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {from.length > 0 ? "No connections." : "No connections. A connection starts from a deployment."}
        </p>
      ) : (
        <List>
          {rows.map(({ item, text, to }) => (
            <Row
              key={`${item.id}${text[0]}`}
              text={text}
              href={to ? servicePath(to) : undefined}
              detail={item.description}
              name={text}
              draft={draftOf("connection", item)}
              onDone={changed}
              onEdit={() => setOpen({ connection: item })}
              onRemove={() =>
                ask("connections", item, `the connection ${text}`, "The services stay; only this connection goes.")
              }
            />
          ))}
        </List>
      )}
      {open && (
        <AddConnectionDialog
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
