import { useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { useIsMobile } from "~/hooks/use-mobile";
import { Badge } from "~/components/ui/badge";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "~/components/ui/collapsible";
import { Input } from "~/components/ui/input";
import { Label } from "~/components/ui/label";
import { Switch } from "~/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";

/** One entry of a type: on when its id is in the value. Missing: a resource deleted since it was chosen. Locked: always
 * on (cannot change). Disabled: cannot be switched. Note: a line under it (e.g. "via environment Production"). */
export interface ResourceItem {
  id: string;
  name: string;
  description?: string;
  missing?: boolean;
  locked?: boolean;
  disabled?: boolean;
  note?: ReactNode;
}
export interface ResourceGroup {
  key: string;
  label: string;
  /** The plural in texts: "Filter servers…", "No servers.". */
  many: string;
  items: ResourceItem[];
}

/** The resources an agent (or one run of it) may use: a tab per type ("Servers 3/12"), every entry with a switch, a
 * filter and an All switch above the list. Narrow screens (below md) get an accordion section per type instead. */
export default function ResourceTabs({
  groups,
  value,
  onChange,
  disabled = false,
  variant,
}: {
  groups: ResourceGroup[];
  value: Partial<Record<string, string[]>>;
  onChange: (key: string, ids: string[]) => void;
  disabled?: boolean;
  variant?: "tabs" | "accordion";
}) {
  const narrow = useIsMobile();
  const [tab, setTab] = useState(groups[0]?.key ?? "");
  const [filters, setFilters] = useState<Record<string, string>>({});
  const ids = (group: ResourceGroup) => value[group.key] ?? [];
  const isOn = (group: ResourceGroup, item: ResourceItem) => !!item.locked || ids(group).includes(item.id);
  const count = (group: ResourceGroup) =>
    `${group.label} ${group.items.filter((item) => isOn(group, item)).length}/${group.items.length}`;

  const list = (group: ResourceGroup) => {
    const filter = (filters[group.key] ?? "").trim().toLowerCase();
    const shown = group.items.filter((item) => `${item.name} ${item.description ?? ""}`.toLowerCase().includes(filter));
    const changeable = shown.filter((item) => !item.locked && !item.disabled);
    const allOn = changeable.length > 0 && changeable.every((item) => ids(group).includes(item.id));
    const setAll = (on: boolean) => {
      const touched = changeable.map((item) => item.id);
      const rest = ids(group).filter((id) => !touched.includes(id));
      onChange(group.key, on ? [...ids(group), ...touched.filter((id) => !ids(group).includes(id))] : rest);
    };
    if (group.items.length === 0) return <p className="text-sm text-muted-foreground">No {group.many}.</p>;
    return (
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <Input
            className="min-w-0 flex-1"
            aria-label={`Filter ${group.many}`}
            placeholder={`Filter ${group.many}…`}
            value={filters[group.key] ?? ""}
            onChange={(e) => setFilters({ ...filters, [group.key]: e.target.value })}
          />
          <div className="flex items-center gap-2 text-sm">
            <Label htmlFor={`all-${group.key}`}>All</Label>
            <span className="text-muted-foreground">
              {shown.filter((item) => isOn(group, item)).length} of {shown.length}
            </span>
            <Switch
              id={`all-${group.key}`}
              aria-label={`All ${group.many}`}
              checked={allOn}
              disabled={disabled || changeable.length === 0}
              onCheckedChange={setAll}
            />
          </div>
        </div>
        {shown.length === 0 ? (
          <p className="text-sm text-muted-foreground">No {group.many} match.</p>
        ) : (
          <div className="divide-y rounded-md border">
            {shown.map((item) => {
              const id = `resource-${group.key}-${item.id}`;
              return (
                <div key={item.id} className="flex items-start gap-3 p-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <Label htmlFor={id} className="break-words">
                        {item.name}
                      </Label>
                      {item.missing && <Badge variant="outline">missing</Badge>}
                    </div>
                    {item.description && <p className="text-sm text-muted-foreground">{item.description}</p>}
                    {item.note && <p className="text-xs text-muted-foreground">{item.note}</p>}
                  </div>
                  <Switch
                    id={id}
                    aria-label={item.name}
                    checked={isOn(group, item)}
                    disabled={disabled || item.locked || item.disabled}
                    onCheckedChange={(on) =>
                      onChange(group.key, on ? [...ids(group), item.id] : ids(group).filter((held) => held !== item.id))
                    }
                  />
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  };

  if ((variant ?? (narrow ? "accordion" : "tabs")) === "accordion")
    return (
      <div className="flex flex-col divide-y rounded-md border">
        {groups.map((group) => (
          <Collapsible key={group.key}>
            <CollapsibleTrigger className="group flex w-full items-center justify-between p-3 text-sm font-medium">
              {count(group)}
              <ChevronDown aria-hidden className="size-4 transition-transform group-data-[state=open]:rotate-180" />
            </CollapsibleTrigger>
            <CollapsibleContent role="region" aria-label={group.label} className="p-3 pt-0">
              {list(group)}
            </CollapsibleContent>
          </Collapsible>
        ))}
      </div>
    );
  const active = groups.some((group) => group.key === tab) ? tab : (groups[0]?.key ?? "");
  return (
    <Tabs value={active} onValueChange={setTab}>
      <TabsList className="h-auto max-w-full flex-wrap justify-start">
        {groups.map((group) => (
          <TabsTrigger key={group.key} value={group.key}>
            {count(group)}
          </TabsTrigger>
        ))}
      </TabsList>
      {groups.map((group) => (
        <TabsContent key={group.key} value={group.key}>
          {list(group)}
        </TabsContent>
      ))}
    </Tabs>
  );
}
