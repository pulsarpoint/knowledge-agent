import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Link } from "react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { BookOpen, Download, Pencil, Plus, RefreshCw, Upload } from "lucide-react";
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
import DataTable, { sortHeader, sortRows, TableEmpty, type Sort } from "~/components/DataTable";
import RowMenu, { deleteMenuItem } from "~/components/RowMenu";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Switch } from "~/components/ui/switch";

const message = (error: unknown, fallback: string) => (error instanceof ApiError ? error.message : fallback);

type SkillSort = "name" | "updated" | "enabled";
const byName = (a: SkillSummary, b: SkillSummary) => a.name.localeCompare(b.name);
const SKILL_ORDER: Record<SkillSort, (a: SkillSummary, b: SkillSummary) => number> = {
  name: byName,
  updated: (a, b) => a.updated_at.localeCompare(b.updated_at) || byName(a, b),
  enabled: (a, b) => Number(b.enabled) - Number(a.enabled) || byName(a, b),
};

/** Skills: a table (name and description, version, source, updated, Enabled switch; Edit, updates and Delete in the row
 * menu) with search and sortable headers; New skill and Import open right Sheets. */
export default function SkillsView() {
  const [skills, setSkills] = useState<SkillSummary[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort<SkillSort>>({ key: "name", descending: false });
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
    try {
      toast.success(await run());
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
  const text = query.trim().toLowerCase();
  const shown = sortRows(
    skills?.filter((skill) => `${skill.name} ${skill.description}`.toLowerCase().includes(text)) ?? [],
    sort,
    SKILL_ORDER,
  );
  const newSkill = (
    <Button disabled={blocked || unavailable} onClick={() => setSheet("new")}>
      <Plus data-icon="inline-start" />
      New skill
    </Button>
  );
  const columns: ColumnDef<SkillSummary, unknown>[] = [
    {
      id: "name",
      header: () => sortHeader("Skill", "name", sort, setSort),
      cell: ({ row: { original: skill } }) => (
        <div className="max-w-md">
          <Link to={`/skills/${encodeURIComponent(skill.id)}`} className="break-words font-medium hover:underline">
            {skill.name}
          </Link>
          <p className="mt-1 whitespace-pre-wrap break-words text-sm text-muted-foreground">{skill.description}</p>
        </div>
      ),
    },
    {
      id: "version",
      header: "Version",
      cell: ({ row: { original: skill } }) => <span className="font-mono text-xs">{skill.version}</span>,
    },
    {
      id: "source",
      header: "Source",
      cell: ({ row: { original: skill } }) => (
        <span className="max-w-xs break-all text-sm">{sourceLabel(skill.source)}</span>
      ),
    },
    {
      id: "updated",
      header: () => sortHeader("Updated", "updated", sort, setSort),
      cell: ({ row: { original: skill } }) => (
        <span className="text-sm">{new Date(skill.updated_at).toLocaleString()}</span>
      ),
    },
    {
      id: "enabled",
      header: () => sortHeader("Enabled", "enabled", sort, setSort),
      cell: ({ row: { original: skill } }) => (
        <Switch
          aria-label={`${skill.name} enabled`}
          checked={skill.enabled}
          disabled={blocked}
          onCheckedChange={(enabled) => void toggle(skill, enabled)}
        />
      ),
    },
    {
      id: "actions",
      header: () => <span className="sr-only">Actions</span>,
      cell: ({ row: { original: skill } }) => (
        <div className="flex justify-end">
          <RowMenu
            name={skill.name}
            disabled={blocked}
            items={[
              { label: "Edit", icon: Pencil, to: `/skills/${encodeURIComponent(skill.id)}` },
              skill.source.kind === "git" && {
                label: "Update from source",
                icon: RefreshCw,
                onSelect: () => void update(skill),
              },
              {
                label: "Update from upload…",
                icon: Upload,
                accept: ".zip,application/zip",
                onFile: (file) => void uploadUpdate(skill, file),
              },
              deleteMenuItem(() => setRemoving(skill)),
            ]}
          />
        </div>
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          Every agent sees every enabled skill; disable a skill to hide it.
        </p>
        <div className="flex gap-2">
          <Button variant="outline" disabled={blocked || unavailable} onClick={() => setSheet("import")}>
            <Download data-icon="inline-start" />
            Import
          </Button>
          {newSkill}
        </div>
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertDescription role="alert">
            {error}
            {unavailable && (
              <Button variant="outline" size="sm" disabled={loading} onClick={() => setRefresh((value) => value + 1)}>
                Retry
              </Button>
            )}
          </AlertDescription>
        </Alert>
      )}
      <NewSkillSheet open={sheet === "new"} onClose={() => setSheet(null)} />
      <ImportSkillsSheet
        open={sheet === "import"}
        onClose={() => setSheet(null)}
        onImported={(names) => {
          setSheet(null);
          toast.success(`Imported ${names.join(", ")}.`);
          setRefresh((value) => value + 1);
        }}
      />
      <DataTable
        columns={columns}
        data={shown}
        loading={skills === null && loading}
        rowId={(skill) => skill.id}
        search={{ plural: "skills", value: query, onChange: setQuery }}
        sort={sort}
        resetKey={`${sort.key}\n${sort.descending}`}
        empty={
          skills?.length === 0 ? (
            <TableEmpty
              icon={BookOpen}
              title="No skills yet"
              description="A skill is a folder with a SKILL.md and the files it uses. Write one, or import it from a zip or a git repository."
            >
              {newSkill}
            </TableEmpty>
          ) : skills ? (
            "No skills match."
          ) : (
            "Could not load skills."
          )
        }
      />
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
