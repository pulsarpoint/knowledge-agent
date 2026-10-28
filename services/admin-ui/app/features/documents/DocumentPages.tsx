import { useState, type ReactNode } from "react";
import { Navigate, useLocation, useSearchParams } from "react-router";
import { getDocumentVersion, type DocumentVersion } from "./api";
import { useDocumentPage } from "./DocumentEditorView";
import DocumentAgentRuns from "./DocumentAgentRuns";
import DocumentConnections from "./DocumentConnections";
import DocumentDrafts from "./DocumentDrafts";
import Markdown from "~/components/Markdown";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";

/** A live document's sub-page: a brand-new draft has none of them (versions, drafts and connections come with
 * publishing), so it opens Edit. */
function LiveOnly({ children }: { children: ReactNode }) {
  const { document } = useDocumentPage();
  const { search } = useLocation();
  return document.is_draft ? <Navigate replace to={{ pathname: "..", search }} /> : children;
}

/** `/documents/:id/history`: the published versions; the open one is shown with Use version as draft. */
export function DocumentHistoryPage() {
  return (
    <LiveOnly>
      <VersionHistory />
    </LiveOnly>
  );
}

function VersionHistory() {
  const { document, loadVersion } = useDocumentPage();
  const [historical, setHistorical] = useState<DocumentVersion | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  async function open(number: number) {
    setLoading(true);
    setError("");
    try {
      setHistorical(await getDocumentVersion(document.id, number));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load version");
    } finally {
      setLoading(false);
    }
  }
  return (
    <div className="flex flex-col gap-4">
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <div className="grid gap-6 md:grid-cols-[240px_minmax(0,1fr)]">
        <div className="flex flex-col gap-2">
          {document.versions.map((version) => (
            <Button
              key={version.version}
              type="button"
              disabled={loading}
              variant={historical?.version === version.version ? "secondary" : "outline"}
              className="h-auto justify-start whitespace-normal py-3 text-left"
              onClick={() => void open(version.version)}
            >
              <span>
                Version {version.version}
                {version.live ? " · Current" : ""}
                {version.from_draft ? " · from draft" : ""}
                <span className="block text-xs font-normal text-muted-foreground">
                  {new Date(version.created_at).toLocaleString()}
                </span>
              </span>
            </Button>
          ))}
        </div>
        <div className="min-w-0 rounded-lg border p-4">
          {historical ? (
            <>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="font-semibold">
                  {historical.title} · v{historical.version}
                </h2>
                <Button type="button" variant="outline" onClick={() => loadVersion(historical)}>
                  Use version as draft
                </Button>
              </div>
              <p className="mt-2 text-sm text-muted-foreground">
                {historical.purpose} · {historical.audience} · {historical.origin}
              </p>
              <p className="mt-2 text-sm text-muted-foreground">
                Resources at save time:{" "}
                {Object.values(historical.source_snapshot)
                  .flat()
                  .map((r) => r.name)
                  .join(", ") || "None"}
              </p>
              {historical.source_snapshot.code_projects
                .filter((r) => r.git_commit)
                .map((r) => (
                  <p key={r.id} className="mt-2 text-sm">
                    {r.name}: <code className="break-all">{r.git_commit}</code>
                  </p>
                ))}
              <Markdown content={historical.markdown} />
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              Select a version to read it or use it as the starting point for an edit.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

/** `/documents/:id/drafts`: the document's drafts; `?draft=<id>` opens one. */
export function DocumentDraftsPage() {
  const page = useDocumentPage();
  const [params] = useSearchParams();
  return (
    <LiveOnly>
      <DocumentDrafts
        documentId={page.document.id}
        selected={params.get("draft")}
        onPromoted={page.onPromoted}
        confirm={page.confirm}
        onCount={page.setDraftCount}
        onDiscarded={page.draftDiscarded}
        onEdit={page.editDraft}
      />
    </LiveOnly>
  );
}

/** `/documents/:id/connections`: the documents it depends on, the ones that depend on it, and what it was built from. */
export function DocumentConnectionsPage() {
  const { document } = useDocumentPage();
  return (
    <LiveOnly>
      <DocumentConnections documentId={document.id} />
    </LiveOnly>
  );
}

/** `/documents/:id/agent-runs`: the Ask agent runs on the document. */
export function DocumentAgentRunsPage() {
  const { document } = useDocumentPage();
  return <DocumentAgentRuns documentId={document.id} />;
}
