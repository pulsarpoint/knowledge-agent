import { useEffect, useState } from "react";
import { getOutputSchemas, type OutputSchemaChoice, type OutputSchemaSummary } from "./api";
import { Field, FieldDescription, FieldLabel } from "~/components/ui/field";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";

const NONE = "none";
const CURRENT = "current";

/** Output schema: none, or a schema of the library and then its current version or a fixed one. A chosen schema that
 * is no longer listed still shows, so it can be cleared. */
export default function OutputSchemaPicker({
  idPrefix,
  value,
  onChange,
  disabled = false,
}: {
  idPrefix: string;
  value: OutputSchemaChoice | null;
  onChange: (choice: OutputSchemaChoice | null) => void;
  disabled?: boolean;
}) {
  const [schemas, setSchemas] = useState<OutputSchemaSummary[] | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    getOutputSchemas(controller.signal)
      .then((result) => setSchemas(result.schemas))
      .catch(() => {
        if (!controller.signal.aborted) setSchemas([]);
      });
    return () => controller.abort();
  }, []);
  const chosen = value ? schemas?.find((item) => item.id === value.id) : undefined;
  const missing = value && schemas && !chosen ? value : null;
  const latest = chosen?.current_version ?? value?.version ?? 0;
  const versions = Array.from({ length: latest }, (_, index) => latest - index);
  return (
    <Field>
      <FieldLabel htmlFor={`${idPrefix}-output-schema`}>Output schema</FieldLabel>
      <div className="flex flex-wrap gap-2">
        <Select
          value={value?.id ?? NONE}
          disabled={disabled}
          onValueChange={(id) => {
            if (!id) return; // Radix reports "" while the value is not among the items yet
            const next = schemas?.find((item) => item.id === id);
            onChange(id === NONE || !next ? null : { id, name: next.name, version: null });
          }}
        >
          <SelectTrigger id={`${idPrefix}-output-schema`} aria-label="Output schema" className="min-w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              <SelectItem value={NONE}>None</SelectItem>
              {(schemas ?? []).map((item) => (
                <SelectItem key={item.id} value={item.id}>
                  {item.name}
                </SelectItem>
              ))}
              {value && !chosen && (
                <SelectItem
                  value={value.id}
                >{`${value.name || value.id}${missing ? " (not available)" : ""}`}</SelectItem>
              )}
            </SelectGroup>
          </SelectContent>
        </Select>
        {value && (
          <Select
            value={value.version === null ? CURRENT : String(value.version)}
            disabled={disabled}
            onValueChange={(next) => {
              if (!next) return;
              onChange({ ...value, version: next === CURRENT ? null : Number(next) });
            }}
          >
            <SelectTrigger aria-label="Schema version" className="min-w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                <SelectItem value={CURRENT}>{chosen ? `Current (v${chosen.current_version})` : "Current"}</SelectItem>
                {versions.map((number) => (
                  <SelectItem key={number} value={String(number)}>
                    v{number}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
        )}
      </div>
      <FieldDescription>The run finishes with JSON in this schema. Current follows later edits.</FieldDescription>
    </Field>
  );
}
