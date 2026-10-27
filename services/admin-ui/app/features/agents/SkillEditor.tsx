import { useEffect, useRef, useState, type FormEvent } from "react";
import { toast } from "sonner";
import BackLink from "~/components/BackLink";
import ConfirmRemove from "~/components/ConfirmRemove";
import LeaveGuard from "~/components/LeaveGuard";
import { useParams } from "react-router";
import { File as FileIcon, FilePlus, Folder, FolderPlus, History, Pencil, Trash2, Upload } from "lucide-react";
import { ApiError } from "~/lib/api";
import {
  changedLabel,
  checkSkillMd,
  fileChanges,
  getSkill,
  pathProblem,
  readSkillFile,
  restoreSkill,
  saveSkillFiles,
  sizeLabel,
  skillMdName,
  sourceLabel,
  treeRows,
  type Skill,
  type SkillVersion,
  type TreeRow,
  type WorkingFile,
} from "./skills";
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
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Field, FieldError, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Skeleton } from "~/components/ui/skeleton";
import { Spinner } from "~/components/ui/spinner";
import { Textarea } from "~/components/ui/textarea";
import { cn } from "~/lib/utils";

const message = (error: unknown, fallback: string) => (error instanceof ApiError ? error.message : fallback);
const when = (value: string | null) => (value ? new Date(value).toLocaleString() : "Unknown time");
const workingCopy = (skill: Skill): Record<string, WorkingFile> =>
  Object.fromEntries(skill.files.map((file) => [file.path, { origin: file.path, size: file.size }]));
const parentOf = (path: string) => path.split("/").slice(0, -1).join("/");
const under = (path: string, folder: string) => path === folder || path.startsWith(`${folder}/`);
const moved = (path: string, from: string, to: string) => (under(path, from) ? to + path.slice(from.length) : path);

