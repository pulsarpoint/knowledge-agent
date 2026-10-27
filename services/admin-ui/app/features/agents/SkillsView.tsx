import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { BookOpen, Download, MoreHorizontal, Pencil, Plus, RefreshCw, Trash2, Upload } from "lucide-react";
import { ApiError } from "~/lib/api";
import {
  changedLabel,
  deleteSkill,
  getSkills,
  setSkillEnabled,
  sourceLabel,
  updateFromSource,
  updateFromUpload,
  type SkillSummary,
} from "./skills";
import { ImportSkillsSheet, NewSkillSheet } from "./SkillImport";
import ConfirmRemove from "~/components/ConfirmRemove";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "~/components/ui/dropdown-menu";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "~/components/ui/empty";
import { Field, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";
import { Switch } from "~/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

const message = (error: unknown, fallback: string) => (error instanceof ApiError ? error.message : fallback);

export default function SkillsView() {
  const [skills, setSkills] = useState<SkillSummary[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sheet, setSheet] = useState<"new" | "import" | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [removing, setRemoving] = useState<SkillSummary | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    getSkills(controller.signal)
      .then((data) => {
        if (controller.signal.aborted) return;
        setSkills(data.skills);
        setError(null);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(message(reason, "Could not load skills. Try refreshing."));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [refresh]);

  async function act(skill: SkillSummary, run: () => Promise<string>, fallback: string) {
    setPending(skill.id);
    setNotice(null);
    try {
      setNotice(await run());
      setError(null);
      setRefresh((value) => value + 1);
    } catch (reason) {
      setError(message(reason, fallback));
    } finally {
      setPending(null);
    }
  }
  const update = (skill: SkillSummary) =>
    act(
      skill,
      async () => {
        const { skill: updated, changed } = await updateFromSource(skill);
        return `${updated.name} updated to version ${updated.version}. ${changedLabel(changed)}`;
      },
      "Could not update this skill from its source.",
    );
  const uploadUpdate = (skill: SkillSummary, file: File) =>
    act(
      skill,
      async () => {
        const { skill: updated, changed } = await updateFromUpload(skill, file);
        return `${updated.name} updated to version ${updated.version}. ${changedLabel(changed)}`;
      },
      "Could not update this skill from this zip.",
    );
  const toggle = (skill: SkillSummary, enabled: boolean) =>
    act(
      skill,
      async () => {
        const saved = await setSkillEnabled(skill, enabled);
        return `${saved.name} ${saved.enabled ? "enabled" : "disabled"}.`;
      },
      "Could not change this skill. Try refreshing.",
    );
  const remove = (skill: SkillSummary) =>
    act(
      skill,
      async () => {
        await deleteSkill(skill.id);
        return `Deleted ${skill.name}.`;
      },
      "Could not delete this skill.",
    );

  const blocked = loading || pending !== null;
  const unavailable = skills === null;
  const matching =
    skills?.filter((skill) =>
      `${skill.name} ${skill.description}`.toLowerCase().includes(query.trim().toLowerCase()),
    ) ?? [];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col gap-1">
          <p className="text-sm text-muted-foreground">
            {skills ? `${skills.length} ${skills.length === 1 ? "skill" : "skills"}` : "Loading skills…"}
          </p>
          <p className="text-sm text-muted-foreground">
            Every agent sees every enabled skill; disable a skill to hide it.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" disabled={blocked} onClick={() => setRefresh((value) => value + 1)}>
            <RefreshCw data-icon="inline-start" />
            Refresh
          </Button>
          <Button variant="outline" disabled={blocked || unavailable} onClick={() => setSheet("import")}>
            <Download data-icon="inline-start" />
            Import
          </Button>
          <Button disabled={blocked || unavailable} onClick={() => setSheet("new")}>
            <Plus data-icon="inline-start" />
            New skill
          </Button>
        </div>
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertDescription role="alert">{error}</AlertDescription>
        </Alert>
      )}
      {notice && (
        <Alert>
          <AlertDescription role="status">{notice}</AlertDescription>
        </Alert>
      )}
      <NewSkillSheet open={sheet === "new"} onClose={() => setSheet(null)} />
      <ImportSkillsSheet
        open={sheet === "import"}
        onClose={() => setSheet(null)}
        onImported={(names) => {
          setSheet(null);
          setNotice(`Imported ${names.join(", ")}.`);
          setRefresh((value) => value + 1);
        }}
      />
      {skills === null && loading && <Skeleton className="h-48 w-full" />}
      {skills?.length === 0 && (
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <BookOpen />
            </EmptyMedia>
            <EmptyTitle>No skills yet</EmptyTitle>
            <EmptyDescription>
              A skill is a folder with a SKILL.md and the files it uses. Write one, or import it from a zip or a git
              repository.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      )}
      {skills !== null && skills.length > 0 && (
        <>
          <Field>
            <FieldLabel htmlFor="filter-skills">Filter skills</FieldLabel>
            <Input
              id="filter-skills"
              placeholder="Search names and descriptions…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </Field>
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Skill</TableHead>
                  <TableHead>Version</TableHead>
                  <TableHead>Source</TableHead>
                  <TableHead>Updated</TableHead>
                  <TableHead>Enabled</TableHead>
                  <TableHead className="w-12 text-right">
                    <span className="sr-only">Actions</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {matching.map((skill) => (
                  <TableRow key={skill.id}>
                    <TableCell className="max-w-md whitespace-normal">
                      <Link
                        to={`/skills/${encodeURIComponent(skill.id)}`}
                        className="break-words font-medium hover:underline"
                      >
                        {skill.name}
                      </Link>
                      <p className="mt-1 whitespace-pre-wrap break-words text-sm text-muted-foreground">
                        {skill.description}
                      </p>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{skill.version}</TableCell>
                    <TableCell className="max-w-xs whitespace-normal break-all text-sm">
                      {sourceLabel(skill.source)}
                    </TableCell>
                    <TableCell className="text-sm">{new Date(skill.updated_at).toLocaleString()}</TableCell>
                    <TableCell>
                      <Switch
                        aria-label={`${skill.name} enabled`}
                        checked={skill.enabled}
                        disabled={blocked}
                        onCheckedChange={(enabled) => void toggle(skill, enabled)}
                      />
                    </TableCell>
                    <TableCell className="text-right">
                      <SkillActions
                        skill={skill}
                        disabled={blocked}
                        onUpdate={() => void update(skill)}
                        onUpload={(file) => void uploadUpdate(skill, file)}
                        onDelete={() => setRemoving(skill)}
                      />
                    </TableCell>
                  </TableRow>
                ))}
                {matching.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={6} className="text-center">
                      No skills match this filter.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </>
      )}
      <ConfirmRemove
        removal={
          removing && {
            title: `Delete “${removing.name}”?`,
            description:
              "The skill, all its files and its previous versions are deleted, and agents that use it lose it. This cannot be undone.",
            action: "Delete skill",
            run: () => void remove(removing),
          }
        }
        onClose={() => setRemoving(null)}
      />
    </div>
  );
}

