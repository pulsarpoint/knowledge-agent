import { useState, type FormEvent } from "react";
import { ApiError } from "~/lib/api";
import { saveServer, type ServerAsset } from "./api";
import { useSheetGuard } from "~/components/LeaveGuard";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldContent, FieldDescription, FieldGroup, FieldLabel, FieldSet } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Switch } from "~/components/ui/switch";
import { Textarea } from "~/components/ui/textarea";

export default function ServerForm({
  server = null,
  onSaved,
  onClose,
}: {
  server?: ServerAsset | null;
  onSaved: (server: ServerAsset) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(server?.name ?? "");
  const [hostname, setHostname] = useState(server?.hostname ?? "");
  const [description, setDescription] = useState(server?.description ?? "");
  const [tags, setTags] = useState(server?.tags.join(", ") ?? "");
  const [sshAllowed, setSshAllowed] = useState(server?.ssh_allowed ?? false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const values = JSON.stringify([name, hostname, description, tags, sshAllowed]);
  const [initial] = useState(values);
  const guard = useSheetGuard(values !== initial);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      onSaved(
        await saveServer(
          {
            name,
            hostname,
            description,
            ssh_allowed: sshAllowed,
            tags: tags
              .split(",")
              .map((tag) => tag.trim())
              .filter(Boolean),
            ...(server ? { revision: server.revision } : {}),
          },
          server?.id,
        ),
      );
    } catch (error) {
      setError(error instanceof ApiError ? error.message : "Could not save this server. Try again.");
    } finally {
      setSaving(false);
    }
  }
  return (
    <Sheet
      open
      onOpenChange={(open) => {
        if (!open && !saving) guard.close(onClose);
      }}
    >
      {guard.dialog}
      <SheetContent className="overflow-y-auto">
        <SheetHeader>
          <SheetTitle>{server ? "Edit server" : "New server"}</SheetTitle>
          <SheetDescription>
            {server
              ? "Update this host's inventory. Linked service deployments use these details automatically."
              : "Add a reusable host to this application's inventory, then link it to service deployments."}
          </SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-col gap-6 p-4">
          {error && (
            <Alert variant="destructive">
              <AlertTitle>Server could not be saved</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <FieldSet disabled={saving}>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="server-name">Server name</FieldLabel>
                <Input
                  id="server-name"
                  required
                  maxLength={200}
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="Production app server"
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="server-hostname">IP address or hostname</FieldLabel>
                <Input
                  id="server-hostname"
                  required
                  maxLength={253}
                  value={hostname}
                  onChange={(event) => setHostname(event.target.value)}
                  placeholder="app-01.internal"
                />
                <FieldDescription>IPv4, IPv6, or a hostname. Enter the address without a URL or port.</FieldDescription>
              </Field>
              <Field>
                <FieldLabel htmlFor="server-description">Description (optional)</FieldLabel>
                <Textarea
                  id="server-description"
                  maxLength={4000}
                  rows={4}
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  placeholder="What this server hosts, its location, and useful operational context."
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="server-tags">Tags (optional)</FieldLabel>
                <Input
                  id="server-tags"
                  maxLength={2112}
                  value={tags}
                  onChange={(event) => setTags(event.target.value)}
                  placeholder="production, payments, eu-west"
                />
                <FieldDescription>
                  Separate tags with commas. Up to 32 tags, 64 characters each. Tags are saved in lowercase.
                </FieldDescription>
              </Field>
              <Field orientation="horizontal" data-disabled={saving}>
                <FieldContent>
                  <FieldLabel htmlFor="server-ssh-allowed">Agent SSH</FieldLabel>
                  <FieldDescription id="server-ssh-description">
                    Allow agents to use SSH for this server. Connection credentials are configured separately.
                  </FieldDescription>
                </FieldContent>
                <Switch
                  id="server-ssh-allowed"
                  checked={sshAllowed}
                  onCheckedChange={setSshAllowed}
                  disabled={saving}
                  aria-describedby="server-ssh-description"
                />
              </Field>
            </FieldGroup>
          </FieldSet>
          {!server && (
            <p className="text-sm text-muted-foreground">
              Registration saves the server immediately. SSH access is unconfigured; service settings are saved
              separately.
            </p>
          )}
          <div className="flex gap-2">
            <Button disabled={saving}>
              {saving && <Spinner data-icon="inline-start" />}
              {server ? "Save" : "Create server"}
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
