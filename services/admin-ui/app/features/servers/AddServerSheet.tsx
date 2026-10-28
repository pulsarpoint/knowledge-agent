import { useEffect, useState, type FormEvent } from "react";
import { ApiError } from "~/lib/api";
import { getServers, saveServer, type ServerAsset } from "./api";
import { addServerToEnvironment } from "./environments";
import { DraftsUsed } from "./ConfirmDraftSheet";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldGroup, FieldLabel, FieldSet } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Textarea } from "~/components/ui/textarea";

const NEW = "new";

/** Puts a server in the environment: one that is not in it yet, or a new one (created first). `uses`: the names of the
 * drafts the environment brings (itself when it is a draft); saving confirms them and a chosen draft server. */
export default function AddServerSheet({
  environmentId,
  memberIds,
  uses = [],
  onAdded,
  onClose,
}: {
  environmentId: string;
  memberIds: string[];
  uses?: string[];
  onAdded: (serverId: string) => void;
  onClose: () => void;
}) {
  const [servers, setServers] = useState<ServerAsset[] | null>(null);
  const [choice, setChoice] = useState("");
  const [name, setName] = useState("");
  const [hostname, setHostname] = useState("");
  const [description, setDescription] = useState("");
  // A new server created by an earlier, partly failed save: saving again only adds it.
  const [created, setCreated] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getServers(controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setServers(value.servers);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e instanceof ApiError ? e.message : "Could not load the servers.");
      });
    return () => controller.abort();
  }, []);

  const candidates = (servers ?? [])
    .filter((server) => !memberIds.includes(server.id))
    .sort((a, b) => a.name.localeCompare(b.name));

  const chosen = candidates.find((server) => server.id === choice);
  const drafts = [...uses, ...(chosen?.status === "draft" ? [chosen.name] : [])];

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      let serverId = choice === NEW ? created : choice;
      if (!serverId) {
        serverId = (await saveServer({ name, hostname, description, tags: [], ssh_allowed: false })).id;
        setCreated(serverId);
      }
      await addServerToEnvironment(environmentId, serverId);
      onAdded(serverId);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not add the server. Try again.");
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
      <SheetContent className="overflow-y-auto">
        <SheetHeader>
          <SheetTitle>Add server</SheetTitle>
          <SheetDescription>Pick a server that is not in this environment yet, or create a new one.</SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-col gap-6 p-4">
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <FieldSet disabled={saving || !servers}>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="add-server-choice">Server</FieldLabel>
                <Select value={choice} onValueChange={setChoice}>
                  <SelectTrigger id="add-server-choice" className="w-full">
                    <SelectValue placeholder="Choose a server" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {candidates.map((server) => (
                        <SelectItem key={server.id} value={server.id}>
                          {server.name} · {server.hostname}
                        </SelectItem>
                      ))}
                      <SelectItem value={NEW}>A new server</SelectItem>
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </Field>
              {choice === NEW && (
                <>
                  <Field>
                    <FieldLabel htmlFor="add-server-name">Name</FieldLabel>
                    <Input
                      id="add-server-name"
                      required
                      maxLength={200}
                      value={name}
                      onChange={(event) => setName(event.target.value)}
                    />
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="add-server-hostname">Hostname</FieldLabel>
                    <Input
                      id="add-server-hostname"
                      required
                      maxLength={253}
                      value={hostname}
                      onChange={(event) => setHostname(event.target.value)}
                      placeholder="app-01.internal"
                    />
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="add-server-description">Description</FieldLabel>
                    <Textarea
                      id="add-server-description"
                      maxLength={4000}
                      rows={3}
                      value={description}
                      onChange={(event) => setDescription(event.target.value)}
                    />
                  </Field>
                </>
              )}
            </FieldGroup>
          </FieldSet>
          <DraftsUsed names={drafts} />
          <div className="flex gap-2">
            <Button disabled={saving || !choice}>
              {saving && <Spinner data-icon="inline-start" />}
              {choice === NEW ? "Create server" : "Add server"}
            </Button>
            <Button type="button" variant="outline" disabled={saving} onClick={onClose}>
              Cancel
            </Button>
          </div>
        </form>
      </SheetContent>
    </Sheet>
  );
}
