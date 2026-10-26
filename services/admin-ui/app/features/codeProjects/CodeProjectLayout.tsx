import { useEffect, useState } from "react";
import { Link, Outlet, useLocation, useOutletContext, useParams } from "react-router";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { ApiError } from "~/lib/api";
import { getCodeProject, pullCodeProject, type CodeProjectDetail } from "./api";
import { graphStatus, syncing } from "./status";
import { getSessionOptions } from "~/features/sessions/api";
import DiscoverWizard from "~/features/sessions/DiscoverWizard";
import ResourceChats from "~/features/sessions/ResourceChats";
import SectionLinks from "~/components/SectionLinks";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";

/** What the project's sub-pages get from the layout: the project (loaded once, reloaded while a job runs), `version`
 * (counts reloads), a reload, `act` (runs an action, shows its notice and reloads) and whether one is running. */
export interface CodeProjectPageContext {
  project: CodeProjectDetail;
  version: number;
  reload: () => void;
  act: (run: () => Promise<unknown>, done: string) => Promise<void>;
  setNotice: (notice: string) => void;
  busy: boolean;
}
export const useCodeProjectPage = () => useOutletContext<CodeProjectPageContext>();

const SECTIONS = [
  { page: "overview", label: "Overview" },
  { page: "dependencies", label: "Dependencies" },
  { page: "documents", label: "Documents" },
  { page: "settings", label: "Settings" },
];
const when = (value: string | null) => (value ? new Date(value).toLocaleString() : "—");
const message = (reason: unknown, fallback: string) =>
  reason instanceof ApiError || reason instanceof Error ? reason.message : fallback;

/** One code project (`/code-projects/:codeProjectId`): a header (All code projects, name, Git source, ref, current
 * commit and when it was synced, sync and code graph status; Chat, Discover… when the library has discovery prompts for
 * code projects, Sync now), the sub-pages Overview, Dependencies, Documents and Settings as links (they keep the query, so an open
 * chat stays open) and the open sub-page. Chat opens the resource chat beside it. Reloads every 2 seconds while a sync
 * or indexing runs. */
export default function CodeProjectLayout() {
  const { codeProjectId } = useParams();
  return <CodeProjectPage key={codeProjectId} codeProjectId={codeProjectId!} />;
}

function CodeProjectPage({ codeProjectId }: { codeProjectId: string }) {
  const { search } = useLocation();
  const [project, setProject] = useState<CodeProjectDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [version, setVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const [discover, setDiscover] = useState(false); // the library has discovery prompts for code projects
  const reload = () => setVersion((value) => value + 1);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const value = await getCodeProject(codeProjectId, controller.signal);
        if (controller.signal.aborted) return;
        setProject(value);
        setError(null);
        if (syncing(value)) timer = setTimeout(load, 2000);
      } catch (reason) {
        if (!controller.signal.aborted) setError(message(reason, "Could not load the code project."));
      }
    }
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [codeProjectId, version]);
  useEffect(() => {
    const controller = new AbortController();
    getSessionOptions({ codeProjectId }, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setDiscover((value.discovery_prompts ?? []).length > 0);
      })
      .catch(() => undefined); // no Discover…
    return () => controller.abort();
  }, [codeProjectId]);

  async function act(run: () => Promise<unknown>, done: string) {
    setBusy(true);
    setNotice("");
    setError(null);
    try {
      await run();
      setNotice(done);
      reload();
    } catch (reason) {
      setError(message(reason, "Could not reach administration."));
    } finally {
      setBusy(false);
    }
  }

  const back = (
    <Button asChild variant="link" className="self-start px-0">
      <Link to="/code-projects">
        <ArrowLeft />
        All code projects
      </Link>
    </Button>
  );
  if (!project)
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
  const resource = { type: "code_project" as const, id: codeProjectId, name: project.name };
  const info = (
    <div data-testid="code-project-header" className="flex flex-col gap-1">
      {back}
      <h2 className="break-words text-xl font-semibold">{project.name}</h2>
      {project.remote_url && (
        <a href={project.remote_url} target="_blank" rel="noreferrer" className="break-all text-sm underline">
          {project.remote_url}
        </a>
      )}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
        {project.requested_ref && (
          <span>
            Ref <span className="font-mono">{project.requested_ref}</span>
          </span>
        )}
        {project.current_commit && (
          <span>
            Commit <span className="font-mono">{project.current_commit.slice(0, 12)}</span>
          </span>
        )}
        {project.synced_at && <span>Synced {when(project.synced_at)}</span>}
      </div>
      <div className="flex flex-wrap gap-1">
        {project.remote_url ? (
          <Badge variant={project.sync_status === "failed" ? "destructive" : "secondary"}>
            {project.sync_status ? `Sync ${project.sync_status}` : "Not cloned"}
          </Badge>
        ) : (
          <Badge variant="outline">Local folders</Badge>
        )}
        <Badge variant={project.processing?.status === "failed" ? "destructive" : "secondary"}>
          {`Code graph ${graphStatus(project)}`}
        </Badge>
      </div>
    </div>
  );
  return (
    <ResourceChats
      onSessionChange={reload}
      resource={resource}
      info={info}
      actions={
        <>
          {discover && <DiscoverWizard resource={resource} />}
          {project.remote_url && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={busy || syncing(project)}
              onClick={() => void act(() => pullCodeProject(codeProjectId), "Git pull queued.")}
            >
              <RefreshCw data-icon="inline-start" />
              Sync now
            </Button>
          )}
        </>
      }
    >
      <SectionLinks
        label="Code project sections"
        sections={SECTIONS.map(({ page, label }) => ({ to: { pathname: page, search }, label }))}
      />
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
      <Outlet context={{ project, version, reload, act, setNotice, busy } satisfies CodeProjectPageContext} />
    </ResourceChats>
  );
}
