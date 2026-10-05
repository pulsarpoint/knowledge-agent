import { useId, useState } from "react";
import { ChevronDown, RefreshCw } from "lucide-react";
import type { AgentContext, ContextOptions } from "./types";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "~/components/ui/collapsible";
import { Field, FieldContent, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "~/components/ui/field";

export const DEFAULT_CONTEXT: AgentContext = { collections: null };

export default function AdvancedContext({ value, onChange, available, locked, options, loading, error, refresh }: {
  value: AgentContext; onChange: (value: AgentContext) => void;
  available: { docs: boolean };
  locked: boolean;
  options: ContextOptions | null; loading: boolean; error: string | null; refresh: () => void;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const collections = options?.collections ?? [];
  const summary = value.collections === null ? "All collections" : `${value.collections.length} ${value.collections.length === 1 ? "collection" : "collections"}`;
  function toggle(name: string) {
    const selected = value.collections ?? [];
    onChange({ ...value, collections: selected.includes(name) ? selected.filter((n) => n !== name) : [...selected, name] });
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
          <FieldLegend>Collections</FieldLegend>
          <FieldGroup>
            <Field orientation="horizontal">
              <Checkbox id={`${id}-all`} checked={value.collections === null} disabled={locked}
                onCheckedChange={(checked) => onChange({ ...value, collections: checked ? null : [] })} />
              <FieldLabel htmlFor={`${id}-all`}>All collections</FieldLabel>
            </Field>
            {value.collections !== null && <>
              <FieldGroup className="max-h-64 overflow-y-auto" data-slot="checkbox-group">
                {collections.map((item, index) => <Field key={item.name} orientation="horizontal">
                  <Checkbox id={`${id}-c-${index}`} checked={value.collections!.includes(item.name)} disabled={locked} onCheckedChange={() => toggle(item.name)} />
                  <FieldContent>
                    <FieldLabel htmlFor={`${id}-c-${index}`}>{item.name}</FieldLabel>
                    <FieldDescription>{item.description ? `${item.description} · ` : ""}{item.indexed_files} indexed files</FieldDescription>
                  </FieldContent>
                </Field>)}
                {value.collections.filter((n) => !collections.some((c) => c.name === n)).map((n) => <p key={n} className="text-sm text-muted-foreground">{n} (unavailable)</p>)}
                {!collections.length && !loading && <p className="text-sm text-muted-foreground">No collections available.</p>}
              </FieldGroup>
              {!value.collections.length && <p className="text-sm text-destructive">Select at least one collection.</p>}
            </>}
          </FieldGroup>
        </FieldSet>}
      </FieldGroup>
    </CollapsibleContent>
  </Collapsible>;
}
