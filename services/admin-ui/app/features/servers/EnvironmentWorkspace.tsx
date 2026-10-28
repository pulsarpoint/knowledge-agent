import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { ChevronLeft, Pencil, Plus, Unlink } from "lucide-react";
import { ApiError } from "~/lib/api";
import { cn } from "~/lib/utils";
import { getServers, type ServerAsset } from "./api";
import {
  getEnvironmentModel,
  removeServerFromEnvironment,
  type EnvironmentModel,
  type ModelDeployment,
  type ModelServer,
} from "./environments";
import { membershipId } from "./inventory";
import { DraftBadge, DraftRowMenu, type DraftTarget } from "./ConfirmDraftSheet";
import AddServerSheet from "./AddServerSheet";
import AddServiceSheet from "./AddServiceSheet";
import ServerForm from "./ServerForm";
import ServicePanel from "./ServicePanel";
import ConfirmRemove, { type Removal } from "~/components/ConfirmRemove";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "~/components/ui/tabs";

const count = (n: number) => (n === 0 ? "no services" : n === 1 ? "1 service" : `${n} services`);

/** The workspace filter (`?drafts=only|none`; absent: all). */
type DraftFilter = "all" | "only" | "none";
const FILTERS: { value: DraftFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "none", label: "Confirmed" },
  { value: "only", label: "Drafts" },
];
const readFilter = (value: string | null): DraftFilter => (value === "only" || value === "none" ? value : "all");
/** A server row is a draft when the server or its membership of the environment is. */
const serverDraft = (server: ModelServer) => server.status === "draft" || server.membership_status === "draft";
/** A server row confirms its draft membership (which confirms a draft server with it) and discards a draft server
 * (which takes its memberships and deployments with it). */
function serverTargets(environmentId: string, server: ModelServer): { confirm: DraftTarget; discard: DraftTarget } {
  const membership: DraftTarget = { kind: "membership", id: membershipId(environmentId, server.id), revision: null };
  const own: DraftTarget = { kind: "server", id: server.id, revision: server.revision };
  return {
    confirm: server.membership_status === "draft" ? membership : own,
    discard: server.status === "draft" ? own : membership,
  };
}
/** The names of the drafts a new entry here uses: the environment, and the server and its membership. A person's save
 * confirms them. */
function placeDrafts(model: EnvironmentModel, server?: ModelServer): string[] {
  const environment = model.environment;
  return [
    ...(environment.status === "draft" ? [environment.name] : []),
    ...(server?.status === "draft" ? [server.name] : []),
    ...(server?.membership_status === "draft" ? [`${server.name} in ${environment.name}`] : []),
  ];
}
/** Which rows the filter shows: drafts only, confirmed only, or all. Under Drafts a deployment stays when it is a draft
 * or a draft connection (from or to it) or draft relation (from or to its service) is about it, so the service panel
 * shows them; a server stays when it, its membership or one of those deployments is a draft. Whatever the header counts
 * is reachable from the list (the rest is under Other drafts). A confirmed deployment never needs a draft, so a
 * deployment of a draft service is a draft itself. */
function filterRows(filter: DraftFilter, model: EnvironmentModel) {
  const deploymentDraft = (item: ModelDeployment) => item.status === "draft";
  const drafted = new Set<string>();
  for (const item of model.connections)
    if (item.status === "draft")
      for (const id of [item.from_deployment_id, item.to_deployment_id]) id && drafted.add(id);
  const draftedServices = new Set<string>();
  for (const item of model.relations)
    if (item.status === "draft")
      for (const id of [item.from_service_id, item.to_service_id]) id && draftedServices.add(id);
  const hasDrafts = (item: ModelDeployment) =>
    deploymentDraft(item) || drafted.has(item.id) || draftedServices.has(item.service_id);
  const keepDeployment = (item: ModelDeployment) =>
    filter === "all" || (filter === "only" ? hasDrafts(item) : !deploymentDraft(item));
  const keepServer = (item: ModelServer) =>
    filter === "all" ||
    (filter === "none" ? !serverDraft(item) : serverDraft(item) || item.deployments.some(hasDrafts));
  return { deploymentDraft, keepDeployment, keepServer };
}

/** The environment's workspace: its servers, the services on the selected server, and the selected service. The
 * selection lives in the URL (`?server=&service=`); an id that is no longer in the environment selects nothing. Narrow
 * screens show the deepest selected column, with a back link. Every change reloads the model, then calls `onChanged`
 * (the page reloads its documents list, which has its servers' documents). A new `refresh` number reloads the model too
 * (the page bumps it after a chat turn, whose agent may have written drafts). */
