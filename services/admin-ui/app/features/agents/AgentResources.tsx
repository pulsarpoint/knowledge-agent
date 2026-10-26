import { useEffect, useState } from "react";
import { toast } from "sonner";
import { ACCESS_TYPES, getAgentOptions, saveAgent, type AccessType, type AgentOptions, type AgentProfile } from "./api";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "~/components/ui/card";
import ResourceTabs from "./ResourceTabs";

const same = (a: { type: string; id: string }, b: { type: string; id: string }) => a.type === b.type && a.id === b.id;

/** The agent's Resources tab: a tab per type listing every resource of the project with a switch (on: in the agent's
 * access). A reference to a resource that no longer exists shows as missing and is dropped on save. One Save writes the
 * agent with its access object. */
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
  function change(next: AgentProfile["access"]["resources"]) {
    setDraft(next);
    setDirty(true);
  }
  /** One type's switches changed: entries switched off leave the draft, switched on ones join it at the end. */
  function pick(type: AccessType, ids: string[]) {
    const kept = draft.filter((item) => item.type !== type || ids.includes(item.id));
    const added = (options?.[type] ?? [])
      .filter((item) => ids.includes(item.id) && !kept.some((held) => same(held, item)))
      .map(({ id, name, description_short }) => ({ type, id, name, description_short }));
    change([...kept, ...added]);
  }
  // A server or service that a selected environment brings to runs: "via environment Production".
  const environments = (options?.environment ?? []).filter((item) => draft.some((held) => same(held, item)));
  const via = (type: AccessType, id: string) => {
    const names = environments
      .filter((item) =>
        (type === "server" ? item.server_ids : type === "service" ? item.service_ids : [])?.includes(id),
      )
      .map((item) => item.name);
    return names.length ? `via environment ${names.join(", ")}` : null;
  };
  // Each type lists its missing entries first, then what the draft holds that the options do not (yet), then the options.
  const groups = ACCESS_TYPES.map(({ type, label, one, many }) => {
    const offered = options?.[type] ?? [];
    const held = draft.filter((item) => item.type === type && !offered.some((entry) => same(item, entry)));
    return {
      key: type,
      label,
      many,
      items: [...held.filter((item) => item.missing), ...held.filter((item) => !item.missing), ...offered].map(
        (item) =>
          item.missing
            ? {
                id: item.id,
                name: `Missing ${one} (${item.id})`,
                description: "This resource no longer exists. It is dropped when you save.",
                missing: true,
              }
            : { id: item.id, name: item.name, description: item.description_short, note: via(type, item.id) },
      ),
    };
  });
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
      <CardContent className="flex flex-col gap-4 pb-6">
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <ResourceTabs
          groups={groups}
          value={Object.fromEntries(
            ACCESS_TYPES.map(({ type }) => [type, draft.filter((item) => item.type === type).map((item) => item.id)]),
          )}
          onChange={(type, ids) => pick(type as AccessType, ids)}
          disabled={busy}
        />
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
