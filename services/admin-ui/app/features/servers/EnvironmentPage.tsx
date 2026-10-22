import { useEffect, useState } from "react";
import { Link } from "react-router";
import { ApiError } from "~/lib/api";
import { getEnvironment, type EnvironmentAsset } from "./environments";
import ProposeWizard from "~/features/sessions/ProposeWizard";
import ResourceChats from "~/features/sessions/ResourceChats";
import EntryDocuments from "./EntryDocuments";
import EnvironmentWorkspace from "./EnvironmentWorkspace";
import { environmentMapPath } from "./inventoryMap";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Skeleton } from "~/components/ui/skeleton";

/** One environment: a header (name, description, System map, Proposals; Propose and Chat on the right) and tabs:
 * Workspace (its servers and services), Documents (the environment's own) and Chats (ResourceChats). */
export default function EnvironmentPage({ environmentId }: { environmentId: string }) {
  const [environment, setEnvironment] = useState<EnvironmentAsset | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    getEnvironment(environmentId, controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        setEnvironment(value);
        setError(null);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e instanceof ApiError ? e.message : "Could not load the environment.");
      });
    return () => controller.abort();
  }, [environmentId, version]);

  const back = (
    <Link className="self-start text-sm underline" to="/environments">
      Environments
    </Link>
  );
  if (!environment)
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
  const id = encodeURIComponent(environmentId);
  const entry = { kind: "environment" as const, id: environmentId, name: environment.name };
  return (
    <ResourceChats
      wide
      resource={{ type: "environment", id: environmentId, name: environment.name }}
      info={
        <div className="flex flex-col gap-1">
          {back}
          <h1 className="break-words text-2xl font-semibold">{environment.name}</h1>
          <p className="whitespace-pre-wrap text-sm text-muted-foreground">
            {environment.description || "No description"}
          </p>
          <div className="flex flex-wrap gap-4 text-sm">
            <Link className="underline" to={environmentMapPath(environmentId)}>
              System map
            </Link>
            <Link className="underline" to={`/environments/${id}/proposals`}>
              Proposals
            </Link>
          </div>
        </div>
      }
      pages={[
        { value: "workspace", label: "Workspace", content: <EnvironmentWorkspace environmentId={environmentId} /> },
        {
          value: "documents",
          label: "Documents",
          content: <EntryDocuments entry={entry} ownOnly onChanged={() => setVersion((value) => value + 1)} />,
        },
      ]}
      actions={<ProposeWizard resource={{ type: "environment", id: environmentId, name: environment.name }} />}
    />
  );
}
