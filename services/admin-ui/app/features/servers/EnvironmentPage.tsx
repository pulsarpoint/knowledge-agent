import { useEffect, useState } from "react";
import { Link } from "react-router";
import { ApiError } from "~/lib/api";
import { getEnvironment, getEnvironmentModel, type EnvironmentAsset } from "./environments";
import { DraftBadge } from "./ConfirmDraftSheet";
import ProposeWizard from "~/features/sessions/ProposeWizard";
import ResourceChats from "~/features/sessions/ResourceChats";
import EntryDocuments from "./EntryDocuments";
import EnvironmentWorkspace from "./EnvironmentWorkspace";
import { environmentMapPath } from "./inventoryMap";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Skeleton } from "~/components/ui/skeleton";

/** One environment: a header (name, description, System map, Proposals, "N drafts to review" linking to the workspace's
 * Drafts filter; Propose and Chat on the right) and tabs:
 * Workspace (its servers and services), Documents (the environment's own) and Chats (ResourceChats). */
export default function EnvironmentPage({ environmentId }: { environmentId: string }) {
  const [environment, setEnvironment] = useState<EnvironmentAsset | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [drafts, setDrafts] = useState(0);

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
    // The drafts count comes from the environment model; without it the header just leaves the line out.
    getEnvironmentModel(environmentId, controller.signal).then(
      (model) => !controller.signal.aborted && setDrafts(model.drafts),
      () => {},
    );
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
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="break-words text-2xl font-semibold">{environment.name}</h1>
            {environment.status === "draft" && <DraftBadge />}
          </div>
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
            {drafts > 0 && (
              <Link className="font-medium underline" to={`/environments/${id}?drafts=only`}>
                {drafts === 1 ? "1 draft to review" : `${drafts} drafts to review`}
              </Link>
            )}
          </div>
        </div>
      }
      pages={[
        {
          value: "workspace",
          label: "Workspace",
          content: (
            <EnvironmentWorkspace environmentId={environmentId} onChanged={() => setVersion((value) => value + 1)} />
          ),
        },
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
