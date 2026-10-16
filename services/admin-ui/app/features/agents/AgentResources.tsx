import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  ACCESS_TYPES,
  getAgentOptions,
  saveAgent,
  type AccessEntry,
  type AgentOptions,
  type AgentProfile,
} from "./api";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "~/components/ui/card";
import { FieldDescription, FieldGroup, FieldLegend, FieldSet } from "~/components/ui/field";
import { Switch } from "~/components/ui/switch";

const same = (a: AccessEntry, b: { type: string; id: string }) => a.type === b.type && a.id === b.id;

/** The agent's Resources tab: what its access references, grouped by type. Add from the project's resources (or all of a
 * type), remove, make a document editable; a reference to a resource that no longer exists shows as missing and is
 * dropped on save. One Save writes the agent with its access object. */
export default function AgentResources({
  profile,
  onSaved,
  onDirtyChange,
}: {
  profile: AgentProfile;
  onSaved: (profile: AgentProfile) => void;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const [draft, setDraft] = useState(profile.access.resources);
  const [options, setOptions] = useState<AgentOptions>();
  const [adding, setAdding] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);
  useEffect(() => {
    let live = true;
    getAgentOptions(profile.id)
      .then((value) => {
        if (live) setOptions(value);
      })
      .catch((e) => {
        if (live) setError(e instanceof Error ? e.message : "Could not load the project's resources.");
      });
    return () => {
      live = false;
    };
  }, [profile.id]);
  // Saved elsewhere (General): keep the edits here unless there are none.
  useEffect(() => {
    if (!dirty) setDraft(profile.access.resources);
  }, [profile]); // eslint-disable-line react-hooks/exhaustive-deps
  function change(next: AccessEntry[]) {
    setDraft(next);
    setDirty(true);
  }
  const add = (items: AccessEntry[]) =>
    change([
      ...draft,
      ...items
        .filter((item) => !draft.some((held) => same(held, item)))
        .map((item) => (item.type === "document" ? { ...item, editable: false } : item)),
    ]);
  async function save() {
    setBusy(true);
    setError("");
    try {
      const saved = await saveAgent({ ...profile, access: { ...profile.access, resources: draft } });
      setDraft(saved.access.resources);
      setDirty(false);
      onSaved(saved);
      toast.success("Resources saved.", { id: "agent-resources-saved" });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the resources.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>Resources</CardTitle>
        <CardDescription>
          What this agent may use. Its runs get these resources, or a subset picked for one run.
        </CardDescription>
      </CardHeader>
      <CardContent className="pb-6">
        <FieldGroup>
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          {ACCESS_TYPES.map(({ type, label, one, many }) => {
            const held = draft.filter((item) => item.type === type);
            const offered = (options?.[type] ?? []).filter((item) => !held.some((entry) => same(entry, item)));
            return (
              <FieldSet key={type}>
                <FieldLegend>{label}</FieldLegend>
                {held.length === 0 && <FieldDescription>No {many}.</FieldDescription>}
                {held.length > 0 && (
                  <div className="divide-y rounded-md border">
                    {held.map((item) => {
                      const name = item.missing ? `Missing ${one} (${item.id})` : item.name;
                      return (
                        <div key={item.id} className="flex flex-wrap items-start gap-3 p-3">
                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
                              {name}
                              {item.missing && <Badge variant="outline">missing</Badge>}
                            </div>
                            <p className="text-sm text-muted-foreground">
                              {item.missing
                                ? "This resource no longer exists. It is dropped when you save."
                                : item.description_short}
                            </p>
                          </div>
                          {type === "document" && !item.missing && (
                            <label className="flex items-center gap-2 text-sm">
                              <Switch
                                aria-label={`Editable ${name}`}
                                disabled={busy}
                                checked={Boolean(item.editable)}
                                onCheckedChange={(editable) =>
                                  change(draft.map((entry) => (same(entry, item) ? { ...entry, editable } : entry)))
                                }
                              />
                              Editable
                            </label>
                          )}
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            disabled={busy}
                            aria-label={`Remove ${name}`}
                            onClick={() => change(draft.filter((entry) => !same(entry, item)))}
                          >
                            Remove
                          </Button>
                        </div>
                      );
                    })}
                  </div>
                )}
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={busy || !options}
                    aria-label={`Add ${many}`}
                    aria-expanded={adding === type}
                    onClick={() => setAdding(adding === type ? null : type)}
                  >
                    Add
                  </Button>
                </div>
                {adding === type && (
                  <div className="flex flex-col gap-2 rounded-md border p-3">
                    {offered.length === 0 ? (
                      <FieldDescription>Every {one} of the project is already here.</FieldDescription>
                    ) : (
                      <>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="self-start"
                          disabled={busy}
                          aria-label={`Select all ${many}`}
                          onClick={() => add(offered)}
                        >
                          Select all
                        </Button>
                        {offered.map((item) => (
                          <div key={item.id} className="flex items-start gap-3">
                            <div className="min-w-0 flex-1">
                              <p className="text-sm font-medium">{item.name}</p>
                              <p className="text-sm text-muted-foreground">{item.description_short}</p>
                            </div>
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              disabled={busy}
                              aria-label={`Add ${item.name}`}
                              onClick={() => add([item])}
                            >
                              Add
                            </Button>
                          </div>
                        ))}
                      </>
                    )}
                  </div>
                )}
              </FieldSet>
            );
          })}
        </FieldGroup>
      </CardContent>
      <CardFooter className="flex-wrap gap-2">
        <Button disabled={busy || !dirty} onClick={() => void save()}>
          Save resources
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={busy || !dirty}
          onClick={() => {
            setDraft(profile.access.resources);
            setDirty(false);
            setError("");
          }}
        >
          Discard changes
        </Button>
      </CardFooter>
    </Card>
  );
}
