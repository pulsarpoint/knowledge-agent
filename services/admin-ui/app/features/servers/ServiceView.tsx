import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router";
import { FileJson, Plus } from "lucide-react";
import AddDeploymentSheet from "./AddDeploymentSheet";
import type { Deployment } from "./inventory";
import type { ServiceAsset, ServiceDeployment } from "./services";
import { builtFrom, RELATION_TABS, tabRelations, useServicePage, type RelationTab } from "./ServiceLayout";
import { connectionRows, DeploymentSheet, deploymentText, Description, servicePath } from "./ServicePanel";
import { draftOf, List, Row } from "./rows";
import { DraftActions, DraftBadge } from "./ConfirmDraftSheet";
import { RowActions } from "./RowAction";
import { getStorage } from "~/lib/storage";
import { Button } from "~/components/ui/button";

/** The configured code folder as mounted in the containers, or null while unknown. */
function useCodeFolder(needed: boolean) {
  const [folder, setFolder] = useState<string | null>(null);
  useEffect(() => {
    if (!needed) return;
    const controller = new AbortController();
    getStorage(controller.signal)
      .then(({ locations }) => setFolder(locations?.find((location) => location.id === "code")?.container_path ?? null))
      .catch(() => undefined);
    return () => controller.abort();
  }, [needed]);
  return folder;
}

/** Where a code project's checkout is: "/code/shop", or "shop (in the code folder)" while the folder is unknown. */
const checkoutText = (checkoutPath: string, codeFolder: string | null) =>
  codeFolder ? `${codeFolder}/${checkoutPath}` : `${checkoutPath} (in the code folder)`;

/** A deployment's server access: "Agent SSH: Enabled · Connection: Enabled · Sudo: Disabled". */
const sshText = ({ server }: ServiceDeployment) =>
  `Agent SSH: ${server.ssh_allowed ? "Enabled" : "Disabled"} · Connection: ${
    server.access_enabled ? "Enabled" : "Not configured / disabled"
  } · Sudo: ${server.allow_sudo ? "Allowed by server policy" : "Disabled"}`;

/** The service read-only: description, owner and address; where it runs (its environments and deployments, each with
 * Edit and Remove); how many items each tab holds, linking to it; and the agent context, code folders and server
 * access. A draft service shows its badge with Confirm / Discard. */
