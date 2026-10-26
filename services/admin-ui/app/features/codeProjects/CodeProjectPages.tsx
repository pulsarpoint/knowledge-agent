import { useState } from "react";
import { Link, Navigate, useLocation, useSearchParams } from "react-router";
import { RefreshCw } from "lucide-react";
import { processCodeProject } from "./api";
import CodeProjectForm from "./CodeProjectForm";
import { useCodeProjectPage } from "./CodeProjectLayout";
import { graphStatus, syncing } from "./status";
import EntryDocuments from "~/features/servers/EntryDocuments";
import LeaveGuard from "~/components/LeaveGuard";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";

const when = (value: string | null) => (value ? new Date(value).toLocaleString() : "—");

/** `/code-projects/:codeProjectId`: opens Overview; an old link (`?view=overview|documents`) opens its sub-page, keeping
 * the rest of the query (an open chat). */
export function CodeProjectIndex() {
  const [current] = useSearchParams();
  const { hash } = useLocation();
  const params = new URLSearchParams(current);
  const page = params.get("view") === "documents" ? "documents" : "overview";
  params.delete("view");
  const query = params.toString();
  return <Navigate replace to={`${page}${query ? `?${query}` : ""}${hash}`} />;
}

/** The project's details, checkout folders, code graph (Re-index), recent syncs and Built into (the services built
 * from it). */
export function CodeProjectOverviewPage() {
  const { project, act, busy } = useCodeProjectPage();
  const codeProjectId = project.id;
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <section aria-label="Repository" className="flex flex-col gap-2 text-sm">
        <h3 className="text-lg font-semibold">Repository</h3>
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
        <h3 className="text-lg font-semibold">Checkout folders</h3>
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
          <h3 className="text-lg font-semibold">Code graph</h3>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={busy || syncing(project)}
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
        <h3 className="text-lg font-semibold">Recent syncs</h3>
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
        <h3 className="text-lg font-semibold">Built into</h3>
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
}

/** The project's documents. */
export function CodeProjectDocumentsPage() {
  const { project, version, reload } = useCodeProjectPage();
  return (
    <EntryDocuments
      entry={{ kind: "code_project", id: project.id, name: project.name }}
      refresh={version}
      onChanged={reload}
    />
  );
}

/** The project's settings (name, description, Git source: clone folder, ref, authentication, automatic pulls; mapped
 * folders), edited in place: Save, or Discard to drop the changes. Leaving with unsaved changes asks first. */
export function CodeProjectSettingsPage() {
  const { project, reload, setNotice } = useCodeProjectPage();
  const [dirty, setDirty] = useState(false);
  const [started, setStarted] = useState(0);
  return (
    <>
      {/* Discard starts the form afresh; a save does when the reloaded project comes with its new revision. (A change
          made by the chat's agent bumps the revision too, and starts it afresh, dropping unsaved edits.) */}
      <CodeProjectForm
        key={`${project.revision}:${started}`}
        project={project}
        onDirtyChange={setDirty}
        onCancel={() => setStarted((n) => n + 1)}
        saveBlocked={
          syncing(project) ? "A sync or code graph job is running; settings can be saved when it finishes." : null
        }
        onSaved={(saved) => {
          setNotice(saved);
          reload();
        }}
      />
      <LeaveGuard dirty={dirty} />
    </>
  );
}
