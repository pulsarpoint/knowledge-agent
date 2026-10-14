import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import AdminLayout from "../components/AdminLayout";
import { getServerPage } from "~/features/servers/discovery";
import { getService } from "~/features/servers/services";
import { getEnvironment } from "~/features/servers/environments";
import { resourceChatPath, type ResourceType } from "~/features/sessions/api";
import ProposalsPage from "~/features/servers/ProposalsPage";

/** A server's, service's or environment's proposals: every proposal made in its runs (features/servers/ProposalsPage). */
export default function Page() {
  const { serverId, serviceId, environmentId } = useParams();
  const resource: { type: ResourceType; id: string } = serverId
    ? { type: "server", id: serverId }
    : serviceId
      ? { type: "service", id: serviceId }
      : { type: "environment", id: environmentId! };
  const [name, setName] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    const signal = controller.signal;
    const load =
      resource.type === "server"
        ? getServerPage(resource.id, signal).then((value) => value.server.name)
        : resource.type === "service"
          ? getService(resource.id, signal).then((value) => value.name)
          : getEnvironment(resource.id, signal).then((value) => value.name);
    load.then((value) => !signal.aborted && setName(value)).catch(() => undefined);
    return () => controller.abort();
  }, [resource.type, resource.id]);
  return (
    <AdminLayout>
      <div className="flex flex-col gap-1">
        <Link className="self-start text-sm underline" to={resourceChatPath(resource)}>
          {name ?? "Back"}
        </Link>
        <h1 className="text-2xl font-semibold">Proposals</h1>
      </div>
      <ProposalsPage key={`${resource.type}:${resource.id}`} resource={resource} />
    </AdminLayout>
  );
}
