import { useEffect, useState } from "react";
import BackLink from "~/components/BackLink";
import { Link, Outlet, useLocation, useOutletContext, useParams, useSearchParams } from "react-router";
import OldLinkRedirect from "~/components/OldLinkRedirect";
import SectionLinks from "~/components/SectionLinks";
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

/** What the environment's sub-pages get from the layout: the environment, `version` (counts reloads after a change),
 * `chatTurns` (counts turns of the page chat, whose agent may have written drafts) and a reload. */
interface EnvironmentPageContext {
  environment: EnvironmentAsset;
  version: number;
  chatTurns: number;
  reload: () => void;
}
const useEnvironmentPage = () => useOutletContext<EnvironmentPageContext>();

const SECTIONS = [
  {
    page: "workspace",
    label: "Workspace",
    help: "The servers in this environment and the services deployed on them. Drafts wait here to be confirmed.",
  },
  {
    page: "documents",
    label: "Documents",
    help: "Document drafts waiting to be published, then the environment's own documents.",
  },
];

/** `/environments/:environmentId`: opens Workspace; an old link (`?view=workspace|documents`) opens its sub-page,
 * keeping the rest of the query (an open chat, a workspace filter). */
export const EnvironmentIndex = () => <OldLinkRedirect pages={{ documents: "documents" }} fallback="workspace" />;

/** One environment (`/environments/:environmentId`): a header (name, description, System map, "N drafts to review"
 * linking to the workspace's Drafts filter, or to Documents when only document drafts wait; Discover and Chat on the
 * right), the sub-pages Workspace and Documents as links (they keep the query, so an open chat stays open) and the open
 * sub-page (ResourceChats). */
export default function EnvironmentLayout() {
  const { environmentId } = useParams();
  return <EnvironmentPage key={environmentId} environmentId={environmentId!} />;
}

function EnvironmentPage({ environmentId }: { environmentId: string }) {
  const { search } = useLocation();
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

  const back = <BackLink to="/environments">All environments</BackLink>;
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
  // The workspace's Drafts filter, keeping the open chat.
  const draftsLink = new URLSearchParams(params);
  draftsLink.set("drafts", "only");
  // Only document drafts waiting: Documents, keeping the open chat.
  const chat = params.get("chat");
  const documentsLink = `/environments/${id}/documents${chat ? `?${new URLSearchParams({ chat })}` : ""}`;
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
            <h2 className="break-words text-xl font-semibold">{environment.name}</h2>
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
                to={drafts > 0 ? `/environments/${id}/workspace?${draftsLink}` : documentsLink}
              >
                {label}
              </Link>
            )}
          </div>
        </div>
      }
      actions={<DiscoverWizard resource={{ type: "environment", id: environmentId, name: environment.name }} />}
    >
      <SectionLinks
        label="Environment sections"
        sections={SECTIONS.map(({ page, ...section }) => ({ ...section, to: { pathname: page, search } }))}
      />
      <Outlet
        context={
          {
            environment,
            version,
            chatTurns,
            reload: () => setVersion((value) => value + 1),
          } satisfies EnvironmentPageContext
        }
      />
    </ResourceChats>
  );
}

/** The environment's servers and services. */
export function EnvironmentWorkspacePage() {
  const { environment, chatTurns, reload } = useEnvironmentPage();
  return <EnvironmentWorkspace environmentId={environment.id} refresh={chatTurns} onChanged={reload} />;
}

/** Drafts to review (every document draft the header counts), then the environment's own documents. */
export function EnvironmentDocuments() {
  const { environment, version, chatTurns, reload } = useEnvironmentPage();
  return (
    <div className="flex flex-col gap-6">
      <EnvironmentDocumentDrafts environmentId={environment.id} refresh={version + chatTurns} />
      <EntryDocuments
        entry={{ kind: "environment", id: environment.id, name: environment.name }}
        ownOnly
        refresh={chatTurns}
        onChanged={reload}
      />
    </div>
  );
}
