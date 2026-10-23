import { useEffect, useState } from "react";
import { getTools, type CatalogTool } from "./api";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import { Field, FieldDescription, FieldLabel, FieldSet, FieldLegend } from "~/components/ui/field";

const GROUP_LABELS: Record<string, string> = {
  documents: "Documents",
  inventory: "Inventory",
  code: "Code",
  servers: "Servers",
  memory: "Memory",
  agents: "Agents",
};

/** The catalog tools by group, each switched on or off. `off` is what is switched off here; `locked` tools are off for
 * another reason (the agent's own list): shown off and disabled, left alone by All and None. */
export default function ToolsList({
  off,
  onChange,
  locked = [],
  disabled = false,
}: {
  off: string[];
  onChange: (off: string[]) => void;
  locked?: string[];
  disabled?: boolean;
}) {
  const [tools, setTools] = useState<CatalogTool[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    getTools()
      .then((value) => live && setTools(value.tools))
      .catch((e) => live && setError(e instanceof Error ? e.message : "Could not load the tools."));
    return () => {
      live = false;
    };
  }, []);
  if (error)
    return (
      <p role="alert" className="text-sm text-destructive">
        {error}
      </p>
    );
  if (!tools) return <p className="text-sm text-muted-foreground">Loading tools…</p>;
  const isOff = (name: string) => off.includes(name) || locked.includes(name);
  const groups = [...new Set(tools.map((tool) => tool.group))];
  const switchGroup = (group: string, on: boolean) => {
    const names = tools.filter((tool) => tool.group === group && !locked.includes(tool.name)).map((tool) => tool.name);
    onChange(
      on ? off.filter((name) => !names.includes(name)) : [...off, ...names.filter((name) => !off.includes(name))],
    );
  };
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        {tools.filter((tool) => !isOff(tool.name)).length} of {tools.length} tools on
      </p>
      {groups.map((group) => {
        const label = GROUP_LABELS[group] ?? group;
        return (
          <FieldSet key={group}>
            <div className="flex items-center justify-between gap-2">
              <FieldLegend variant="label">{label}</FieldLegend>
              <div className="flex gap-1">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={disabled}
                  aria-label={`All ${label}`}
                  onClick={() => switchGroup(group, true)}
                >
                  All
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={disabled}
                  aria-label={`None ${label}`}
                  onClick={() => switchGroup(group, false)}
                >
                  None
                </Button>
              </div>
            </div>
            {tools
              .filter((tool) => tool.group === group)
              .map((tool) => (
                <Field key={tool.name} orientation="horizontal">
                  <Checkbox
                    id={`tool-${tool.name}`}
                    aria-label={tool.name}
                    disabled={disabled || locked.includes(tool.name)}
                    checked={!isOff(tool.name)}
                    onCheckedChange={(on) =>
                      onChange(on === true ? off.filter((name) => name !== tool.name) : [...off, tool.name])
                    }
                  />
                  <FieldLabel htmlFor={`tool-${tool.name}`}>{tool.name}</FieldLabel>
                  <FieldDescription>
                    {tool.description}
                    {locked.includes(tool.name) && <span className="ml-2 font-medium">Off for this agent</span>}
                  </FieldDescription>
                </Field>
              ))}
          </FieldSet>
        );
      })}
    </div>
  );
}
