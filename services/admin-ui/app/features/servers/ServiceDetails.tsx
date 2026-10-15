import { FileJson, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { type ServiceAsset } from "./services";
import { getStorage } from "~/lib/storage";
import { serviceTypes, serviceRoles } from "./serviceClassification";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "~/components/ui/card";
import { Separator } from "~/components/ui/separator";

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

/** A service's saved details; onClose adds a close button (the services list), the service page has none. */
export default function ServiceDetails({ service, onClose }: { service: ServiceAsset; onClose?: () => void }) {
  const codeFolder = useCodeFolder(service.repositories.some((repo) => repo.checkout_path));
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <CardTitle>{service.name}</CardTitle>
          {onClose && (
            <Button variant="ghost" size="icon" aria-label="Close service details" onClick={onClose}>
              <X />
            </Button>
          )}
        </div>
        <CardDescription>
          Saved application context · Updated {new Date(service.updated_at).toLocaleString()}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-6">
        <Link className="text-sm underline" to={`/documents?service_id=${service.id}`}>
          Service documentation
        </Link>
        <div className="flex flex-wrap gap-2">
          <Badge variant="secondary">{serviceTypes[service.service_type]}</Badge>
          <Badge variant="outline">{serviceRoles[service.service_role].label}</Badge>
        </div>
        {service.description && <p className="whitespace-pre-wrap text-sm">{service.description}</p>}
        <p className="text-sm text-muted-foreground">Owner: {service.owner || "Not specified"}</p>
        <Separator />
        <div className="grid gap-6 md:grid-cols-2">
          {[
            { title: "Depends on", links: service.dependencies },
            { title: "Used by", links: service.dependents },
          ].map(({ title, links }) => (
            <section key={title} className="flex flex-col gap-3" aria-label={title}>
              <h3 className="font-semibold">{title}</h3>
              {links.length === 0 ? (
                <p className="text-sm text-muted-foreground">No services selected.</p>
              ) : (
                <ul className="flex flex-col gap-3">
                  {links.map((link) => (
                    <li key={link.service_id} className="flex flex-col gap-1 text-sm">
                      <span className="font-medium">{link.name}</span>
                      {link.description && (
                        <p className="whitespace-pre-wrap break-words text-muted-foreground">{link.description}</p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          ))}
        </div>
        <Separator />
        <section className="flex flex-col gap-3">
          <h3 className="font-semibold">Code projects</h3>
          {service.repositories.length === 0 && (
            <p className="text-sm text-muted-foreground">No code projects linked yet.</p>
          )}
          {service.repositories.map((repo) => (
            <div key={repo.id} className="flex flex-col gap-1 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <Link className="font-medium underline underline-offset-4" to="/code-projects">
                  {repo.name}
                </Link>
                {repo.archived && <Badge variant="outline">Archived</Badge>}
              </div>
              {repo.remote_url ? (
                <p className="text-muted-foreground">
                  Downloaded commit:{" "}
                  <span className="font-mono">{repo.current_commit?.slice(0, 12) || "Not pulled"}</span> · Ref:{" "}
                  {repo.requested_ref || "Default branch"}
                </p>
              ) : (
                <p className="text-muted-foreground">Local folders</p>
              )}
              {repo.checkout_path && (
                <p className="break-all font-mono text-xs text-muted-foreground">
                  {codeFolder ? `${codeFolder}/${repo.checkout_path}` : `${repo.checkout_path} (in the code folder)`}
                </p>
              )}
            </div>
          ))}
        </section>
        <Separator />
        <section className="flex flex-col gap-3">
          <h3 className="font-semibold">Environments</h3>
          <div className="flex flex-wrap gap-2">
            {service.environments.map((environment) => (
              <Link key={environment.id} to={`/environments?view=graph&environment_id=${environment.id}`}>
                <Badge variant="secondary">{environment.name}</Badge>
              </Link>
            ))}
          </div>
          {service.environments.length === 0 && (
            <p className="text-sm text-muted-foreground">No environments assigned.</p>
          )}
        </section>
        <Separator />
        <section className="flex flex-col gap-4">
          <h3 className="font-semibold">Deployments</h3>
          {service.deployments.length === 0 && (
            <p className="text-sm text-muted-foreground">Deployment details have not been recorded.</p>
          )}
          {service.deployments.map((deployment) => (
            <div key={deployment.id} className="flex flex-col gap-2 rounded-lg border p-4 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="secondary">{deployment.environment}</Badge>
                <span className="font-medium">{deployment.server.name}</span>
                <span className="font-mono text-muted-foreground">{deployment.server.hostname}</span>
              </div>
              {deployment.server.description && (
                <p className="whitespace-pre-wrap break-words text-muted-foreground">{deployment.server.description}</p>
              )}
              {deployment.server.tags.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {deployment.server.tags.map((tag) => (
                    <Badge key={tag} variant="outline" className="max-w-full whitespace-normal break-all">
                      {tag}
                    </Badge>
                  ))}
                </div>
              )}
              <p>
                Runtime: {deployment.runtime || "Not specified"}
                {deployment.runtime_name && (
                  <>
                    {" "}
                    · <span className="font-mono">{deployment.runtime_name}</span>
                  </>
                )}
                {deployment.port !== null && <> · Port {deployment.port}</>}
              </p>
              <p>
                Reported deployed version:{" "}
                <span className="font-mono">{deployment.deployed_version || "Not recorded"}</span>
              </p>
              <p className="break-words">
                Logs: <span className="font-mono">{deployment.log_location || "Not recorded"}</span>
              </p>
              <p className="text-xs text-muted-foreground">
                SSH allowed: {deployment.server.ssh_allowed ? "On" : "Off"} · Connection:{" "}
                {deployment.server.access_enabled ? "Enabled" : "Not configured / disabled"} · Sudo:{" "}
                {deployment.server.allow_sudo ? "Allowed by server policy" : "Disabled"}
              </p>
            </div>
          ))}
        </section>
      </CardContent>
      <CardFooter className="flex flex-wrap justify-between gap-3">
        <p className="max-w-lg text-xs text-muted-foreground">
          Operator-maintained inventory. Live health and agent monitoring are not connected yet.
        </p>
        <Button asChild variant="outline">
          <a href={`/api/services/${service.id}/context`} target="_blank" rel="noreferrer">
            <FileJson data-icon="inline-start" />
            Agent context (JSON)
          </a>
        </Button>
      </CardFooter>
    </Card>
  );
}
