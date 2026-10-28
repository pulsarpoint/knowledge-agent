import { useState } from "react";
import type { DocumentResources, ResourceType } from "./api";
import { LINK_TYPES } from "./ResourceBadges";
import { ApiError } from "~/lib/api";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";

const LISTS: Record<ResourceType, keyof DocumentResources> = {
  environment: "environments",
  server: "servers",
  service: "services",
  code_project: "code_projects",
  deployment: "deployments",
};

/** Add resource: a right sheet searching `resources` (servers, services, environments, code projects and deployments,
 * as the document page loaded them) by name, grouped by type, without the ones already linked (`linked`:
 * "<type>:<id>"). Picking one runs `add` (a refusal stays shown); the page closes the sheet when it succeeds. */
export default function AddResourceSheet({
  resources,
  linked,
  add,
  onClose,
}: {
  resources: DocumentResources;
  linked: Set<string>;
  add: (type: ResourceType, id: string, name: string) => Promise<unknown>;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const needle = query.trim().toLowerCase();
  const groups = LINK_TYPES.map(({ type, plural }) => ({
    type,
    plural,
    items: (resources[LISTS[type]] ?? [])
      .filter((item) => !linked.has(`${type}:${item.id}`))
      .filter((item) => `${item.name} ${item.hostname ?? ""}`.toLowerCase().includes(needle))
      .sort((a, b) => a.name.localeCompare(b.name)),
  })).filter((group) => group.items.length > 0);

  async function pick(type: ResourceType, id: string, name: string) {
    setSaving(true);
    setError("");
    try {
      await add(type, id, name);
    } catch (e) {
      setError(e instanceof ApiError || e instanceof Error ? e.message : "Could not add it. Try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Sheet
      open
      onOpenChange={(open) => {
        if (!open && !saving) onClose();
      }}
    >
      <SheetContent className="flex w-full flex-col gap-0 overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>Add resource</SheetTitle>
          <SheetDescription>
            Link this document to a server, service, environment, code project or deployment.
          </SheetDescription>
        </SheetHeader>
        <div className="flex flex-col gap-4 p-4">
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <Input
            type="search"
            aria-label="Search resources"
            placeholder="Search resources…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") event.preventDefault(); /* the page's form must not submit */
            }}
          />
          {groups.length === 0 ? (
            <p className="text-sm text-muted-foreground">{needle ? "No matches." : "Nothing to add."}</p>
          ) : (
            groups.map((group) => (
              <div key={group.type} role="group" aria-label={group.plural} className="flex flex-col gap-1">
                <h4 className="text-sm font-medium text-muted-foreground">{group.plural}</h4>
                <ul className="flex flex-col gap-1">
                  {group.items.map((item) => (
                    <li key={item.id}>
                      <Button
                        type="button"
                        variant="outline"
                        disabled={saving}
                        onClick={() => void pick(group.type, item.id, item.name)}
                        className="h-auto w-full justify-start whitespace-normal text-left [overflow-wrap:anywhere]"
                      >
                        {item.name}
                      </Button>
                    </li>
                  ))}
                </ul>
              </div>
            ))
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
