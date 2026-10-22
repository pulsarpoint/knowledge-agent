import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { ChevronLeft, Pencil, Plus, Unlink } from "lucide-react";
import { ApiError } from "~/lib/api";
import { cn } from "~/lib/utils";
import { getServers, type ServerAsset } from "./api";
import {
  getEnvironmentModel,
  removeServerFromEnvironment,
  type EnvironmentModel,
  type ModelServer,
} from "./environments";
import AddServerDialog from "./AddServerDialog";
import AddServiceDialog from "./AddServiceDialog";
import ServerForm from "./ServerForm";
import ServicePanel from "./ServicePanel";
import { RowAction, RowActions } from "./RowAction";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";

const count = (n: number) => (n === 0 ? "no services" : n === 1 ? "1 service" : `${n} services`);

/** The environment's workspace: its servers, the services on the selected server, and the selected service. The
 * selection lives in the URL (`?server=&service=`); an id that is no longer in the environment selects nothing. Narrow
 * screens show the deepest selected column, with a back link. Every change reloads the model, then calls `onChanged`
 * (the page reloads its documents list, which has its servers' documents). */
export default function EnvironmentWorkspace({
  environmentId,
  onChanged,
}: {
  environmentId: string;
  onChanged?: () => void;
}) {
  const [model, setModel] = useState<EnvironmentModel | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [adding, setAdding] = useState<"server" | "service" | null>(null);
  const [editing, setEditing] = useState<ServerAsset | null>(null);
  const [params, setParams] = useSearchParams();

  useEffect(() => {
    const controller = new AbortController();
    getEnvironmentModel(environmentId, controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        setModel(value);
        setLoadError(null);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setLoadError(e instanceof ApiError ? e.message : "Could not load the environment's servers and services.");
      });
    return () => controller.abort();
  }, [environmentId, version]);
  const reload = () => {
    setVersion((value) => value + 1);
    onChanged?.();
  };

  /** Changes the selection, keeping the page's other params (such as the open chat). */
  const select = (changes: { server?: string | null; service?: string | null }) =>
    setParams((current) => {
      const next = new URLSearchParams(current);
      for (const [key, value] of Object.entries(changes)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      return next;
    });

  if (!model)
    return loadError ? (
      <Alert variant="destructive">
        <AlertDescription>{loadError}</AlertDescription>
      </Alert>
    ) : (
      <Skeleton className="h-60" />
    );

  const server = model.servers.find((item) => item.id === params.get("server")) ?? null;
  const deployment = server?.deployments.find((item) => item.service_id === params.get("service")) ?? null;
  const column = deployment ? 3 : server ? 2 : 1;
  // Wide screens show every column; narrow ones only the deepest selected one.
  const shown = (n: number) => (column === n ? "flex" : "hidden md:flex");

  async function edit(item: ModelServer) {
    setError(null);
    try {
      const found = (await getServers()).servers.find((value) => value.id === item.id);
      if (found) setEditing(found);
      else reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not load the server.");
    }
  }
  async function remove(item: ModelServer) {
    setError(null);
    try {
      await removeServerFromEnvironment(environmentId, item.id);
      if (item.id === server?.id) select({ server: null, service: null });
      reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not remove the server from the environment.");
    }
  }

  return (
    <div className="flex flex-col gap-3">
      {loadError && (
        <Alert variant="destructive">
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      )}
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <div className="grid min-w-0 gap-4 md:grid-cols-[minmax(12rem,1fr)_minmax(14rem,1fr)_minmax(0,2fr)] md:items-start">
        <section aria-label="Servers" className={cn("min-w-0 flex-col gap-2 rounded-lg border p-3", shown(1))}>
          <h2 className="text-lg font-semibold">Servers</h2>
          {model.servers.length === 0 ? (
            <p className="text-sm text-muted-foreground">No servers in this environment yet.</p>
          ) : (
            <ul className="flex flex-col gap-1">
              {model.servers.map((item) => (
                <li key={item.id} className="flex items-center gap-1">
                  <button
                    type="button"
                    aria-pressed={item.id === server?.id}
                    className="flex min-w-0 flex-1 flex-col items-start rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted/50 aria-pressed:bg-muted"
                    onClick={() => select({ server: item.id, service: null })}
                  >
                    <span className="max-w-full font-medium [overflow-wrap:anywhere]">{item.name}</span>
                    <span className="text-xs text-muted-foreground">{count(item.deployments.length)}</span>
                  </button>
                  <RowActions>
                    <RowAction label="Edit" name={item.name} onClick={() => void edit(item)}>
                      <Pencil />
                    </RowAction>
                    <RowAction label="Remove from environment" name={item.name} onClick={() => void remove(item)}>
                      <Unlink />
                    </RowAction>
                  </RowActions>
                </li>
              ))}
            </ul>
          )}
          <Button type="button" size="sm" variant="outline" className="self-start" onClick={() => setAdding("server")}>
            <Plus data-icon="inline-start" />
            Add server
          </Button>
        </section>

        {server ? (
          <section
            aria-label={`Services on ${server.name}`}
            className={cn("min-w-0 flex-col gap-2 rounded-lg border p-3", shown(2))}
          >
            <Button
              type="button"
              variant="link"
              className="h-auto self-start p-0 md:hidden"
              onClick={() => select({ server: null, service: null })}
            >
              <ChevronLeft data-icon="inline-start" />
              Back to servers
            </Button>
            <h2 className="text-lg font-semibold [overflow-wrap:anywhere]">Services on {server.name}</h2>
            <Link className="self-start text-sm underline" to={`/servers/${encodeURIComponent(server.id)}`}>
              {server.hostname || "Server page"}
            </Link>
            {server.deployments.length === 0 ? (
              <p className="text-sm text-muted-foreground">No services on this server in this environment.</p>
            ) : (
              <ul className="flex flex-col gap-1">
                {server.deployments.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      aria-pressed={item.id === deployment?.id}
                      className="flex w-full min-w-0 flex-col items-start rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted/50 aria-pressed:bg-muted"
                      onClick={() => select({ service: item.service_id })}
                    >
                      <span className="max-w-full font-medium [overflow-wrap:anywhere]">{item.service_name}</span>
                      {item.runtime_name && item.runtime_name !== item.service_name && (
                        <span className="max-w-full truncate text-xs text-muted-foreground" title={item.runtime_name}>
                          {item.runtime_name}
                        </span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="self-start"
              onClick={() => setAdding("service")}
            >
              <Plus data-icon="inline-start" />
              Add service
            </Button>
          </section>
        ) : (
          <p className="hidden text-sm text-muted-foreground md:block">Choose a server to see its services.</p>
        )}

        {server && deployment ? (
          <div className={cn("min-w-0 flex-col gap-2", shown(3))}>
            <Button
              type="button"
              variant="link"
              className="h-auto self-start p-0 md:hidden"
              onClick={() => select({ service: null })}
            >
              <ChevronLeft data-icon="inline-start" />
              Back to services on {server.name}
            </Button>
            <ServicePanel
              key={deployment.service_id}
              serviceId={deployment.service_id}
              environmentId={environmentId}
              serverId={server.id}
              onChanged={reload}
            />
          </div>
        ) : (
          server && (
            <p className="hidden text-sm text-muted-foreground md:block">Choose a service to see its details.</p>
          )
        )}
      </div>

      {adding === "server" && (
        <AddServerDialog
          environmentId={environmentId}
          memberIds={model.servers.map((item) => item.id)}
          onClose={() => setAdding(null)}
          onAdded={(id) => {
            setAdding(null);
            select({ server: id, service: null });
            reload();
          }}
        />
      )}
      {adding === "service" && server && (
        <AddServiceDialog
          environmentId={environmentId}
          server={server}
          onClose={() => setAdding(null)}
          onAdded={(id) => {
            setAdding(null);
            select({ service: id });
            reload();
          }}
        />
      )}
      {editing && (
        <ServerForm
          server={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            reload();
          }}
        />
      )}
    </div>
  );
}
