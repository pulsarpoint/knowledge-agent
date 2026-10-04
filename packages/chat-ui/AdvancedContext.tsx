import { useId, useState } from "react";
import { ChevronDown, RefreshCw } from "lucide-react";
import type { AgentContext, ContextOptions } from "./types";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "~/components/ui/collapsible";
import { Field, FieldContent, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "~/components/ui/field";
import { Switch } from "~/components/ui/switch";

export const DEFAULT_CONTEXT: AgentContext = { folders: null, include_subfolders: true, repositories: null };

export default function AdvancedContext({ value, onChange, available, locked, options, loading, error, refresh }: {
  value: AgentContext; onChange: (value: AgentContext) => void;
  available: { docs: boolean };
  locked: boolean;
  options: ContextOptions | null; loading: boolean; error: string | null; refresh: () => void;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const folders = options?.folders ?? [];
  const summary = value.folders === null ? "All document folders" : `${value.folders.length} document folders`;
  function toggleFolder(item: string) {
    const selected = value.folders ?? [];
    onChange({ ...value, folders: selected.includes(item) ? selected.filter((path) => path !== item) : [...selected, item] });
  }
  return <Collapsible open={open} onOpenChange={setOpen} className="rounded-lg border p-3">
    <div className="flex flex-wrap items-center gap-2">
      <CollapsibleTrigger asChild>
        <Button variant="ghost" size="sm"><ChevronDown className={open ? "rotate-180" : ""} />Advanced</Button>
      </CollapsibleTrigger>
      <span className="text-xs text-muted-foreground">{summary}</span>
    </div>
    <CollapsibleContent className="pt-4">
      <div className="mb-4 flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">{locked ? "Start a new conversation to change context." : "Choose the sources this conversation can use."}</p>
        <Button type="button" variant="ghost" size="sm" onClick={refresh} disabled={loading} aria-label="Refresh context"><RefreshCw />{loading ? "Loading…" : "Refresh"}</Button>
      </div>
      {(error || options?.warnings.length) ? <Alert className="mb-4"><AlertDescription>{error ?? options?.warnings.join(" ")}</AlertDescription></Alert> : null}
      <FieldGroup>
        {available.docs && <FieldSet>
          <FieldLegend>Documents</FieldLegend>
          <FieldGroup>
            <Field orientation="horizontal">
              <Checkbox id={`${id}-all-folders`} checked={value.folders === null} disabled={locked} onCheckedChange={(checked) => onChange({ ...value, folders: checked ? null : [] })} />
              <FieldLabel htmlFor={`${id}-all-folders`}>All document folders</FieldLabel>
            </Field>
            {value.folders !== null && <>
              <Field orientation="horizontal">
                <Switch id={`${id}-children`} checked={value.include_subfolders} disabled={locked} onCheckedChange={(checked) => onChange({ ...value, include_subfolders: checked })} />
                <FieldLabel htmlFor={`${id}-children`}>Include subfolders</FieldLabel>
              </Field>
              <FieldGroup className="max-h-64 overflow-y-auto" data-slot="checkbox-group">
                {folders.map((folder, index) => <Field key={folder.path} orientation="horizontal">
                  <Checkbox id={`${id}-folder-${index}`} checked={value.folders!.includes(folder.path)} disabled={locked} onCheckedChange={() => toggleFolder(folder.path)} />
                  <FieldContent>
                    <FieldLabel htmlFor={`${id}-folder-${index}`} className="break-all font-mono">{folder.path}</FieldLabel>
                    <FieldDescription>{folder.description ? `${folder.description} · ` : ""}{folder.indexed_files} indexed files in this folder</FieldDescription>
                  </FieldContent>
                </Field>)}
                {value.folders.filter((path) => !folders.some((folder) => folder.path === path)).map((path) => <p key={path} className="text-sm text-muted-foreground">{path} (unavailable)</p>)}
                {!folders.length && !loading && <p className="text-sm text-muted-foreground">No document folders available.</p>}
              </FieldGroup>
              {!value.folders.length && <p className="text-sm text-destructive">Select at least one folder.</p>}
            </>}
          </FieldGroup>
        </FieldSet>}
      </FieldGroup>
    </CollapsibleContent>
  </Collapsible>;
}
