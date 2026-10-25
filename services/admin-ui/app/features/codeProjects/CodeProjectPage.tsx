import { useEffect, useState } from "react";
import { Link } from "react-router";
import { Pencil, RefreshCw } from "lucide-react";
import { ApiError } from "~/lib/api";
import { getCodeProject, processCodeProject, pullCodeProject, type CodeProjectDetail } from "./api";
import CodeProjectForm from "./CodeProjectForm";
import { getSessionOptions } from "~/features/sessions/api";
import DiscoverWizard from "~/features/sessions/DiscoverWizard";
import ResourceChats from "~/features/sessions/ResourceChats";
import EntryDocuments from "~/features/servers/EntryDocuments";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Skeleton } from "~/components/ui/skeleton";

const SYNCING = ["queued", "running", "waiting_input"];
const syncing = (project: CodeProjectDetail) =>
  SYNCING.includes(project.sync_status ?? "") || ["queued", "running"].includes(project.processing?.status ?? "");
/** The code graph's state in words: its running or failed job, else its index status, else not indexed. */
const graphStatus = (project: CodeProjectDetail) =>
  project.processing && ["queued", "running", "failed"].includes(project.processing.status)
    ? project.processing.status
    : (project.code_index?.status ?? "not indexed");
const when = (value: string | null) => (value ? new Date(value).toLocaleString() : "—");
const message = (reason: unknown, fallback: string) =>
  reason instanceof ApiError || reason instanceof Error ? reason.message : fallback;

/** One code project: a header (name, Git source, ref, current commit and when it was synced, sync and code graph
 * status; Chat, Discover… when the library has prompts for code projects, Sync now and Edit — the list's form, in a
 * sheet) and tabs Overview (details, checkout folders, code graph with Re-index, recent syncs and Built into: the
 * services built from it) and Documents (EntryDocuments). Reloads every 2 seconds while a sync or indexing runs. */
