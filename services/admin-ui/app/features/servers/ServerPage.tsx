import { useEffect, useState } from "react";
import { Link } from "react-router";
import { ApiError } from "~/lib/api";
import { getServerPage, type ServerPageData } from "./discovery";
import DiscoverWizard from "~/features/sessions/DiscoverWizard";
import ResourceChats from "~/features/sessions/ResourceChats";
import EntryDocuments from "./EntryDocuments";
import { DraftActions, DraftBadge } from "./ConfirmDraftSheet";
import { RowActions } from "./RowAction";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Skeleton } from "~/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

/** One server: a header (details, SSH state; Chat and Discover on the right) and tabs Overview (the services
 * deployed on it; a draft deployment with its badge and Confirm / Discard), Documents and Chats (ResourceChats: the chat opens beside the page on wide screens). */
export default function ServerPage({ serverId }: { serverId: string }) {
  const [data, setData] = useState<ServerPageData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    getServerPage(serverId, controller.signal)
      .then((page) => {
        if (controller.signal.aborted) return;
        setData(page);
        setError(null);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e instanceof ApiError ? e.message : "Could not load the server.");
      });
    return () => controller.abort();
  }, [serverId, version]);

  if (!data)
    return error ? (
      <div className="flex flex-col gap-3">
        <Link className="self-start text-sm underline" to="/servers">
          Servers
        </Link>
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      </div>
    ) : (
      <Skeleton className="h-60" />
    );
  const { server, environments, deployments, discovery } = data;
  const blocked = discovery.ready ? null : (discovery.reason ?? "This server is not ready for a chat.");
  const info = (
    <>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <div className="flex flex-col gap-1">
        <Link className="self-start text-sm underline" to="/servers">
          Servers
        </Link>
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="break-words text-2xl font-semibold">{server.name}</h1>
          {server.status === "draft" && <DraftBadge />}
        </div>
        <p className="font-mono text-sm">{server.hostname}</p>
        {server.description && <p className="text-sm text-muted-foreground">{server.description}</p>}
        <div className="flex flex-wrap gap-1">
          {environments.map((item) => (
            <Link key={item.id} to={`/environments/${encodeURIComponent(item.id)}`}>
              <Badge variant="secondary">{item.name}</Badge>
            </Link>
          ))}
          <Badge variant={data.ssh_ready ? "secondary" : "outline"}>
            {data.ssh_ready ? "SSH set up" : "SSH not set up"}
          </Badge>
          <Badge variant={server.ssh_allowed ? "secondary" : "outline"}>
            {server.ssh_allowed ? "Agent SSH allowed" : "Agent SSH off"}
          </Badge>
        </div>
        {discovery.reason && <p className="text-sm text-muted-foreground">{discovery.reason}</p>}
      </div>
    </>
  );
  const services = (
    <>
      <section aria-label="Services" className="flex flex-col gap-2">
        <h2 className="text-lg font-semibold">Services</h2>
        {deployments.length === 0 ? (
          <p className="text-sm text-muted-foreground">No services are recorded on this server.</p>
        ) : (
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Service</TableHead>
                  <TableHead>Environment</TableHead>
                  <TableHead>Runtime</TableHead>
                  <TableHead>Port</TableHead>
                  <TableHead>Version</TableHead>
                  <TableHead>
                    <span className="sr-only">Actions</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {deployments.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell>
                      <Link className="underline" to={`/services/${encodeURIComponent(item.service_id)}`}>
                        {item.service_name}
                      </Link>
                      {item.status === "draft" && (
                        <span className="ml-2 inline-flex align-middle">
                          <DraftBadge />
                        </span>
                      )}
                    </TableCell>
                    <TableCell>{item.environment}</TableCell>
                    <TableCell>{[item.runtime, item.runtime_name].filter(Boolean).join(" · ") || "—"}</TableCell>
                    <TableCell>{item.port ?? "—"}</TableCell>
                    <TableCell>{item.deployed_version || "—"}</TableCell>
                    <TableCell>
                      {item.status === "draft" && (
                        <RowActions>
                          <DraftActions
                            name={item.service_name}
                            confirm={{ kind: "deployment", id: item.id, revision: item.revision }}
                            onDone={() => setVersion((value) => value + 1)}
                          />
                        </RowActions>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </section>
    </>
  );
  return (
    <ResourceChats
      onSessionChange={() => setVersion((value) => value + 1)}
      resource={{ type: "server", id: serverId, name: server.name }}
      info={info}
      blockedReason={blocked}
      pages={[
        { value: "overview", label: "Overview", content: services },
        {
          value: "documents",
          label: "Documents",
          content: (
            <EntryDocuments
              entry={{ kind: "server", id: serverId, name: server.name }}
              refresh={version}
              onChanged={() => setVersion((value) => value + 1)}
            />
          ),
        },
      ]}
      actions={
        <DiscoverWizard resource={{ type: "server", id: serverId, name: server.name }} blockedReason={blocked} />
      }
    />
  );
}
