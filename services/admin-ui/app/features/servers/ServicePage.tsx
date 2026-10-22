import { useEffect, useState } from "react";
import { Link } from "react-router";
import { FileJson } from "lucide-react";
import { ApiError } from "~/lib/api";
import { getService, type ServiceAsset } from "./services";
import ProposeWizard from "~/features/sessions/ProposeWizard";
import ResourceChats from "~/features/sessions/ResourceChats";
import ServicePanel from "./ServicePanel";
import { checkoutText, sshText, useCodeFolder } from "./ServiceDetails";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";

/** One service: its Info (the service panel, as in the environment workspace, then its agent context, code folders and
 * server access) and its Chats (ResourceChats). */
export default function ServicePage({ serviceId }: { serviceId: string }) {
  const [service, setService] = useState<ServiceAsset | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    getService(serviceId, controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        setService(value);
        setError(null);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e instanceof ApiError ? e.message : "Could not load the service.");
      });
    return () => controller.abort();
  }, [serviceId, version]);

  const back = (
    <Link className="self-start text-sm underline" to="/services">
      Services
    </Link>
  );
  if (!service)
    return error ? (
      <div className="flex flex-col gap-3">
        {back}
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      </div>
    ) : (
      <Skeleton className="h-60" />
    );
  return (
    <ResourceChats
      resource={{ type: "service", id: serviceId, name: service.name }}
      info={
        <>
          <div className="flex flex-col gap-1">
            {back}
            <h1 className="break-words text-2xl font-semibold">{service.name}</h1>
          </div>
          <ServicePanel serviceId={serviceId} onChanged={() => setVersion((value) => value + 1)} />
          <AccessAndCode service={service} />
        </>
      }
      actions={
        <>
          <ProposeWizard resource={{ type: "service", id: serviceId, name: service.name }} />
          <Button asChild size="sm" variant="link">
            <Link to={`/services/${encodeURIComponent(serviceId)}/proposals`}>Proposals</Link>
          </Button>
        </>
      }
    />
  );
}

/** What the service page shows beside the panel: the agent context link, each code project's checkout in the code
 * folder and each deployment's server access. */
function AccessAndCode({ service }: { service: ServiceAsset }) {
  const checkouts = service.repositories.filter((repo) => repo.checkout_path);
  const codeFolder = useCodeFolder(checkouts.length > 0);
  return (
    <section aria-label="Access and code" className="flex flex-col gap-2 rounded-lg border p-3 text-sm">
      <h3 className="font-semibold">Access and code</h3>
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
