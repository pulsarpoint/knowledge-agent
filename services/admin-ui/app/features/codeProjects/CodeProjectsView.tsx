import CodeGraphExplorer from "./CodeGraphExplorer";
import { useEffect, useState, type FormEvent } from "react";
import { FileText, Folder, MoreHorizontal, Plus, RefreshCw, X } from "lucide-react";
import {
  getCodeDirectory,
  getCodeFile,
  getCodeProjects,
  pullCodeProject,
  processCodeProject,
  removeCodeProject,
  saveCodeProject,
  type CodeDirectory,
  type CodeProject,
  type CodeProjectInput,
} from "./api";
import { Alert, AlertDescription } from "~/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "~/components/ui/alert-dialog";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "~/components/ui/card";
import { Checkbox } from "~/components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "~/components/ui/dropdown-menu";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "~/components/ui/empty";
import CodeFolderTree from "./CodeFolderTree";
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Spinner } from "~/components/ui/spinner";
import { Switch } from "~/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";
import { Textarea } from "~/components/ui/textarea";

const syncing = (project: CodeProject) =>
  ["queued", "running", "waiting_input"].includes(project.sync_status ?? "") ||
  ["queued", "running"].includes(project.processing?.status ?? "");
/** Folders under code/ that removing the project with its files would delete. */
const projectFolders = (project: CodeProject) =>
  [...new Set([...project.folder_paths, ...(project.clone_path ? [project.clone_path] : [])])]
    .sort()
    .map((folder) => `code/${folder}`);
const errorMessage = (reason: unknown) =>
  reason instanceof Error ? reason.message : "Could not reach administration.";