export default function ServiceView() {
  const { service, services, relations, deployments, connections, changed, conflict, ask } = useServicePage();
  const { search } = useLocation();
  const [editing, setEditing] = useState<Deployment | null>(null);
  const [adding, setAdding] = useState(false);
  const places = new Map(service.deployments.map((item) => [item.id, `${item.environment} / ${item.server.name}`]));
  const tab = (page: string) => ({ pathname: servicePath(service.id, page), search });
  const counts = [
    { page: "code", label: "Code projects", count: builtFrom(service.id, relations).length },
    ...(Object.keys(RELATION_TABS) as RelationTab[]).map((page) => ({
      page,
      label: RELATION_TABS[page].label,
      count: tabRelations(service.id, relations, page).length,
    })),
    {
      page: "connections",
      label: "Connections",
      count: connectionRows(services, connections, new Set(deployments.map((item) => item.id)), service.id).length,
    },
  ];

  return (
    <div className="flex min-w-0 flex-col gap-6">
      {service.status === "draft" && (
        <div className="flex items-center gap-2 text-sm">
          <DraftBadge />
          <span className="text-muted-foreground">An agent recorded this service.</span>
          <RowActions>
            <DraftActions
              name={service.name}
              confirm={{ kind: "service", id: service.id, revision: service.revision }}
              onDone={changed}
            />
          </RowActions>
        </div>
      )}
      <section aria-label="Details" className="flex flex-col gap-2 text-sm">
        {service.description ? (
          <Description text={service.description} />
        ) : (
          <p className="text-muted-foreground">No description yet.</p>
        )}
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
          <dt className="text-muted-foreground">Owner</dt>
          <dd>{service.owner || "Not specified"}</dd>
          {service.address && (
            <>
              <dt className="text-muted-foreground">Address</dt>
              <dd className="break-all font-mono">{service.address}</dd>
            </>
          )}
        </dl>
      </section>
      <section aria-label="Where it runs" className="flex flex-col gap-2 text-sm">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-lg font-semibold">Where it runs</h3>
          <Button type="button" size="sm" variant="outline" onClick={() => setAdding(true)}>
            <Plus data-icon="inline-start" />
            Add deployment
          </Button>
        </div>
        {service.environments.length > 0 && (
          <p className="flex flex-wrap gap-x-3 gap-y-1">
            {service.environments.map((environment) => (
              <Link
                key={environment.id}
                className="underline"
                to={`/environments/${encodeURIComponent(environment.id)}`}
              >
                {environment.name}
              </Link>
            ))}
          </p>
        )}
        {deployments.length === 0 ? (
          <p className="text-muted-foreground">No deployment recorded.</p>
        ) : (
          <List>
            {deployments.map((item) => {
              const place = places.get(item.id) ?? "an unknown place";
              return (
                <Row
                  key={item.id}
                  text={`${place} · ${deploymentText(item)}`}
                  name={place}
                  draft={draftOf("deployment", item)}
                  onDone={changed}
                  onEdit={() => setEditing(item)}
                  onRemove={() =>
                    ask("deployments", item, `the deployment on ${place}`, "Its connections are removed with it.")
                  }
                />
              );
            })}
          </List>
        )}
      </section>
      <section aria-label="Related" className="flex flex-col gap-2 text-sm">
        <h3 className="text-lg font-semibold">Related</h3>
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(10rem,1fr))] gap-2">
          {counts.map(({ page, label, count }) => (
            <li key={page}>
              <Link className="flex flex-col rounded-lg border px-3 py-2 hover:bg-muted" to={tab(page)}>
                <span className="text-muted-foreground">{label}</span>{" "}
                <span className="text-lg font-semibold">{count}</span>
              </Link>
            </li>
          ))}
        </ul>
      </section>
      <AccessAndCode service={service} />
      {adding && (
        <AddDeploymentSheet
          serviceId={service.id}
          serviceName={service.name}
          onSaved={() => {
            setAdding(false);
            changed();
          }}
          onClose={() => setAdding(false)}
        />
      )}
      {editing && (
        <DeploymentSheet
          deployment={editing}
          onSaved={() => {
            setEditing(null);
            changed();
          }}
          onConflict={() => {
            setEditing(null);
            conflict();
          }}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}

/** The agent context link, each code project's checkout in the code folder and each deployment's server access. */
function AccessAndCode({ service }: { service: ServiceAsset }) {
  const checkouts = service.repositories.filter((repo) => repo.checkout_path);
  const codeFolder = useCodeFolder(checkouts.length > 0);
  return (
    <section aria-label="Access and code" className="flex flex-col gap-2 text-sm">
      <h3 className="text-lg font-semibold">Access and code</h3>
      <Button asChild size="sm" variant="outline" className="self-start">
        <a href={`/api/services/${encodeURIComponent(service.id)}/context`} target="_blank" rel="noreferrer">
          <FileJson data-icon="inline-start" />
          Agent context (JSON)
        </a>
      </Button>
      {checkouts.length > 0 && (
        <ul className="flex flex-col gap-1">
          {checkouts.map((repo) => (
            <li key={repo.id} className="break-all font-mono text-xs text-muted-foreground">
              {`${repo.name}: ${checkoutText(repo.checkout_path!, codeFolder)}`}
            </li>
          ))}
        </ul>
      )}
      {service.deployments.length > 0 && (
        <ul className="flex flex-col gap-1">
          {service.deployments.map((deployment) => (
            <li key={deployment.id} className="text-xs text-muted-foreground">
              {`${deployment.environment} / ${deployment.server.name}: ${sshText(deployment)}`}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
