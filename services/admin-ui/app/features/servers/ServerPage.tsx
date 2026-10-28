import { useEffect, useState } from "react";
import BackLink from "~/components/BackLink";
import { Link, Outlet, useLocation, useOutletContext, useParams } from "react-router";
import OldLinkRedirect from "~/components/OldLinkRedirect";
import SectionLinks from "~/components/SectionLinks";
import { ApiError } from "~/lib/api";
import { getServerPage, type ServerPageData } from "./discovery";
import DiscoverWizard from "~/features/sessions/DiscoverWizard";
import ResourceChats from "~/features/sessions/ResourceChats";
import EntryDocuments from "./EntryDocuments";
import { DraftBadge, DraftRowMenu } from "./ConfirmDraftSheet";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Skeleton } from "~/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

/** What the server's sub-pages get from the layout: the server page's data, `version` (counts reloads) and a reload. */
interface ServerPageContext {
  data: ServerPageData;
  version: number;
  reload: () => void;
}
const useServerPage = () => useOutletContext<ServerPageContext>();

const SECTIONS = [
  { page: "overview", label: "Overview", help: "The services deployed on this server, in each environment." },
  { page: "documents", label: "Documents", help: "Documents about this server and drafts waiting to be published." },
];

/** `/servers/:serverId`: opens Overview; an old link (`?view=overview|documents`) opens its sub-page, keeping the rest of
 * the query (an open chat). */
export const ServerIndex = () => <OldLinkRedirect pages={{ documents: "documents" }} fallback="overview" />;

/** One server (`/servers/:serverId`): a header (details, SSH state; Chat and Discover on the right), the sub-pages
 * Overview and Documents as links (they keep the query, so an open chat stays open) and the open sub-page
 * (ResourceChats: the chat opens beside the page on wide screens). */
export default function ServerLayout() {
  const { serverId } = useParams();
  return <ServerPage key={serverId} serverId={serverId!} />;
}

function ServerPage({ serverId }: { serverId: string }) {
  const { search } = useLocation();
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
        <BackLink to="/servers">All servers</BackLink>
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      </div>
    ) : (
      <Skeleton className="h-60" />
    );
  const { server, environments, discovery } = data;
  const blocked = discovery.ready ? null : (discovery.reason ?? "This server is not ready for a chat.");
  const info = (
    <>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <div className="flex flex-col gap-1">
        <BackLink to="/servers">All servers</BackLink>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="break-words text-xl font-semibold">{server.name}</h2>
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
            {server.ssh_allowed ? "Agent SSH enabled" : "Agent SSH disabled"}
          </Badge>
        </div>
        {discovery.reason && <p className="text-sm text-muted-foreground">{discovery.reason}</p>}
      </div>
    </>
  );
  return (
    <ResourceChats
      onSessionChange={() => setVersion((value) => value + 1)}
      resource={{ type: "server", id: serverId, name: server.name }}
      info={info}
      blockedReason={blocked}
      actions={
        <DiscoverWizard resource={{ type: "server", id: serverId, name: server.name }} blockedReason={blocked} />
      }
    >
      <SectionLinks
        label="Server sections"
        sections={SECTIONS.map(({ page, ...section }) => ({ ...section, to: { pathname: page, search } }))}
      />
      <Outlet context={{ data, version, reload: () => setVersion((value) => value + 1) } satisfies ServerPageContext} />
    </ResourceChats>
  );
}

/** The documents about the server. */
export function ServerDocuments() {
  const { data, version, reload } = useServerPage();
  return (
    <EntryDocuments
      entry={{ kind: "server", id: data.server.id, name: data.server.name }}
      refresh={version}
      onChanged={reload}
    />
  );
}

/** The services deployed on the server (a draft deployment with its badge and Confirm / Discard). */
export function ServerOverview() {
  const {
    data: { deployments },
    reload,
  } = useServerPage();
  return (
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
                    <Link className="underline" to={`/services/${encodeURIComponent(item.service_id)}/view`}>
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
                  <TableCell className="text-right">
                    {item.status === "draft" && (
                      <DraftRowMenu
                        name={item.service_name}
                        draft={{
                          confirm: { kind: "deployment", id: item.id, revision: item.revision },
                          onDone: reload,
                        }}
                        items={[]}
                      />
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </section>
  );
}