export default function EnvironmentWorkspace({
  environmentId,
  onChanged,
  refresh = 0,
}: {
  environmentId: string;
  onChanged?: () => void;
  refresh?: number;
}) {
  const [model, setModel] = useState<EnvironmentModel | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [removal, setRemoval] = useState<Removal | null>(null);
  const [adding, setAdding] = useState<"server" | "service" | null>(null);
  const [editing, setEditing] = useState<ServerAsset | null>(null);
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();

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
  }, [environmentId, version, refresh]);
  const reload = () => {
    setVersion((value) => value + 1);
    onChanged?.();
  };

  /** Changes the selection, keeping the page's other params (such as the open chat). */
  const select = (changes: { server?: string | null; service?: string | null; drafts?: string | null }) =>
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

  const filter = readFilter(params.get("drafts"));
  const { deploymentDraft, keepDeployment, keepServer } = filterRows(filter, model);
  const servers = model.servers.filter(keepServer);
  const server = model.servers.find((item) => item.id === params.get("server")) ?? null;
  const shownDeployments = server?.deployments.filter(keepDeployment) ?? [];
  // Drafts with no row in the columns: a draft environment and draft external services.
  const otherDrafts =
    filter === "none"
      ? []
      : [
          ...(model.environment.status === "draft"
            ? [{ ...model.environment, kind: "environment" as const, label: "Environment" }]
            : []),
          ...model.external_services
            .filter((item) => item.status === "draft")
            .map((item) => ({ ...item, kind: "service" as const, label: item.address || "External service" })),
        ];
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
      <ConfirmRemove removal={removal} onClose={() => setRemoval(null)} />
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
      <Tabs
        value={filter}
        onValueChange={(value) => select({ drafts: value === "all" ? null : value })}
        className="self-start"
      >
        <TabsList aria-label="Show">
          {FILTERS.map((item) => (
            <TabsTrigger key={item.value} value={item.value}>
              {item.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      {otherDrafts.length > 0 && (
        <section aria-label="Other drafts" className="flex flex-col gap-2 rounded-lg border p-3">
          <h2 className="text-lg font-semibold">Other drafts</h2>
          <ul className="flex flex-col gap-1">
            {otherDrafts.map((item) => (
              <li key={`${item.kind}:${item.id}`} className="flex items-center gap-1">
                <span className="flex min-w-0 flex-1 flex-col px-2 py-1.5 text-sm">
                  <span className="flex max-w-full items-center gap-2">
                    <span className="font-medium [overflow-wrap:anywhere]">{item.name}</span>
                    <DraftBadge />
                  </span>
                  <span className="text-xs text-muted-foreground">{item.label}</span>
                </span>
                <DraftRowMenu
                  name={item.name}
                  draft={{
                    confirm: { kind: item.kind, id: item.id, revision: item.revision },
                    onDone: (action) =>
                      action === "discard" && item.kind === "environment" ? navigate("/environments") : reload(),
                  }}
                  items={[]}
                />
              </li>
            ))}
          </ul>
        </section>
      )}
      <div className="grid min-w-0 gap-4 md:grid-cols-[minmax(12rem,1fr)_minmax(14rem,1fr)_minmax(0,2fr)] md:items-start">
        <section aria-label="Servers" className={cn("min-w-0 flex-col gap-2 rounded-lg border p-3", shown(1))}>
          <h2 className="text-lg font-semibold">Servers</h2>
          {model.servers.length === 0 ? (
            <p className="text-sm text-muted-foreground">No servers in this environment yet.</p>
          ) : servers.length === 0 ? (
            <p className="text-sm text-muted-foreground">No servers match this filter.</p>
          ) : (
            <ul className="flex flex-col gap-1">
              {servers.map((item) => (
                <li key={item.id} className="flex items-center gap-1">
                  <button
                    type="button"
                    aria-pressed={item.id === server?.id}
                    className="flex min-w-0 flex-1 flex-col items-start rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted/50 aria-pressed:bg-muted"
                    onClick={() => select({ server: item.id, service: null })}
                  >
                    <span className="flex max-w-full items-center gap-2">
                      <span className="font-medium [overflow-wrap:anywhere]">{item.name}</span>
                      {serverDraft(item) && <DraftBadge />}
                    </span>
                    <span className="text-xs text-muted-foreground">{count(item.deployments.length)}</span>
                  </button>
                  <DraftRowMenu
                    name={item.name}
                    draft={serverDraft(item) ? { ...serverTargets(environmentId, item), onDone: reload } : undefined}
                    items={[
                      { label: "Edit", icon: Pencil, onSelect: () => void edit(item) },
                      {
                        label: "Remove from environment",
                        icon: Unlink,
                        destructive: true,
                        onSelect: () =>
                          setRemoval({
                            title: `Remove ${item.name} from ${model.environment.name}?`,
                            description: "The server stays recorded; it is only no longer in this environment.",
                            action: "Remove from environment",
                            run: () => void remove(item),
                          }),
                      },
                    ]}
                  />
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
            ) : shownDeployments.length === 0 ? (
              <p className="text-sm text-muted-foreground">No services match this filter.</p>
            ) : (
              <ul className="flex flex-col gap-1">
                {shownDeployments.map((item) => (
                  <li key={item.id} className="flex items-center gap-1">
                    <button
                      type="button"
                      aria-pressed={item.id === deployment?.id}
                      className="flex min-w-0 flex-1 flex-col items-start rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted/50 aria-pressed:bg-muted"
                      onClick={() => select({ service: item.service_id })}
                    >
                      <span className="flex max-w-full items-center gap-2">
                        <span className="font-medium [overflow-wrap:anywhere]">{item.service_name}</span>
                        {deploymentDraft(item) && <DraftBadge />}
                      </span>
                      {item.runtime_name && item.runtime_name !== item.service_name && (
                        <span className="max-w-full truncate text-xs text-muted-foreground" title={item.runtime_name}>
                          {item.runtime_name}
                        </span>
                      )}
                    </button>
                    {deploymentDraft(item) && (
                      <DraftRowMenu
                        name={item.service_name}
                        draft={{
                          confirm: { kind: "deployment", id: item.id, revision: item.revision },
                          onDone: reload,
                        }}
                        items={[]}
                      />
                    )}
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
              refresh={refresh}
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
        <AddServerSheet
          environmentId={environmentId}
          memberIds={model.servers.map((item) => item.id)}
          uses={placeDrafts(model)}
          onClose={() => setAdding(null)}
          onAdded={(id) => {
            setAdding(null);
            select({ server: id, service: null });
            reload();
          }}
        />
      )}
      {adding === "service" && server && (
        <AddServiceSheet
          environmentId={environmentId}
          server={server}
          uses={placeDrafts(model, server)}
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