function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).replace(/^data:[^,]*,/, ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

type Naming = { mode: "file" | "folder" | "rename"; value: string; error: string | null };
const NAMING_LABEL = { file: "New file path", folder: "New folder path", rename: "New path" };

/** The skill editor: a working copy of the skill's files, edited in a tree and a text editor; Save sends the changes,
 * which the server writes as a new folder. */
export default function SkillEditor() {
  const { skillId = "" } = useParams();
  const [skill, setSkill] = useState<Skill | null>(null);
  const [working, setWorking] = useState<Record<string, WorkingFile>>({});
  const [folders, setFolders] = useState<string[]>([]);
  const [selected, setSelected] = useState<TreeRow | null>(null);
  const [naming, setNaming] = useState<Naming | null>(null);
  const [deleting, setDeleting] = useState<TreeRow | null>(null);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [restoring, setRestoring] = useState<SkillVersion | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const leaving = useRef(false);
  const upload = useRef<HTMLInputElement>(null);

  function open(value: Skill, keep?: string) {
    const files = workingCopy(value);
    setSkill(value);
    setWorking(files);
    setFolders([]);
    const path = keep && keep in files ? keep : "SKILL.md" in files ? "SKILL.md" : null;
    setSelected(path ? { path, name: path.split("/").pop()!, depth: 0, kind: "file" } : null);
    setNaming(null);
  }
  useEffect(() => {
    let cancelled = false;
    getSkill(skillId)
      .then((value) => !cancelled && open(value))
      .catch((reason) => !cancelled && setError(message(reason, "Could not open this skill.")));
    return () => {
      cancelled = true;
    };
  }, [skillId]);

  const file = selected?.kind === "file" ? working[selected.path] : undefined;
  const needsText = !!file && file.origin !== null && file.text === undefined && !file.binary && !file.base64;
  useEffect(() => {
    if (!skill || !selected || !file || !needsText) return;
    const { path } = selected;
    const origin = file.origin!;
    let cancelled = false;
    readSkillFile(skill.id, origin)
      .then((data) => {
        if (cancelled) return;
        setWorking((files) => {
          const entry = files[path];
          if (!entry || entry.origin !== origin || entry.text !== undefined) return files;
          const loaded = data.text === null ? { binary: true } : { text: data.text, saved: data.text };
          return { ...files, [path]: { ...entry, size: data.size, ...loaded } };
        });
      })
      .catch((reason) => !cancelled && setError(message(reason, `Could not read ${path}.`)));
    return () => {
      cancelled = true;
    };
  }, [skill, selected?.path, file?.origin, needsText]);

  if (!skill)
    return error ? (
      <Alert variant="destructive">
        <AlertDescription role="alert">{error}</AlertDescription>
      </Alert>
    ) : (
      <Skeleton className="h-96 w-full" />
    );

  const paths = Object.keys(working);
  const rows = treeRows(paths, folders);
  const taken = (path: string) => rows.some((row) => row.path === path);
  const changes = fileChanges(
    skill.files.map((item) => item.path),
    working,
  );
  const dirty = changes.length > 0;
  const skillMd = working["SKILL.md"];
  const skillMdCheck = !skillMd
    ? "A skill needs a SKILL.md."
    : skillMd.text !== undefined
      ? checkSkillMd(skillMd.text)
      : undefined;
  const folder = selected ? (selected.kind === "folder" ? selected.path : parentOf(selected.path)) : "";
  const prefix = folder ? `${folder}/` : "";

  function startNaming(mode: Naming["mode"]) {
    setNaming({ mode, value: mode === "rename" ? selected!.path : prefix, error: null });
  }
  function submitNaming(event: FormEvent) {
    event.preventDefault();
    if (!naming) return;
    const path = naming.value.trim();
    const problem = pathProblem(path, taken);
    if (problem) return setNaming({ ...naming, error: problem });
    if (naming.mode === "file") {
      setWorking({ ...working, [path]: { origin: null, size: 0, text: "" } });
      setSelected({ path, name: path.split("/").pop()!, depth: 0, kind: "file" });
    } else if (naming.mode === "folder") {
      setFolders([...folders, path]);
      setSelected({ path, name: path.split("/").pop()!, depth: 0, kind: "folder" });
    } else if (selected) {
      const from = selected.path;
      setWorking(Object.fromEntries(Object.entries(working).map(([key, value]) => [moved(key, from, path), value])));
      setFolders(folders.map((item) => moved(item, from, path)));
      setSelected({ ...selected, path, name: path.split("/").pop()! });
    }
    setNaming(null);
  }
  function remove(row: TreeRow) {
    setWorking(Object.fromEntries(Object.entries(working).filter(([key]) => !under(key, row.path))));
    setFolders(folders.filter((item) => !under(item, row.path)));
    setSelected(null);
  }
  async function addUploads(list: FileList | null) {
    const chosen = Array.from(list ?? []);
    if (upload.current) upload.current.value = "";
    try {
      const added = await Promise.all(
        chosen.map(async (item) => [`${prefix}${item.name}`, item.size, await readBase64(item)] as const),
      );
      setWorking((files) => {
        const next = { ...files };
        for (const [path, size, base64] of added) next[path] = { origin: files[path]?.origin ?? null, size, base64 };
        return next;
      });
    } catch {
      setError("Could not read the uploaded files.");
    }
  }
  async function run(work: () => Promise<string>, fallback: string) {
    setBusy(true);
    setError(null);
    try {
      toast.success(await work());
    } catch (reason) {
      setError(message(reason, fallback));
    } finally {
      setBusy(false);
    }
  }
  const save = () =>
    run(async () => {
      const result = await saveSkillFiles(skill, changes);
      open(result.skill, selected?.path);
      const { version, source } = result.skill;
      return `Saved version ${version} (${sourceLabel(source)}). ${changedLabel(result.changed)}`;
    }, "Could not save this skill. Your changes are still here.");
  const restore = (version: SkillVersion) =>
    run(async () => {
      open(await restoreSkill(skill, version.version), selected?.path);
      setVersionsOpen(false);
      return `Restored version ${version.version}.`;
    }, "Could not restore this version.");
  const filesUnder = (row: TreeRow) => paths.filter((path) => under(path, row.path)).length;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <BackLink to="/skills">All skills</BackLink>
          <h2 className="text-xl font-semibold">{skill.name}</h2>
          <p className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            <span>Version</span>
            <span className="font-mono text-xs">{skill.version}</span>
            <span>·</span>
            <span className="break-all">{sourceLabel(skill.source)}</span>
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" disabled={busy} onClick={() => setVersionsOpen(true)}>
            <History data-icon="inline-start" />
            Previous versions
          </Button>
          <Button disabled={busy || !dirty} onClick={() => void save()}>
            {busy && <Spinner data-icon="inline-start" />}
            Save
          </Button>
        </div>
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertDescription role="alert">{error}</AlertDescription>
        </Alert>
      )}
      {skillMdCheck !== undefined && (
        <p className={cn("text-sm", skillMdCheck ? "text-destructive" : "text-muted-foreground")}>
          {skillMdCheck ?? `Frontmatter OK: ${skillMdName(skillMd!.text!)}`}
        </p>
      )}
      <div className="grid min-h-[28rem] gap-4 md:grid-cols-[18rem_1fr]">
        <section aria-label="Files" className="flex flex-col gap-2 rounded-lg border p-2">
          <div className="flex flex-wrap gap-1">
            <Button size="sm" variant="ghost" onClick={() => startNaming("file")}>
              <FilePlus />
              Add file
            </Button>
            <Button size="sm" variant="ghost" onClick={() => startNaming("folder")}>
              <FolderPlus />
              Add folder
            </Button>
            <Button size="sm" variant="ghost" onClick={() => upload.current?.click()}>
              <Upload />
              Upload files
            </Button>
            <input
              ref={upload}
              type="file"
              multiple
              hidden
              aria-label="Upload files"
              onChange={(event) => void addUploads(event.target.files)}
            />
            <Button size="sm" variant="ghost" disabled={!selected} onClick={() => startNaming("rename")}>
              <Pencil />
              Rename
            </Button>
            <Button size="sm" variant="ghost" disabled={!selected} onClick={() => selected && setDeleting(selected)}>
              <Trash2 />
              Delete
            </Button>
          </div>
          {naming && (
            <form onSubmit={submitNaming} className="flex flex-col gap-2 rounded-md bg-muted/50 p-2">
              <Field data-invalid={naming.error !== null}>
                <FieldLabel htmlFor="skill-naming">{NAMING_LABEL[naming.mode]}</FieldLabel>
                <Input
                  id="skill-naming"
                  autoFocus
                  value={naming.value}
                  aria-invalid={naming.error !== null}
                  onChange={(event) => setNaming({ ...naming, value: event.target.value, error: null })}
                />
                {naming.error && <FieldError>{naming.error}</FieldError>}
              </Field>
              <div className="flex gap-2">
                <Button size="sm">{naming.mode === "rename" ? "Rename" : "Add"}</Button>
                <Button size="sm" type="button" variant="outline" onClick={() => setNaming(null)}>
                  Cancel
                </Button>
              </div>
            </form>
          )}
          <ul className="flex flex-col">
            {rows.map((row) => (
              <li key={`${row.kind}:${row.path}`}>
                <button
                  type="button"
                  aria-label={row.path}
                  aria-current={selected?.path === row.path ? "true" : undefined}
                  onClick={() => setSelected(row)}
                  className={cn(
                    "flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm hover:bg-accent",
                    selected?.path === row.path && "bg-accent font-medium",
                  )}
                  style={{ paddingLeft: `${0.5 + row.depth}rem` }}
                >
                  {row.kind === "folder" ? (
                    <Folder className="size-4 shrink-0" />
                  ) : (
                    <FileIcon className="size-4 shrink-0" />
                  )}
                  <span className="truncate">{row.name}</span>
                </button>
              </li>
            ))}
          </ul>
          {folders.some((item) => !paths.some((path) => under(path, item))) && (
            <p className="text-xs text-muted-foreground">Empty folders are not saved.</p>
          )}
        </section>
        <section aria-label="Editor" className="flex min-w-0 flex-col gap-2">
          {!file ? (
            <p className="text-sm text-muted-foreground">
              {selected ? `Folder ${selected.path}` : "Select a file to edit it."}
            </p>
          ) : file.text !== undefined ? (
            <>
              <p className="font-mono text-sm">{selected!.path}</p>
              <Textarea
                aria-label={`Contents of ${selected!.path}`}
                className="min-h-[26rem] flex-1 font-mono text-sm"
                spellCheck={false}
                value={file.text}
                onChange={(event) =>
                  setWorking({
                    ...working,
                    [selected!.path]: {
                      ...file,
                      text: event.target.value,
                      size: new TextEncoder().encode(event.target.value).length,
                    },
                  })
                }
              />
            </>
          ) : file.binary || file.base64 !== undefined ? (
            <div className="flex flex-col gap-1 rounded-lg border p-4">
              <p className="font-mono text-sm">{selected!.path}</p>
              <p className="text-sm">{sizeLabel(file.size)}</p>
              <p className="text-sm text-muted-foreground">
                {file.base64 !== undefined
                  ? "Uploaded; written when you save."
                  : "A binary file cannot be edited here; upload a file with the same name to replace it."}
              </p>
            </div>
          ) : (
            <Skeleton className="h-96 w-full" />
          )}
        </section>
      </div>

      <Sheet open={versionsOpen} onOpenChange={setVersionsOpen}>
        <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-lg">
          <SheetHeader>
            <SheetTitle>Previous versions</SheetTitle>
            <SheetDescription>
              The last three folders of this skill are kept. Restore makes one the current files again.
            </SheetDescription>
          </SheetHeader>
          <ul className="flex flex-col gap-2">
            {skill.versions.map((version) => (
              <li key={version.version} className="flex items-start justify-between gap-3 rounded-md border p-3">
                <div className="flex min-w-0 flex-col gap-1">
                  <p className="flex items-center gap-2 font-mono text-sm">
                    {version.version}
                    {version.current && <Badge variant="secondary">Current</Badge>}
                  </p>
                  <p className="text-sm text-muted-foreground">{when(version.created_at)}</p>
                  <p className="break-all text-sm text-muted-foreground">{sourceLabel(version.source)}</p>
                </div>
                {!version.current && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    aria-label={`Restore ${version.version}`}
                    onClick={() => setRestoring(version)}
                  >
                    Restore
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </SheetContent>
      </Sheet>

      <AlertDialog open={restoring !== null} onOpenChange={(value) => !value && setRestoring(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Restore version {restoring?.version}?</AlertDialogTitle>
            <AlertDialogDescription>
              This replaces the current files ({skill.version}) with the files of version {restoring?.version}. The
              current version stays in Previous versions.
              {dirty && " Your unsaved changes are discarded."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (restoring) void restore(restoring);
                setRestoring(null);
              }}
            >
              Restore
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <ConfirmRemove
        removal={
          deleting && {
            title:
              deleting.kind === "folder"
                ? `Delete folder “${deleting.path}” and its ${filesUnder(deleting)} ${filesUnder(deleting) === 1 ? "file" : "files"}?`
                : `Delete “${deleting.path}”?`,
            description: "It is removed from the skill when you save. Previous versions keep their copy.",
            action: deleting.kind === "folder" ? "Delete folder" : "Delete file",
            run: () => remove(deleting),
          }
        }
        onClose={() => setDeleting(null)}
      />

      <LeaveGuard dirty={dirty} leaving={leaving} />
    </div>
  );
}
