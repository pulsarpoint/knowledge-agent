import { useEffect, useState, type FormEvent } from "react";
import { X } from "lucide-react";
import { saveCodeProject, type CodeProject, type CodeProjectInput } from "./api";
import CodeFolderTree from "./CodeFolderTree";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Spinner } from "~/components/ui/spinner";
import { Switch } from "~/components/ui/switch";
import { Textarea } from "~/components/ui/textarea";

const errorMessage = (reason: unknown) =>
  reason instanceof Error ? reason.message : "Could not reach administration.";

/** The code project form: New project on the list (in a sheet; Cancel) and a project's Settings (edited in place: Save
 * and Discard, enabled once something changed; onDirtyChange reports it): its name, description, source (existing
 * folders or a Git repository: URL, clone folder, ref, authentication, automatic pulls) and mapped folders. Save calls
 * onSaved with the notice to show; with `saveBlocked`, Save waits and the reason is shown. */
export default function CodeProjectForm({
  project,
  onSaved,
  onCancel,
  onDirtyChange,
  saveBlocked = null,
}: {
  project: CodeProject | null;
  onSaved: (notice: string) => void;
  onCancel: () => void;
  onDirtyChange?: (dirty: boolean) => void;
  /** Why Save must wait (shown beside it), e.g. a job is running. */
  saveBlocked?: string | null;
}) {
  const editing = project;
  const [git, setGit] = useState(project?.remote_url != null);
  const [cloneFolderEdited, setCloneFolderEdited] = useState(false);
  const [token, setToken] = useState("");
  const [username, setUsername] = useState("");
  const [privateRepo, setPrivateRepo] = useState(project?.has_token ?? false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [initial] = useState<CodeProjectInput>(() => ({
    name: project?.name ?? "",
    description: project?.description ?? "",
    remote_url: project?.remote_url ?? null,
    requested_ref: project?.requested_ref ?? null,
    clone_path: project?.clone_path ?? project?.checkout_path ?? null,
    folder_paths: project?.folder_paths ?? [],
    sync_interval_minutes: project?.sync_interval_minutes ?? null,
    clone_now: false,
    ...(project ? { revision: project.revision } : {}),
  }));
  const [draft, setDraft] = useState(initial);
  const dirty =
    git !== (project?.remote_url != null) ||
    privateRepo !== (project?.has_token ?? false) ||
    token !== "" ||
    username !== "" ||
    JSON.stringify(draft) !== JSON.stringify(initial);
  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);

  async function save(event: FormEvent) {
    event.preventDefault();
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
      setBusy(false);
      onSaved(value.clone_now ? "Code project saved. Clone queued." : "Code project saved.");
    } catch (reason) {
      setError(errorMessage(reason));
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save} className="flex flex-col gap-6">
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
                Defaults to the project name, directly inside the code root. You can choose a different unused folder
                name.
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
                Use saved authentication for cloning and pulls. This does not change the repository’s visibility on your
                Git provider.
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
                        editing?.has_token ? "Leave blank to keep the saved username" : "Username for repository access"
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
                        editing?.has_token ? "Leave blank to keep the saved token" : "Token with repository read access"
                      }
                    />
                    <FieldDescription>
                      Username and token are encrypted in the database and never returned to this form or included in
                      Git URLs.
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
                onCheckedChange={(checked) => setDraft({ ...draft, sync_interval_minutes: checked ? 60 : null })}
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
                  The background repository worker queues each pull when it is due. It continues while this page is
                  closed; a busy worker may delay a pull.
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
            Expand the tree and select one or more folders. Selecting a folder includes its contents and subfolders.
            {git && " The clone folder is included automatically."}
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
                  onClick={() => setDraft({ ...draft, folder_paths: draft.folder_paths.filter((p) => p !== folder) })}
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
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {saveBlocked && <p className="text-sm text-muted-foreground">{saveBlocked}</p>}
      <div className="flex flex-wrap gap-3">
        <Button
          disabled={
            busy || saveBlocked !== null || (!git && draft.folder_paths.length === 0) || (editing !== null && !dirty)
          }
        >
          {busy && <Spinner />}
          {editing ? "Save" : git && draft.clone_now ? "Save and clone" : "Save project"}
        </Button>
        <Button type="button" variant="outline" disabled={busy || (editing !== null && !dirty)} onClick={onCancel}>
          {editing ? "Discard" : "Cancel"}
        </Button>
      </div>
    </form>
  );
}
