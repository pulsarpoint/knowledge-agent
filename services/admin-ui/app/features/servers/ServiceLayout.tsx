import { useEffect, useState } from "react";
import { Link, Outlet, useLocation, useOutletContext, useParams } from "react-router";
import { ArrowLeft } from "lucide-react";
import { ApiError } from "~/lib/api";
import {
  deleteItem,
  getServiceConnections,
  getServiceDeployments,
  getServiceRelations,
  isStale,
  type Connection,
  type Deployment,
  type ItemKind,
  type Relation,
  type RelationKind,
} from "./inventory";
import { getServices, type ServiceAsset } from "./services";
import { serviceRoles, serviceTypes } from "./serviceClassification";
import { connectionRows } from "./ServicePanel";
import { CONFLICT, removeItem } from "./rows";
import ConfirmRemove, { type Removal } from "./ConfirmRemove";
import { DraftBadge } from "./ConfirmDraftSheet";
import DiscoverWizard from "~/features/sessions/DiscoverWizard";
import ResourceChats from "~/features/sessions/ResourceChats";
import SectionLinks from "~/components/SectionLinks";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";

/** What each sub-page means, shown under the tab links. */
const SECTION_HELP: Record<string, string> = {
  view: "A summary of the service: what it is, who owns it and where it runs.",
  edit: "Change the service's own details. Relations and deployments are managed on their own tabs.",
  code: "The code projects this service is built from, and the libraries those projects use.",
  "depends-on": "Services this service needs to work. If one of them is down, this service is affected.",
  "used-by": "Services that depend on this one. They are affected when this service is down or changes.",
  "part-of": "Larger services this service belongs to, for example a component of a platform.",
  parts: "Services that are components of this one.",
  connections:
    "Network traffic to and from this service's deployments: which host and port it talks to, and who talks to it.",
  documents: "Documents about this service, including its inventory document and drafts waiting to be published.",
};

/** The four relation tabs: which relations each lists, and whether this service is their "from" end. */
export const RELATION_TABS = {
  "depends-on": { label: "Depends on", kind: "depends_on", outgoing: true },
  "used-by": { label: "Used by", kind: "depends_on", outgoing: false },
  "part-of": { label: "Part of", kind: "part_of", outgoing: true },
  parts: { label: "Parts", kind: "part_of", outgoing: false },
} as const satisfies Record<string, { label: string; kind: RelationKind; outgoing: boolean }>;
export type RelationTab = keyof typeof RELATION_TABS;

/** The relations a tab lists: of its kind, with the service at its end. */
export const tabRelations = (serviceId: string, relations: Relation[], tab: RelationTab) => {
  const { kind, outgoing } = RELATION_TABS[tab];
  return relations.filter(
    (item) => item.kind === kind && (outgoing ? item.from_service_id : item.to_service_id) === serviceId,
  );
};
/** The code projects the service is built from (its built_from relations). */
export const builtFrom = (serviceId: string, relations: Relation[]) =>
  relations.filter((item) => item.kind === "built_from" && item.from_service_id === serviceId);

/** What the service's sub-pages get from the layout: the service and every service (for names), its relations,
 * deployments and connections (loaded once, reloaded after a change or a turn of the page chat), `version` (counts
 * reloads), `changed` (after a change: clears the error and reloads), `conflict` (a stale revision: reloads and says
 * so), `ask` (a removal, confirmed first) and `setNotice`. */
export interface ServicePageContext {
  service: ServiceAsset;
  services: ServiceAsset[];
  relations: Relation[];
  deployments: Deployment[];
  connections: Connection[];
  version: number;
  changed: () => void;
  conflict: () => void;
  ask: (kind: ItemKind, item: { id: string; revision: number }, what: string, description: string) => void;
  setNotice: (notice: string) => void;
}
export const useServicePage = () => useOutletContext<ServicePageContext>();

interface Data {
  services: ServiceAsset[];
  relations: Relation[];
  deployments: Deployment[];
  connections: Connection[];
}
const message = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

/** The way back to the services list. */
export const AllServices = () => (
  <Button asChild variant="link" className="self-start px-0">
    <Link to="/services">
      <ArrowLeft />
      All services
    </Link>
  </Button>
);

/** One service (`/services/:serviceId`): a header (All services, name, type and role badges, the address of an external
 * service; Chat and Discover…), the sub-pages View, Edit, Code, Depends on, Used by, Part of, Parts, Connections and
 * Documents as links with counts (they keep the query, so an open chat stays open) and the open sub-page. */
