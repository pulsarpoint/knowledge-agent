import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Link } from "react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { FolderGit2, Plus, RefreshCw } from "lucide-react";
import { getCodeProjects, pullCodeProject, removeCodeProject, type CodeProject } from "./api";
import { useCodeProjectsPage } from "./CodeProjectsLayout";
import { graphStatus, syncStatus, syncing } from "./status";
import DataTable, { FILTER_WIDTH, sortHeader as header, TableEmpty, type Sort } from "~/components/DataTable";
import { Alert, AlertDescription } from "~/components/ui/alert";
import ConfirmRemove from "~/components/ConfirmRemove";
import RowMenu, { deleteMenuItem } from "~/components/RowMenu";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import { Field, FieldDescription, FieldLabel } from "~/components/ui/field";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Spinner } from "~/components/ui/spinner";

const ALL = "all";
export type SortKey = "name" | "synced" | "status";
export interface ListView {
  search: string;
  sync: string; // a sync status, or ALL
  graph: string; // a code graph status, or ALL
  sort: SortKey;
  descending: boolean;
}

/** The projects the table shows: those whose name or source has the search, with the chosen sync and code graph status,
 * sorted by name, last synced (never synced first) or sync status (then name). */
export function visibleProjects(projects: CodeProject[], view: ListView) {
  const search = view.search.trim().toLowerCase();
  const byName = (a: CodeProject, b: CodeProject) => a.name.localeCompare(b.name);
  const compare: Record<SortKey, (a: CodeProject, b: CodeProject) => number> = {
    name: byName,
    synced: (a, b) => (a.synced_at ?? "").localeCompare(b.synced_at ?? "") || byName(a, b),
    status: (a, b) => syncStatus(a).toLowerCase().localeCompare(syncStatus(b).toLowerCase()) || byName(a, b),
  };
  return projects
    .filter(
      (project) =>
        (!search || `${project.name}\n${project.remote_url ?? "local folder"}`.toLowerCase().includes(search)) &&
        (view.sync === ALL || syncStatus(project) === view.sync) &&
        (view.graph === ALL || graphStatus(project) === view.graph),
    )
    .sort((a, b) => (view.descending ? -1 : 1) * compare[view.sort](a, b));
}

/** Folders under code/ that removing the project with its files would delete. */
const projectFolders = (project: CodeProject) =>
  [...new Set([...project.folder_paths, ...(project.clone_path ? [project.clone_path] : [])])]
    .sort()
    .map((folder) => `code/${folder}`);
const errorMessage = (reason: unknown) =>
  reason instanceof Error ? reason.message : "Could not reach administration.";
const when = (value: string) => new Date(value).toLocaleString();

/** Code projects → Code projects: a table (name linking to the project's page, source, ref, commit and when it was synced,
 * sync and code graph status, how many services are built from it; Sync now and Delete) with search, status filters,
 * sorting by a header's click and 20 rows a page. Reloads while a sync or indexing runs, or automatic pulls are on. */
