import { useEffect, useState, type FormEvent } from "react";
import { toast } from "sonner";
import { ApiError } from "~/lib/api";
import { getSSH, saveSSH, testSSH, type SSHServer, type SSHInput, type SSHTestResult } from "./operations";
import { useSheetGuard } from "~/components/LeaveGuard";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Switch } from "~/components/ui/switch";
import { Textarea } from "~/components/ui/textarea";

export default function ServerSSHForm({ id, onClose }: { id: string; onClose: () => void }) {
  const [server, setServer] = useState<SSHServer | null>(null);
  const [key, setKey] = useState("");
  const [removeKey, setRemoveKey] = useState(false);
  const [replaceKey, setReplaceKey] = useState(false);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<SSHTestResult | null>(null);
  useEffect(() => {
    let live = true;
    getSSH(id)
      .then((value) => {
        if (live) setServer(value);
      })
      .catch((reason) => {
        if (live) setError(reason instanceof ApiError ? reason.message : "Could not load SSH settings.");
      });
    return () => {
      live = false;
    };
  }, [id]);
  const hasChanges = dirty || key.trim().length > 0 || removeKey;
  const guard = useSheetGuard(hasChanges && !busy);
  const close = () => guard.close(onClose);
  function clearFeedback() {
    setError(null);
    setTestResult(null);
  }
  function update(value: Partial<SSHServer>) {
    setServer((current) => current && { ...current, ...value });
    setDirty(true);
    clearFeedback();
  }
  function input(current: SSHServer): SSHInput {
    return {
      revision: current.revision,
      username: current.username || null,
      port: current.port,
      allow_sudo: current.allow_sudo,
      known_hosts: current.known_hosts,
      ...(removeKey ? { private_key: null } : key.trim() ? { private_key: key } : {}),
    };
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!server) return;
    setBusy(true);
    setError(null);
    setTestResult(null);
    try {
      const saved = await saveSSH(id, input(server));
      setServer(saved);
      setKey("");
      setRemoveKey(false);
      setReplaceKey(false);
      setDirty(false);
      toast.success("SSH settings saved.");
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Could not save SSH settings.");
    } finally {
      setBusy(false);
    }
  }
  async function test() {
    if (!server) return;
    setBusy(true);
    setError(null);
    setTestResult(null);
    try {
      const result = await testSSH(id, input(server));
      setTestResult(result);
      if (result.ok) toast.success(result.message);
      else setError(result.message);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Could not test SSH.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Sheet
      open
      onOpenChange={(open) => {
        if (!open && !busy) close();
      }}
    >
      {guard.dialog}
      <SheetContent className="overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>SSH connection{server ? ` · ${server.name}` : ""}</SheetTitle>
          <SheetDescription>Configure the SSH login and permissions agents use on this server.</SheetDescription>
        </SheetHeader>
        <form className="flex flex-col gap-6 p-4" onSubmit={(event) => void save(event)}>
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          {!server && !error && <Spinner />}
          {server && (
            <>
              <p className="break-all font-mono text-sm text-muted-foreground">
                {server.hostname} · Agent SSH {server.ssh_allowed ? "enabled" : "disabled in server inventory"}
              </p>
              <fieldset disabled={busy}>
                <FieldGroup>
                  <Field>
                    <FieldLabel htmlFor="ssh-user">Default SSH user</FieldLabel>
                    <Input
                      id="ssh-user"
                      value={server.username ?? ""}
                      onChange={(e) => update({ username: e.target.value })}
                      maxLength={65}
                      placeholder="operations"
                    />
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="ssh-port">SSH port</FieldLabel>
                    <Input
                      id="ssh-port"
                      type="number"
                      min={1}
                      max={65535}
                      required
                      value={server.port}
                      onChange={(e) => update({ port: Number(e.target.value) })}
                    />
                  </Field>
                  <Field>
                    <FieldLabel htmlFor={!server.has_private_key || replaceKey ? "ssh-key" : undefined}>
                      SSH private key
                    </FieldLabel>
                    {server.has_private_key && (
                      <>
                        <p className="text-sm font-medium">
                          {removeKey ? "Private key marked for removal" : "Private key saved"}
                        </p>
                        <FieldDescription>
                          {removeKey
                            ? "Saving will remove the stored key and disable SSH connections until another key is added."
                            : "Saving settings keeps the existing key unless you enter a replacement or choose Remove saved key."}
                        </FieldDescription>
                        {!replaceKey && !removeKey && (
                          <Button
                            type="button"
                            variant="outline"
                            className="self-start"
                            onClick={() => setReplaceKey(true)}
                          >
                            Replace key
                          </Button>
                        )}
                      </>
                    )}
                    {(!server.has_private_key || replaceKey) && (
                      <>
                        <Textarea
                          id="ssh-key"
                          autoComplete="off"
                          spellCheck={false}
                          rows={4}
                          maxLength={40000}
                          className="font-mono text-xs"
                          value={key}
                          onChange={(e) => {
                            setKey(e.target.value);
                            clearFeedback();
                          }}
                          placeholder={
                            server.has_private_key
                              ? "Paste a replacement private key, or leave blank to keep the saved key"
                              : "Paste an OpenSSH or PEM private key"
                          }
                        />
                        <FieldDescription>
                          {server.has_private_key
                            ? key.trim()
                              ? "Saving will replace the stored private key."
                              : "No replacement entered. Saving will keep the existing private key."
                            : "No private key saved yet."}{" "}
                          Keys are encrypted in the database and never displayed again. Use a key without a passphrase.
                        </FieldDescription>
                        {server.has_private_key && (
                          <Button
                            type="button"
                            variant="ghost"
                            className="self-start"
                            onClick={() => {
                              setKey("");
                              setReplaceKey(false);
                              clearFeedback();
                            }}
                          >
                            Cancel replacement
                          </Button>
                        )}
                      </>
                    )}
                  </Field>
                  {server.has_private_key && (
                    <Field orientation="horizontal">
                      <FieldLabel htmlFor="ssh-remove-key">Remove saved key</FieldLabel>
                      <Switch
                        id="ssh-remove-key"
                        checked={removeKey}
                        onCheckedChange={(value) => {
                          setRemoveKey(value);
                          setKey("");
                          setReplaceKey(false);
                          clearFeedback();
                        }}
                      />
                    </Field>
                  )}
                  <Field>
                    <FieldLabel htmlFor="ssh-known-hosts">Trusted host keys (optional)</FieldLabel>
                    <Textarea
                      id="ssh-known-hosts"
                      rows={3}
                      maxLength={40000}
                      spellCheck={false}
                      className="font-mono text-xs"
                      value={server.known_hosts ?? ""}
                      onChange={(e) => update({ known_hosts: e.target.value })}
                      placeholder={`${server.port === 22 ? server.hostname : `[${server.hostname}]:${server.port}`} ssh-ed25519 AAAA…`}
                    />
                    <FieldDescription>
                      Leave blank to allow tests and agent runs without host verification. Saved known_hosts entries
                      enable verification; a different key will be rejected. Test the connection to obtain the public
                      host key.
                    </FieldDescription>
                  </Field>
                  <Field orientation="horizontal">
                    <FieldLabel htmlFor="ssh-sudo">Allow sudo to root</FieldLabel>
                    <Switch
                      id="ssh-sudo"
                      checked={server.allow_sudo}
                      onCheckedChange={(value) => update({ allow_sudo: value })}
                    />
                  </Field>
                  <p className="text-sm text-muted-foreground">
                    Root commands require passwordless sudo. Normal commands run with privilege escalation disabled.
                    Linux with setpriv and GNU timeout is required.
                  </p>
                </FieldGroup>
              </fieldset>
              <div className="flex flex-wrap gap-2">
                <Button disabled={busy}>{busy && <Spinner />}Save</Button>
                <Button
                  type="button"
                  variant="outline"
                  disabled={
                    busy ||
                    !server.ssh_allowed ||
                    !server.username?.trim() ||
                    !Number.isInteger(server.port) ||
                    server.port < 1 ||
                    server.port > 65535 ||
                    removeKey ||
                    !(key.trim() || server.has_private_key)
                  }
                  onClick={() => void test()}
                >
                  Test connection
                </Button>
                <Button type="button" variant="outline" disabled={busy} onClick={close}>
                  Cancel
                </Button>
              </div>
              <p className="text-sm text-muted-foreground">
                Testing uses the current form values. {hasChanges ? "You have unsaved changes. " : ""}Save to keep
                changes.
              </p>
              {testResult?.host_key && (
                <FieldGroup>
                  <Field>
                    <FieldLabel htmlFor="ssh-observed-key">Server public host key</FieldLabel>
                    <Textarea id="ssh-observed-key" readOnly rows={3} value={testResult.host_key.known_hosts_line} />
                    <FieldDescription>
                      {testResult.host_key.algorithm} ·{" "}
                      {testResult.host_key.verified
                        ? "Verified against trusted hosts"
                        : "Received with host verification off"}
                    </FieldDescription>
                    <p className="break-all font-mono text-xs">{testResult.host_key.fingerprint}</p>
                  </Field>
                  <Button
                    type="button"
                    variant="outline"
                    disabled={
                      busy ||
                      server.known_hosts
                        .split("\n")
                        .some((line) => line.trim() === testResult.host_key?.known_hosts_line)
                    }
                    onClick={() => {
                      const line = testResult.host_key?.known_hosts_line;
                      if (!line) return;
                      update({ known_hosts: [server.known_hosts.trim(), line].filter(Boolean).join("\n") });
                      toast.success("Public host key added to the field. Save to trust it for future connections.");
                    }}
                  >
                    Add to trusted hosts
                  </Button>
                </FieldGroup>
              )}
            </>
          )}
        </form>
      </SheetContent>
    </Sheet>
  );
}