export default function ServiceLayout() {
  const { serviceId } = useParams();
  return <ServicePage key={serviceId} serviceId={serviceId!} />;
}

function ServicePage({ serviceId }: { serviceId: string }) {
  const { pathname, search } = useLocation();
  const [data, setData] = useState<Data | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [version, setVersion] = useState(0);
  const [removal, setRemoval] = useState<Removal | null>(null);
  const reload = () => setVersion((value) => value + 1);
  // A notice belongs to the tab it was given on.
  useEffect(() => setNotice(""), [pathname]);

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    Promise.all([
      getServices(signal),
      getServiceRelations(serviceId, signal),
      getServiceDeployments(serviceId, signal),
      getServiceConnections(serviceId, signal),
    ])
      .then(([{ services }, { relations }, { deployments }, { connections }]) => {
        if (signal.aborted) return;
        setData({ services, relations, deployments, connections });
        setLoadError(null);
      })
      .catch((e) => {
        if (!signal.aborted) setLoadError(message(e, "Could not load the service."));
      });
    return () => controller.abort();
  }, [serviceId, version]);

  const changed = () => {
    setError(null);
    setNotice("");
    reload();
  };
  const conflict = () => {
    reload();
    setError(CONFLICT);
  };
  const ask = (kind: ItemKind, item: { id: string; revision: number }, what: string, description: string) =>
    setRemoval({ title: `Remove ${what}?`, description, action: "Remove", run: () => void remove(kind, item) });
  const remove = (kind: ItemKind, item: { id: string; revision: number }) => removeItem(kind, item, changed, setError);

  const service = data?.services.find((item) => item.id === serviceId);
  if (!data || !service)
    return loadError || data ? (
      <div className="flex flex-col gap-3">
        <AllServices />
        <Alert variant={loadError ? "destructive" : "default"}>
          <AlertDescription>{loadError ?? "This service is no longer recorded."}</AlertDescription>
        </Alert>
      </div>
    ) : (
      <Skeleton className="h-60" />
    );

  const count = (n: number) => ` (${n})`;
  const connections = connectionRows(
    data.services,
    data.connections,
    new Set(data.deployments.map((item) => item.id)),
    serviceId,
  );
  const page = pathname.split("/").filter(Boolean)[2] ?? "view";
  const sections = [
    { page: "view", label: "View" },
    { page: "edit", label: "Edit" },
    { page: "code", label: `Code${count(builtFrom(serviceId, data.relations).length)}` },
    ...(Object.keys(RELATION_TABS) as RelationTab[]).map((tab) => ({
      page: tab,
      label: `${RELATION_TABS[tab].label}${count(tabRelations(serviceId, data.relations, tab).length)}`,
    })),
    { page: "connections", label: `Connections${count(connections.length)}` },
    { page: "documents", label: "Documents" },
  ];
  const resource = { type: "service" as const, id: serviceId, name: service.name };
  const info = (
    <div data-testid="service-header" className="flex flex-col gap-1">
      <AllServices />
      <h2 className="break-words text-xl font-semibold">{service.name}</h2>
      <div className="flex flex-wrap gap-1">
        <Badge variant="secondary">{serviceTypes[service.service_type]}</Badge>
        <Badge variant="outline">{serviceRoles[service.service_role].label}</Badge>
        {service.status === "draft" && <DraftBadge />}
      </div>
      {service.address && <p className="break-all font-mono text-sm">{service.address}</p>}
    </div>
  );
  return (
    <ResourceChats
      onSessionChange={reload}
      resource={resource}
      info={info}
      actions={<DiscoverWizard resource={resource} />}
    >
      <SectionLinks
        label="Service sections"
        sections={sections.map(({ page, label }) => ({ to: { pathname: page, search }, label }))}
      />
      {SECTION_HELP[page] && <p className="text-sm text-muted-foreground">{SECTION_HELP[page]}</p>}
      {notice && (
        <Alert>
          <AlertDescription role="status">{notice}</AlertDescription>
        </Alert>
      )}
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <Outlet
        context={
          {
            service,
            ...data,
            version,
            changed,
            conflict,
            ask,
            setNotice,
          } satisfies ServicePageContext
        }
      />
      <ConfirmRemove removal={removal} onClose={() => setRemoval(null)} />
    </ResourceChats>
  );
}