export default function CodeProjectsList() {
  const { reloaded, newProject } = useCodeProjectsPage();
  const [projects, setProjects] = useState<CodeProject[]>([]);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState<ListView>({ search: "", sync: ALL, graph: ALL, sort: "name", descending: false });
  const [removing, setRemoving] = useState<CodeProject | null>(null);
  const [deleteFiles, setDeleteFiles] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const value = await getCodeProjects(controller.signal);
        if (controller.signal.aborted) return;
        setProjects(value.projects);
        setLoading(false);
        if (value.projects.some((p) => syncing(p) || p.sync_interval_minutes !== null))
          timer = setTimeout(load, value.projects.some(syncing) ? 2000 : 10000);
      } catch (reason) {
        if (!controller.signal.aborted) {
          setError(errorMessage(reason));
          setLoading(false);
        }
      }
    }
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [refresh, reloaded]);

  const change = (next: Partial<ListView>) => setView((current) => ({ ...current, ...next }));

  async function act(run: () => Promise<string>) {
    setBusy(true);
    setError("");
    try {
      toast.success(await run());
      setRefresh((n) => n + 1);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }
  const pull = (project: CodeProject) =>
    act(async () => {
      await pullCodeProject(project.id);
      return "Git pull queued.";
    });
  const remove = (project: CodeProject, withFiles: boolean) =>
    act(async () => {
      const result = await removeCodeProject(project.id, withFiles);
      if (result.errors.length) setError(result.errors.join(" "));
      return !withFiles
        ? `${project.name} deleted. Files on disk were kept.`
        : result.deleted.length
          ? `${project.name} deleted. Deleted ${result.deleted.join(", ")}.`
          : `${project.name} deleted. No files were found to delete.`;
    });

  const sortHeader = (label: string, key: SortKey) =>
    header(label, key, { key: view.sort, descending: view.descending }, (next: Sort<SortKey>) =>
      change({ sort: next.key, descending: next.descending }),
    );
  const columns: ColumnDef<CodeProject, unknown>[] = [
    {
      id: "name",
      header: () => sortHeader("Name", "name"),
      cell: ({ row: { original: project } }) => (
        <Link className="font-medium underline" to={`/code-projects/${encodeURIComponent(project.id)}`}>
          {project.name}
        </Link>
      ),
    },
    {
      id: "source",
      header: "Source",
      cell: ({ row: { original: project } }) => (
        <span className="break-all text-sm">{project.remote_url ?? "local folder"}</span>
      ),
    },
    {
      id: "ref",
      header: "Ref",
      cell: ({ row: { original: project } }) =>
        project.requested_ref ? <span className="font-mono text-sm">{project.requested_ref}</span> : "—",
    },
    {
      id: "commit",
      header: () => sortHeader("Commit", "synced"),
      cell: ({ row: { original: project } }) => (
        <div className="flex flex-col gap-0.5">
          <span className="font-mono text-sm">{project.current_commit?.slice(0, 12) ?? "—"}</span>
          {project.synced_at && <span className="text-xs text-muted-foreground">{when(project.synced_at)}</span>}
        </div>
      ),
    },
    {
      id: "sync",
      header: () => sortHeader("Sync status", "status"),
      cell: ({ row: { original: project } }) => (
        <div className="flex flex-col items-start gap-1">
          <Badge
            variant={!project.remote_url ? "outline" : project.sync_status === "failed" ? "destructive" : "secondary"}
          >
            {syncing(project) && <Spinner />}
            {syncStatus(project)}
          </Badge>
          {project.sync_error && <span className="text-xs text-destructive">{project.sync_error}</span>}
        </div>
      ),
    },
    {
      id: "graph",
      header: "Code graph",
      cell: ({ row: { original: project } }) => (
        <Badge variant={project.processing?.status === "failed" ? "destructive" : "secondary"}>
          {graphStatus(project)}
        </Badge>
      ),
    },
    {
      id: "built",
      header: "Built into",
      cell: ({ row: { original: project } }) => project.built_into_count ?? 0,
    },
    {
      id: "actions",
      header: "Actions",
      cell: ({ row: { original: project } }) => (
        <div className="flex justify-end gap-1">
          {project.remote_url && (
            <Button
              variant="ghost"
              size="sm"
              aria-label={`Sync now for ${project.name}`}
              disabled={busy || syncing(project)}
              onClick={() => void pull(project)}
            >
              <RefreshCw data-icon="inline-start" />
              Sync now
            </Button>
          )}
          <RowMenu
            name={project.name}
            disabled={busy || syncing(project)}
            items={[
              deleteMenuItem(() => {
                setDeleteFiles(false);
                setRemoving(project);
              }),
            ]}
          />
        </div>
      ),
    },
  ];

  const shown = visibleProjects(projects, view);
  /** A filter's statuses: those in the list, and the chosen one even when no project has it any more. */
  const options = (status: (project: CodeProject) => string, chosen: string) =>
    [...new Set([...projects.map(status), ...(chosen === ALL ? [] : [chosen])])].sort();
  const toolbar = (
    <>
      <Select value={view.sync} onValueChange={(sync) => change({ sync })}>
        <SelectTrigger className={FILTER_WIDTH} aria-label="Sync status">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectItem value={ALL}>All sync statuses</SelectItem>
            {options(syncStatus, view.sync).map((status) => (
              <SelectItem key={status} value={status}>
                {status}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
      <Select value={view.graph} onValueChange={(graph) => change({ graph })}>
        <SelectTrigger className={FILTER_WIDTH} aria-label="Code graph">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectItem value={ALL}>All code graphs</SelectItem>
            {options(graphStatus, view.graph).map((status) => (
              <SelectItem key={status} value={status}>
                {status}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
    </>
  );

  return (
    <div className="flex min-w-0 flex-col gap-4">
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <DataTable
        columns={columns}
        data={shown}
        search={{ plural: "code projects", value: view.search, onChange: (search) => change({ search }) }}
        sort={{
          key: view.sort === "synced" ? "commit" : view.sort === "status" ? "sync" : "name",
          descending: view.descending,
        }}
        resetKey={`${view.sync}\n${view.graph}\n${view.sort}\n${view.descending}`}
        toolbar={toolbar}
        actions={
          <Button onClick={newProject}>
            <Plus data-icon="inline-start" />
            New code project
          </Button>
        }
        loading={loading}
        rowId={(project) => project.id}
        empty={
          projects.length === 0 ? (
            <TableEmpty
              icon={FolderGit2}
              title="No code projects yet"
              description="New code project maps existing folders or clones a Git repository."
            />
          ) : (
            "No code projects match."
          )
        }
      />
      <ConfirmRemove
        removal={
          removing && {
            title: `Delete “${removing.name}”?`,
            description:
              "It disappears from Code projects, services and agents, and its folders can be used by another project.",
            action: "Delete code project",
            run: () => void remove(removing, deleteFiles),
          }
        }
        onClose={() => setRemoving(null)}
      >
        {removing && (
          <div className="flex flex-col gap-3 text-sm">
            <ul className="list-disc pl-5 break-all">
              {projectFolders(removing).map((folder) => (
                <li key={folder}>{folder}</li>
              ))}
              {projectFolders(removing).length === 0 && <li className="text-muted-foreground">No folders</li>}
            </ul>
            <Field orientation="horizontal" className="items-start">
              <Checkbox
                id="remove-delete-files"
                checked={deleteFiles}
                onCheckedChange={(checked) => setDeleteFiles(checked === true)}
              />
              <div>
                <FieldLabel htmlFor="remove-delete-files">
                  Also delete these folders and the code graph from disk
                </FieldLabel>
                <FieldDescription className="mt-1">
                  This cannot be undone. Refused when another code project uses a folder.
                </FieldDescription>
              </div>
            </Field>
          </div>
        )}
      </ConfirmRemove>
    </div>
  );
}