/** A skill row's actions in one menu: Edit, Update from source (git skills), Update from upload… (a zip), Delete. */
function SkillActions({
  skill,
  disabled,
  onUpdate,
  onUpload,
  onDelete,
}: {
  skill: SkillSummary;
  disabled: boolean;
  onUpdate: () => void;
  onUpload: (file: File) => void;
  onDelete: () => void;
}) {
  const zip = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={zip}
        type="file"
        accept=".zip,application/zip"
        className="sr-only"
        tabIndex={-1}
        aria-label={`Zip for ${skill.name}`}
        disabled={disabled}
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (file) onUpload(file);
        }}
      />
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" aria-label={`Actions for ${skill.name}`} disabled={disabled}>
            <MoreHorizontal />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem asChild>
            <Link to={`/skills/${encodeURIComponent(skill.id)}`}>
              <Pencil />
              Edit
            </Link>
          </DropdownMenuItem>
          {skill.source.kind === "git" && (
            <DropdownMenuItem onSelect={onUpdate}>
              <RefreshCw />
              Update from source
            </DropdownMenuItem>
          )}
          <DropdownMenuItem onSelect={() => zip.current?.click()}>
            <Upload />
            Update from upload…
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem className="text-destructive focus:text-destructive" onSelect={onDelete}>
            <Trash2 />
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  );
}
