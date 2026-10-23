import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { ApiError } from "~/lib/api";
import { draftsLabel, getEnvironment, getEnvironmentModel, type EnvironmentAsset } from "./environments";
import { DraftBadge } from "./ConfirmDraftSheet";
import EnvironmentDocumentDrafts from "./EnvironmentDocumentDrafts";
import DiscoverWizard from "~/features/sessions/DiscoverWizard";
import ResourceChats from "~/features/sessions/ResourceChats";
import EntryDocuments from "./EntryDocuments";
import EnvironmentWorkspace from "./EnvironmentWorkspace";
import { environmentMapPath } from "./inventoryMap";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Skeleton } from "~/components/ui/skeleton";

/** One environment: a header (name, description, System map, "N drafts to review" linking to the workspace's
 * Drafts filter, or to the Documents tab when only document drafts wait; Discover and Chat on the right) and tabs:
 * Workspace (its servers and services), Documents (Drafts to review: every document draft the header counts; then the
 * environment's own documents) and Chats (ResourceChats). */
export default function EnvironmentPage({ environmentId }: { environmentId: string }) {
  const [environment, setEnvironment] = useState<EnvironmentAsset | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [drafts, setDrafts] = useState(0);
  const [documentDrafts, setDocumentDrafts] = useState(0);
  // Bumped after each turn of the page chat: its agent may have written drafts.
  const [chatTurns, setChatTurns] = useState(0);
  const [params] = useSearchParams();

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
      (model) => {
        if (controller.signal.aborted) return;
        setDrafts(model.drafts);
        setDocumentDrafts(model.document_drafts);
      },
      () => {},
    );
    return () => controller.abort();
  }, [environmentId, version, chatTurns]);

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
  // The workspace's Drafts filter, keeping the open chat.
  const draftsLink = new URLSearchParams(params);
  draftsLink.delete("view");
  draftsLink.set("drafts", "only");
  // Only document drafts waiting: the Documents tab.
  const documentsLink = new URLSearchParams(params);
  documentsLink.set("view", "documents");
  const label = draftsLabel(drafts, documentDrafts);
  return (
    <ResourceChats
      wide
      onSessionChange={() => setChatTurns((value) => value + 1)}
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
            {label && (
              <Link
                className="font-medium underline"
                to={`/environments/${id}?${drafts > 0 ? draftsLink : documentsLink}`}
              >
                {label}
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
            <EnvironmentWorkspace
              environmentId={environmentId}
              refresh={chatTurns}
              onChanged={() => setVersion((value) => value + 1)}
            />
          ),
        },
        {
          value: "documents",
          label: "Documents",
          content: (
            <div className="flex flex-col gap-6">
              <EnvironmentDocumentDrafts environmentId={environmentId} refresh={version + chatTurns} />
              <EntryDocuments
                entry={entry}
                ownOnly
                refresh={chatTurns}
                onChanged={() => setVersion((value) => value + 1)}
              />
            </div>
          ),
        },
      ]}
      actions={<DiscoverWizard resource={{ type: "environment", id: environmentId, name: environment.name }} />}
    />
  );
}