export default function CodeProjectsView() {
  const [graphProject, setGraphProject] = useState<string | null>(null);
  const [projects, setProjects] = useState<CodeProject[]>([]);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [draft, setDraft] = useState<CodeProjectInput | null>(null);
  const [editing, setEditing] = useState<CodeProject | null>(null);
  const [git, setGit] = useState(false);
  const [cloneFolderEdited, setCloneFolderEdited] = useState(false);
  const [token, setToken] = useState("");
  const [username, setUsername] = useState("");
  const [privateRepo, setPrivateRepo] = useState(false);
  const [busy, setBusy] = useState(false);
  const [path, setPath] = useState("");
  const [offset, setOffset] = useState(0);
  const [directory, setDirectory] = useState<CodeDirectory | null>(null);
  const [directoryError, setDirectoryError] = useState("");
  const [filePath, setFilePath] = useState<string | null>(null);
  const [file, setFile] = useState<{ path: string; text: string } | null>(null);
  const [fileError, setFileError] = useState("");

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
  }, [refresh]);
  const checkouts = projects.map((p) => p.synced_at).join(",");
  useEffect(() => {
    const controller = new AbortController();
    setDirectory(null);
    setDirectoryError("");
    getCodeDirectory(path, offset, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setDirectory(value);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setDirectoryError(errorMessage(reason));
      });
    return () => controller.abort();
  }, [path, offset, refresh, checkouts]);
  useEffect(() => {
    const controller = new AbortController();
    setFile(null);
    setFileError("");
    if (filePath !== null)
      getCodeFile(filePath, controller.signal)
        .then((value) => {
          if (!controller.signal.aborted) setFile(value);
        })
        .catch((reason) => {
          if (!controller.signal.aborted) setFileError(errorMessage(reason));
        });
    return () => controller.abort();
  }, [filePath, refresh]);

  function open(project: CodeProject | null) {
    setEditing(project);
    setGit(project?.remote_url != null);
    setCloneFolderEdited(false);
    setToken("");
    setUsername("");
    setPrivateRepo(project?.has_token ?? false);
    setError("");
    setNotice("");
    setDraft({
      name: project?.name ?? "",
      description: project?.description ?? "",
      remote_url: project?.remote_url ?? null,
      requested_ref: project?.requested_ref ?? null,
      clone_path: project?.clone_path ?? project?.checkout_path ?? null,
      folder_paths: project?.folder_paths ?? [],
      sync_interval_minutes: project?.sync_interval_minutes ?? null,
      clone_now: false,
      ...(project ? { revision: project.revision } : {}),
    });
  }
  function browse(folder: string) {
    setPath(folder);
    setOffset(0);
    setFilePath(null);
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!draft) return;
    setBusy(true);
    setError("");
    const value = {
      ...draft,
      remote_url: git ? draft.remote_url : null,
      clone_path: git ? draft.clone_path : null,
      requested_ref: git ? draft.requested_ref : null,
      sync_interval_minutes: git ? draft.sync_interval_minutes : null,
      clone_now: git && draft.clone_now,
    };
    if (git && privateRepo) {
      if (username) value.username = username;
      if (token) value.token = token;
    } else if (git && editing?.has_token) value.token = null;
    try {
      await saveCodeProject(value, editing?.id);
      setNotice(value.clone_now ? "Code project saved. Clone queued." : "Code project saved.");
      setDraft(null);
      setToken("");
      setUsername("");
      setRefresh((n) => n + 1);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }
  const [removing, setRemoving] = useState<CodeProject | null>(null);
  const [deleteFiles, setDeleteFiles] = useState(false);

  async function remove(project: CodeProject, withFiles: boolean) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await removeCodeProject(project.id, withFiles);
      if (graphProject === project.id) setGraphProject(null);
      setNotice(
        !withFiles
          ? `${project.name} removed. Files on disk were kept.`
          : result.deleted.length
            ? `${project.name} removed. Deleted ${result.deleted.join(", ")}.`
            : `${project.name} removed. No files were found to delete.`,
      );
      if (result.errors.length) setError(result.errors.join(" "));
      setRefresh((n) => n + 1);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  async function generateGraph(project: CodeProject) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await processCodeProject(project.id);
      setNotice("Code processing queued. Unchanged files reuse their index.");
      setRefresh((r) => r + 1);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  async function pull(project: CodeProject) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await pullCodeProject(project.id);
      setNotice("Git pull queued.");
      setRefresh((n) => n + 1);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">{projects.length} code projects</p>
        <div className="flex gap-2">
          <Button variant="outline" disabled={busy} onClick={() => setRefresh((n) => n + 1)}>
            <RefreshCw data-icon="inline-start" />
            Refresh
          </Button>
          <Button disabled={busy} onClick={() => open(null)}>
            <Plus data-icon="inline-start" />
            Add project
          </Button>
        </div>
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {notice && (
        <Alert>
          <AlertDescription role="status">{notice}</AlertDescription>
        </Alert>
      )}
      {draft && (
        <Card>
          <CardHeader>
            <CardTitle>{editing ? "Edit code project" : "Add code project"}</CardTitle>
            <CardDescription>
              Map folders inside the code root, or clone a Git repository into a named folder.
            </CardDescription>
          </CardHeader>
          <form onSubmit={save}>
            <CardContent className="pb-6">
              <FieldGroup>
                <Field>
                  <FieldLabel htmlFor="code-project-name">Project name</FieldLabel>
                  <Input
                    id="code-project-name"
                    required
                    maxLength={200}
                    disabled={busy}
                    value={draft.name}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        name: e.target.value,
                        ...(!editing && !cloneFolderEdited
                          ? { clone_path: e.target.value.trim().replace(/[\/\\]/g, "-") || null }
                          : {}),
                      })
                    }
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor="code-project-description">Description</FieldLabel>
                  <Textarea
                    id="code-project-description"
                    maxLength={4000}
                    disabled={busy}
                    value={draft.description}
                    onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor="code-project-source">Source</FieldLabel>
                  <Select
                    value={git ? "git" : "local"}
                    disabled={busy || editing !== null}
                    onValueChange={(value) => {
                      setGit(value === "git");
                      setToken("");
                      setUsername("");
                      setPrivateRepo(false);
                      setDraft({ ...draft, clone_now: value === "git" });
                    }}
                  >
                    <SelectTrigger id="code-project-source">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectGroup>
                        <SelectItem value="local">Existing folders</SelectItem>
                        <SelectItem value="git">Git repository</SelectItem>
                      </SelectGroup>
                    </SelectContent>
                  </Select>
                </Field>
                {git && (
                  <>
                    <Field>
                      <FieldLabel htmlFor="code-project-url">Git repository URL</FieldLabel>
                      <Input
                        id="code-project-url"
                        required
                        maxLength={2000}
                        disabled={busy || editing !== null}
                        value={draft.remote_url ?? ""}
                        placeholder="https://github.com/organization/repository"
                        onChange={(e) => setDraft({ ...draft, remote_url: e.target.value })}
                      />
                      <FieldDescription>Enter the repository URL without a username or token.</FieldDescription>
                    </Field>
                    <Field>
                      <FieldLabel htmlFor="code-clone-folder">Clone folder</FieldLabel>
                      <Input
                        id="code-clone-folder"
                        required
                        maxLength={1000}
                        disabled={busy || editing?.current_commit != null}
                        value={draft.clone_path ?? ""}
                        placeholder="payments-api"
                        onChange={(e) => {
                          setCloneFolderEdited(true);
                          setDraft({ ...draft, clone_path: e.target.value });
                        }}
                      />
                      <FieldDescription>
                        Defaults to the project name, directly inside the code root. You can choose a different unused
                        folder name.
                      </FieldDescription>
                    </Field>
                    <Field>
                      <FieldLabel htmlFor="code-project-ref">Branch, tag, or commit (optional)</FieldLabel>
                      <Input
                        id="code-project-ref"
                        maxLength={200}
                        disabled={busy}
                        value={draft.requested_ref ?? ""}
                        placeholder="Default branch"
                        onChange={(e) => setDraft({ ...draft, requested_ref: e.target.value || null })}
                      />
                    </Field>
                    <FieldSet>
                      <Field orientation="horizontal">
                        <Switch
                          id="code-private-repo"
                          disabled={busy}
                          checked={privateRepo}
                          onCheckedChange={(checked) => {
                            setPrivateRepo(checked);
                            setToken("");
                            setUsername("");
                          }}
                        />
                        <FieldLabel htmlFor="code-private-repo">Private repository</FieldLabel>
                      </Field>
                      <FieldDescription>
                        Use saved authentication for cloning and pulls. This does not change the repository’s visibility
                        on your Git provider.
                      </FieldDescription>
                      {privateRepo ? (
                        <>
                          {editing?.has_token && (
                            <FieldDescription>
                              Authentication is saved. Leave either field blank to keep its saved value.
                            </FieldDescription>
                          )}
                          <Field>
                            <FieldLabel htmlFor="code-project-username">Git username</FieldLabel>
                            <Input
                              id="code-project-username"
                              type="password"
                              autoComplete="new-password"
                              maxLength={200}
                              required={!editing?.has_token}
                              disabled={busy}
                              value={username}
                              onChange={(e) => setUsername(e.target.value)}
                              placeholder={
                                editing?.has_token
                                  ? "Leave blank to keep the saved username"
                                  : "Username for repository access"
                              }
                            />
                          </Field>
                          <Field>
                            <FieldLabel htmlFor="code-project-token">Access token</FieldLabel>
                            <Input
                              id="code-project-token"
                              type="password"
                              autoComplete="new-password"
                              maxLength={1024}
                              required={!editing?.has_token}
                              disabled={busy}
                              value={token}
                              onChange={(e) => setToken(e.target.value)}
                              placeholder={
                                editing?.has_token
                                  ? "Leave blank to keep the saved token"
                                  : "Token with repository read access"
                              }
                            />
                            <FieldDescription>
                              Username and token are encrypted in the database and never returned to this form or
                              included in Git URLs.
                            </FieldDescription>
                          </Field>
                        </>
                      ) : (
                        editing?.has_token && (
                          <FieldDescription>
                            Saving will remove the saved username and token. Future pulls will use public access.
                          </FieldDescription>
                        )
                      )}
                    </FieldSet>
                    <Field orientation="horizontal">
                      <Switch
                        id="code-auto-pull"
                        disabled={busy}
                        checked={draft.sync_interval_minutes !== null}
                        onCheckedChange={(checked) =>
                          setDraft({ ...draft, sync_interval_minutes: checked ? 60 : null })
                        }
                      />
                      <FieldLabel htmlFor="code-auto-pull">Automatic pulls</FieldLabel>
                    </Field>
                    {draft.sync_interval_minutes !== null && (
                      <Field>
                        <FieldLabel htmlFor="code-pull-interval">Pull interval (minutes)</FieldLabel>
                        <Input
                          id="code-pull-interval"
                          type="number"
                          min={5}
                          max={10080}
                          required
                          disabled={busy}
                          value={Number.isNaN(draft.sync_interval_minutes) ? "" : draft.sync_interval_minutes}
                          onChange={(e) => setDraft({ ...draft, sync_interval_minutes: e.target.valueAsNumber })}
                        />
                        <FieldDescription>
                          The background repository worker queues each pull when it is due. It continues while this page
                          is closed; a busy worker may delay a pull.
                        </FieldDescription>
                      </Field>
                    )}
                    {!editing && (
                      <Field orientation="horizontal">
                        <Checkbox
                          id="code-clone-now"
                          disabled={busy}
                          checked={draft.clone_now}
                          onCheckedChange={(checked) => setDraft({ ...draft, clone_now: checked === true })}
                        />
                        <FieldLabel htmlFor="code-clone-now">Clone repository after saving</FieldLabel>
                      </Field>
                    )}
                  </>
                )}
                <FieldSet>
                  <FieldLegend variant="label">Mapped folders</FieldLegend>
                  <FieldDescription>
                    Expand the tree and select one or more folders. Selecting a folder includes its contents and
                    subfolders.{git && " The clone folder is included automatically."}
                  </FieldDescription>
                  <div className="max-h-80 overflow-auto rounded-md border p-3">
                    <CodeFolderTree
                      key={editing?.id ?? "new"}
                      selected={draft.folder_paths}
                      lockedPath={git ? draft.clone_path : null}
                      disabled={busy}
                      onChange={(folder_paths) => setDraft((value) => value && { ...value, folder_paths })}
                    />
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {draft.folder_paths.map((folder) => (
                      <Badge key={folder} variant="secondary" className="max-w-full whitespace-normal break-all">
                        {folder}
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          aria-label={`Unmap ${folder}`}
                          disabled={busy || (git && folder === draft.clone_path)}
                          onClick={() =>
                            setDraft({ ...draft, folder_paths: draft.folder_paths.filter((p) => p !== folder) })
                          }
                        >
                          <X />
                        </Button>
                      </Badge>
                    ))}
                    {draft.folder_paths.length === 0 && (
                      <p className="text-sm text-muted-foreground">No existing folders selected.</p>
                    )}
                  </div>
                </FieldSet>
              </FieldGroup>
            </CardContent>
            <CardFooter className="flex flex-wrap gap-3">
              <Button disabled={busy || (!git && draft.folder_paths.length === 0)}>
                {busy && <Spinner />}
                {git && draft.clone_now ? "Save and clone" : "Save project"}
              </Button>
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={() => {
                  setDraft(null);
                  setToken("");
                  setUsername("");
                }}
              >
                Cancel
              </Button>
            </CardFooter>
          </form>
        </Card>
      )}
      <Card id="code-browser" className="min-w-0">
        <CardHeader>
          <CardTitle>Code folder</CardTitle>
          <CardDescription>
            {directory ? (
              <>
                <span className="break-all">
                  {directory.host_path ?? "CODE_FOLDER"} → {directory.root}
                </span>
                <br />
                All project folders live inside this mount.
              </>
            ) : (
              "Browse the code folder mounted into Docker."
            )}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex min-w-0 flex-col gap-4">
          <nav aria-label="Code folder path" className="flex flex-wrap items-center gap-1">
            <Button variant="link" onClick={() => browse("")}>
              Code root
            </Button>
            {path
              .split("/")
              .filter(Boolean)
              .map((part, index) => (
                <span key={index} className="flex items-center gap-1">
                  /
                  <Button
                    variant="link"
                    onClick={() =>
                      browse(
                        path
                          .split("/")
                          .slice(0, index + 1)
                          .join("/"),
                      )
                    }
                  >
                    {part}
                  </Button>
                </span>
              ))}
          </nav>

          {directoryError ? (
            <Alert variant="destructive">
              <AlertDescription>{directoryError}</AlertDescription>
            </Alert>
          ) : !directory ? (
            <Spinner />
          ) : (
            <>
              {directory.entries.length === 0 ? (
                <Empty>
                  <EmptyHeader>
                    <EmptyTitle>This folder is empty</EmptyTitle>
                    <EmptyDescription>
                      Add files on the host or clone a Git project into the code root.
                    </EmptyDescription>
                  </EmptyHeader>
                </Empty>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Name</TableHead>
                      <TableHead>Type</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {directory.entries.map((entry) => (
                      <TableRow key={entry.path}>
                        <TableCell className="max-w-lg whitespace-normal break-all">
                          <Button
                            variant="link"
                            className="max-w-full whitespace-normal"
                            disabled={!["folder", "file"].includes(entry.kind)}
                            onClick={() => (entry.kind === "folder" ? browse(entry.path) : setFilePath(entry.path))}
                          >
                            {entry.kind === "folder" ? <Folder /> : <FileText />}
                            {entry.name}
                          </Button>
                        </TableCell>
                        <TableCell>{entry.kind === "symlink" ? "Symbolic link" : entry.kind}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="text-xs text-muted-foreground">
                  {directory.total} entries · Symbolic links are listed but not followed.
                </p>
                <div className="flex gap-2">
                  {offset > 0 && (
                    <Button variant="outline" onClick={() => setOffset(Math.max(0, offset - 200))}>
                      Previous
                    </Button>
                  )}
                  {directory.next_offset !== null && (
                    <Button variant="outline" onClick={() => setOffset(directory.next_offset!)}>
                      Next
                    </Button>
                  )}
                </div>
              </div>
            </>
          )}
        </CardContent>
      </Card>
      {filePath !== null && (
        <Card className="min-w-0">
          <CardHeader>
            <div className="flex items-center justify-between gap-3">
              <CardTitle className="break-all">{filePath}</CardTitle>
              <Button variant="ghost" size="icon" aria-label="Close file preview" onClick={() => setFilePath(null)}>
                <X />
              </Button>
            </div>
            <CardDescription>File preview</CardDescription>
          </CardHeader>
          <CardContent>
            {fileError ? (
              <Alert variant="destructive">
                <AlertDescription>{fileError}</AlertDescription>
              </Alert>
            ) : !file ? (
              <Spinner />
            ) : (
              <pre
                aria-label="Code file content"
                tabIndex={0}
                className="max-h-[32rem] overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted p-4 text-xs"
              >
                {file.text}
              </pre>
            )}
          </CardContent>
        </Card>
      )}
      <Card>
        <CardHeader>
          <CardTitle>Code projects</CardTitle>
          <CardDescription>
            Each project groups one or more folders. Generate graph builds a CodeGraph index for inspection; generate it
            again after changing source. Git pulls update the project's folders; connect them to a RAG collection in RAG
            → Collections to make the code searchable.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <Spinner />
          ) : projects.length === 0 ? (
            <Empty>
              <EmptyHeader>
                <EmptyTitle>No code projects yet</EmptyTitle>
                <EmptyDescription>Add a project to map existing folders or clone a Git repository.</EmptyDescription>
              </EmptyHeader>
            </Empty>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Project</TableHead>
                  <TableHead>Folders</TableHead>
                  <TableHead>Git sync</TableHead>
                  <TableHead>Code processing</TableHead>
                  <TableHead>Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {projects.map((project) => (
                  <TableRow key={project.id}>
                    <TableCell className="max-w-sm whitespace-normal">
                      <p className="font-medium">{project.name}</p>
                      <p className="text-sm text-muted-foreground">{project.description}</p>
                      {project.remote_url && (
                        <div className="flex flex-col items-start gap-2">
                          <a
                            href={project.remote_url}
                            target="_blank"
                            rel="noreferrer"
                            className="break-all text-xs underline"
                          >
                            {project.remote_url}
                          </a>
                          <Badge variant="outline">
                            {project.has_token ? "Authentication saved" : "Public access"}
                          </Badge>
                        </div>
                      )}
                    </TableCell>
                    <TableCell className="max-w-sm whitespace-normal">
                      {project.folder_paths.map((folder) => (
                        <Button
                          key={folder}
                          variant="link"
                          className="max-w-full whitespace-normal break-all"
                          onClick={() => browse(folder)}
                        >
                          {folder}
                        </Button>
                      ))}
                      {project.folder_paths.length === 0 && (
                        <span className="text-muted-foreground">Not cloned yet</span>
                      )}
                    </TableCell>
                    <TableCell className="max-w-xs whitespace-normal">
                      {project.remote_url ? (
                        <div className="flex flex-col items-start gap-2">
                          <Badge variant={project.sync_status === "failed" ? "destructive" : "secondary"}>
                            {syncing(project) && <Spinner />}
                            {project.sync_status ?? "Not cloned"}
                          </Badge>
                          <span className="text-xs">
                            {project.sync_interval_minutes
                              ? `Every ${project.sync_interval_minutes} minutes`
                              : "Manual pulls"}
                          </span>
                          {project.synced_at && (
                            <span className="text-xs text-muted-foreground">
                              Last success: {new Date(project.synced_at).toLocaleString()}
                            </span>
                          )}
                          {project.next_sync_at && (
                            <span className="text-xs text-muted-foreground">
                              Next pull: {new Date(project.next_sync_at).toLocaleString()}
                            </span>
                          )}
                          {project.sync_error && <span className="text-sm text-destructive">{project.sync_error}</span>}
                        </div>
                      ) : (
                        <Badge variant="outline">Local folders</Badge>
                      )}
                    </TableCell>
                    <TableCell className="max-w-xs whitespace-normal">
                      <div className="flex flex-col items-start gap-2">
                        <Badge variant={project.processing?.status === "failed" ? "destructive" : "secondary"}>
                          {project.processing && ["queued", "running", "failed"].includes(project.processing.status)
                            ? project.processing.status
                            : (project.code_index?.status ?? "Not processed")}
                        </Badge>
                        {project.code_index && (
                          <>
                            <span className="text-xs">
                              {project.code_index.stats.fileCount} indexed files · {project.code_index.stats.nodeCount}{" "}
                              symbols · {project.code_index.stats.edgeCount} relationships
                            </span>
                            <span className="text-xs">
                              {Object.keys(project.code_index.stats.filesByLanguage).join(", ")}
                            </span>
                            <span className="text-xs text-muted-foreground">
                              {project.code_index.unresolved_references} unresolved references ·{" "}
                              {project.code_index.parse_error_files.length} files with parse errors
                            </span>
                            <span className="text-xs text-muted-foreground">
                              Last processed: {new Date(project.code_index.processed_at).toLocaleString()}
                            </span>
                          </>
                        )}
                        {project.processing?.error && (
                          <span className="text-sm text-destructive">{project.processing.error}</span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <DropdownMenu modal={false}>
                        <DropdownMenuTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={`Actions for ${project.name}`}
                            disabled={busy}
                          >
                            <MoreHorizontal />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          {project.code_index && (
                            <DropdownMenuItem onSelect={() => setGraphProject(project.id)}>View graph</DropdownMenuItem>
                          )}
                          <DropdownMenuItem disabled={syncing(project)} onSelect={() => void generateGraph(project)}>
                            Generate graph
                          </DropdownMenuItem>
                          <DropdownMenuItem disabled={syncing(project)} onSelect={() => open(project)}>
                            Edit
                          </DropdownMenuItem>
                          {project.remote_url && (
                            <DropdownMenuItem disabled={syncing(project)} onSelect={() => void pull(project)}>
                              {project.current_commit ? "Pull" : "Clone"}
                            </DropdownMenuItem>
                          )}
                          <DropdownMenuSeparator />
                          <DropdownMenuItem
                            className="text-destructive focus:text-destructive"
                            disabled={syncing(project)}
                            onSelect={() => {
                              setDeleteFiles(false);
                              setRemoving(project);
                            }}
                          >
                            Remove
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      <AlertDialog
        open={removing !== null}
        onOpenChange={(open) => {
          if (!open) setRemoving(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove code project?</AlertDialogTitle>
            <AlertDialogDescription>
              Remove {removing?.name}? It disappears from Code projects, services and agents, and its folders can be
              used by another project. Folders connected to a RAG collection stay connected until you remove them in RAG
              → Collections.
            </AlertDialogDescription>
          </AlertDialogHeader>
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
                    This cannot be undone. Refused when another code project uses a folder or a RAG collection indexes
                    it.
                  </FieldDescription>
                </div>
              </Field>
            </div>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (removing) void remove(removing, deleteFiles);
                setRemoving(null);
              }}
            >
              Remove project
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      {graphProject && projects.find((p) => p.id === graphProject)?.code_index && (
        <Card>
          <CardHeader>
            <CardTitle>{projects.find((p) => p.id === graphProject)?.name} · Code graph</CardTitle>
            <Button type="button" variant="outline" className="self-start" onClick={() => setGraphProject(null)}>
              Close graph
            </Button>
          </CardHeader>
          <CardContent>
            <CodeGraphExplorer
              projectId={graphProject}
              sourceHash={projects.find((p) => p.id === graphProject)!.code_index!.source_hash}
            />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