export default function CodeProjectPage({ codeProjectId }: { codeProjectId: string }) {
  const [project, setProject] = useState<CodeProjectDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [version, setVersion] = useState(0);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [discover, setDiscover] = useState(false); // the library has prompts for code projects

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
        if (!controller.signal.aborted) setDiscover(value.prompts.length > 0);
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
      setVersion((value) => value + 1);
    } catch (reason) {
      setError(message(reason, "Could not reach administration."));
    } finally {
      setBusy(false);
    }
  }

  const back = (
    <Link className="self-start text-sm underline" to="/code-projects">
      Code projects
    </Link>
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
  const running = syncing(project);
  const info = (
    <div data-testid="code-project-header" className="flex flex-col gap-1">
      {back}
      <h1 className="break-words text-2xl font-semibold">{project.name}</h1>
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
  const overview = (
    <div className="flex min-w-0 flex-col gap-6">
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
      <section aria-label="Repository" className="flex flex-col gap-2 text-sm">
        <h2 className="text-lg font-semibold">Repository</h2>
        {project.description && <p className="text-muted-foreground">{project.description}</p>}
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
          <dt className="text-muted-foreground">Source</dt>
          <dd className="break-all">{project.remote_url ?? "Existing folders"}</dd>
          {project.remote_url && (
            <>
              <dt className="text-muted-foreground">Ref</dt>
              <dd>{project.requested_ref ?? "Default branch"}</dd>
              <dt className="text-muted-foreground">Commit</dt>
              <dd className="break-all font-mono">{project.current_commit ?? "Not cloned yet"}</dd>
              <dt className="text-muted-foreground">Last sync</dt>
              <dd>{when(project.synced_at)}</dd>
              <dt className="text-muted-foreground">Pulls</dt>
              <dd>
                {project.sync_interval_minutes ? `Every ${project.sync_interval_minutes} minutes` : "Manual pulls"}
                {project.next_sync_at && ` · next ${when(project.next_sync_at)}`}
              </dd>
              <dt className="text-muted-foreground">Authentication</dt>
              <dd>{project.has_token ? "Authentication saved" : "Public access"}</dd>
            </>
          )}
        </dl>
        {project.sync_error && <p className="text-destructive">{project.sync_error}</p>}
      </section>
      <section aria-label="Checkout folders" className="flex flex-col gap-2 text-sm">
        <h2 className="text-lg font-semibold">Checkout folders</h2>
        {project.folder_paths.length === 0 ? (
          <p className="text-muted-foreground">Not cloned yet.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {project.folder_paths.map((folder) => (
              <li key={folder} className="break-all font-mono text-xs">{`code/${folder}`}</li>
            ))}
          </ul>
        )}
      </section>
      <section aria-label="Code graph" className="flex flex-col gap-2 text-sm">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-semibold">Code graph</h2>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={busy || running}
            onClick={() =>
              void act(
                () => processCodeProject(codeProjectId),
                "Code processing queued. Unchanged files reuse their index.",
              )
            }
          >
            <RefreshCw data-icon="inline-start" />
            {project.code_index ? "Re-index" : "Generate graph"}
          </Button>
        </div>
        <p>{`Status: ${graphStatus(project)}`}</p>
        {project.code_index && (
          <>
            <p>
              {`${project.code_index.stats.fileCount} indexed files · ${project.code_index.stats.nodeCount} symbols · ` +
                `${project.code_index.stats.edgeCount} relationships`}
            </p>
            <p className="text-muted-foreground">
              {Object.keys(project.code_index.stats.filesByLanguage).join(", ")}
              {` · Last processed ${when(project.code_index.processed_at)}`}
            </p>
          </>
        )}
        {project.processing?.error && <p className="text-destructive">{project.processing.error}</p>}
      </section>
      <section aria-label="Recent syncs" className="flex flex-col gap-2 text-sm">
        <h2 className="text-lg font-semibold">Recent syncs</h2>
        {project.sync_jobs.length === 0 ? (
          <p className="text-muted-foreground">No syncs yet.</p>
        ) : (
          <ul className="flex flex-col divide-y rounded-lg border">
            {project.sync_jobs.map((job) => (
              <li key={job.id} className="flex flex-col gap-0.5 px-3 py-2">
                <span className="flex flex-wrap items-center gap-2">
                  <Badge variant={job.status === "failed" ? "destructive" : "secondary"}>{job.status}</Badge>
                  <span className="text-muted-foreground">
                    {when(job.finished_at ?? job.started_at ?? job.created_at)}
                  </span>
                </span>
                {job.error && <span className="text-destructive">{job.error}</span>}
              </li>
            ))}
          </ul>
        )}
      </section>
      <section aria-label="Built into" className="flex flex-col gap-2 text-sm">
        <h2 className="text-lg font-semibold">Built into</h2>
        {project.built_into.length === 0 ? (
          <p className="text-muted-foreground">No service is recorded as built from this code project.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {project.built_into.map((service) => (
              <li key={service.id}>
                <Link className="underline" to={`/services/${encodeURIComponent(service.id)}`}>
                  {service.name}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
  return (
    <>
      <ResourceChats
        onSessionChange={() => setVersion((value) => value + 1)}
        resource={resource}
        info={info}
        pages={[
          { value: "overview", label: "Overview", content: overview },
          {
            value: "documents",
            label: "Documents",
            content: (
              <EntryDocuments
                entry={{ kind: "code_project", id: codeProjectId, name: project.name }}
                refresh={version}
                onChanged={() => setVersion((value) => value + 1)}
              />
            ),
          },
        ]}
        actions={
          <>
            {discover && <DiscoverWizard resource={resource} />}
            {project.remote_url && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={busy || running}
                onClick={() => void act(() => pullCodeProject(codeProjectId), "Git pull queued.")}
              >
                <RefreshCw data-icon="inline-start" />
                Sync now
              </Button>
            )}
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={running}
              onClick={() => {
                setNotice("");
                setEditing(true);
              }}
            >
              <Pencil data-icon="inline-start" />
              Edit
            </Button>
          </>
        }
      />
      <Sheet open={editing} onOpenChange={setEditing}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
          <SheetHeader>
            <SheetTitle>Edit code project</SheetTitle>
            <SheetDescription>
              Map folders inside the code root, or clone a Git repository into a named folder.
            </SheetDescription>
          </SheetHeader>
          <div className="px-4 pb-4">
            {editing && (
              <CodeProjectForm
                project={project}
                onCancel={() => setEditing(false)}
                onSaved={(saved) => {
                  setEditing(false);
                  setNotice(saved);
                  setVersion((value) => value + 1);
                }}
              />
            )}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
