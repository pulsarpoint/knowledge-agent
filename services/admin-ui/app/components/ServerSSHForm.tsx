import { useEffect, useState, type FormEvent } from 'react';
import { ApiError } from '../lib/api';
import { getSSH, saveSSH, testSSH, deleteAgentSSH, type SSHServer, type SSHInput, type SSHTestResult } from '../lib/operations';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '~/components/ui/alert-dialog';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Field, FieldDescription, FieldGroup, FieldLabel } from '~/components/ui/field';
import { Input } from '~/components/ui/input';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '~/components/ui/sheet';
import { Spinner } from '~/components/ui/spinner';
import { Switch } from '~/components/ui/switch';
import { Textarea } from '~/components/ui/textarea';

export default function ServerSSHForm({ id, agentId, agentName, onClose }: { id: string; agentId?: string; agentName?: string; onClose: () => void }) {
  const [server, setServer] = useState<SSHServer | null>(null);
  const [savedServer, setSavedServer] = useState<SSHServer | null>(null);
  const [overrideConnection, setOverrideConnection] = useState(false);
  const [removingOverride, setRemovingOverride] = useState(false);
  const [key, setKey] = useState('');
  const [removeKey, setRemoveKey] = useState(false);
  const [replaceKey, setReplaceKey] = useState(false);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<SSHTestResult | null>(null);
  useEffect(() => { let live = true; (agentId ? getSSH(id, agentId) : getSSH(id)).then(value => { if (live) { setServer(value); setSavedServer(value); setOverrideConnection(value.connection_source === 'agent'); } }).catch(reason => { if (live) setError(reason instanceof ApiError ? reason.message : 'Could not load SSH settings.'); }); return () => { live = false; }; }, [id, agentId]);
  const inherited = Boolean(agentId) && !overrideConnection;
  const hasChanges = dirty || key.trim().length > 0 || removeKey;
  function clearFeedback() { setNotice(null); setError(null); setTestResult(null); }
  function update(value: Partial<SSHServer>) { setServer(current => current && { ...current, ...value }); setDirty(true); clearFeedback(); }
  function input(current: SSHServer): SSHInput {
    return { revision: current.revision, ...(agentId ? { server_revision: current.server_revision } : {}), username: current.username || null, port: current.port, allow_sudo: current.allow_sudo,
      known_hosts: current.known_hosts, ...(removeKey ? { private_key: null } : key.trim() ? { private_key: key } : {}) };
  }
  async function save(event: FormEvent) {
    event.preventDefault(); if (!server || inherited) return;
    setBusy(true); setError(null); setNotice(null); setTestResult(null);
    try {
      const saved = agentId ? await saveSSH(id, input(server), agentId) : await saveSSH(id, input(server));
      setServer(saved); setSavedServer(saved); setOverrideConnection(saved.connection_source === 'agent');
      setKey(''); setRemoveKey(false); setReplaceKey(false); setDirty(false); setNotice('SSH settings saved.');
    } catch (reason) { setError(reason instanceof ApiError ? reason.message : 'Could not save SSH settings.'); }
    finally { setBusy(false); }
  }
  async function test() {
    if (!server) return; setBusy(true); setError(null); setNotice(null); setTestResult(null);
    try { const result = agentId ? await testSSH(id, input(server), agentId) : await testSSH(id, input(server)); setTestResult(result); if (result.ok) setNotice(result.message); else setError(result.message); }
    catch (reason) { setError(reason instanceof ApiError ? reason.message : 'Could not test SSH.'); }
    finally { setBusy(false); }
  }
  return <><Sheet open onOpenChange={open => { if (!open && !busy) onClose(); }}><SheetContent className="overflow-y-auto sm:max-w-xl"><SheetHeader>
    <SheetTitle>SSH connection{server ? ` · ${server.name}` : ''}</SheetTitle><SheetDescription>{agentId ? `SSH configuration for ${agentName ?? 'this agent'}. Use the server configuration or save an independent override.` : 'Configure the default SSH login and permissions for agents using this server.'}</SheetDescription>
  </SheetHeader><form className="flex flex-col gap-6 p-4" onSubmit={event => void save(event)}>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {notice && <Alert><AlertDescription role="status">{notice}</AlertDescription></Alert>}
    {!server && !error && <Spinner />}
    {server && <><p className="break-all font-mono text-sm text-muted-foreground">{server.hostname} · SSH {server.ssh_allowed ? 'allowed' : 'disabled in server inventory'}</p>
      {agentId && <Field orientation="horizontal"><Switch id="ssh-agent-override" checked={overrideConnection} disabled={busy} onCheckedChange={checked => {
        if (!checked && server.connection_source === 'agent') { setRemovingOverride(true); return; }
        setOverrideConnection(checked); setKey(''); setRemoveKey(false); setReplaceKey(false); setDirty(checked); clearFeedback();
        if (!checked && savedServer) setServer(savedServer);
      }} /><div><FieldLabel htmlFor="ssh-agent-override">Override server SSH configuration</FieldLabel><FieldDescription>{inherited ? 'Uses the server’s saved user, key, port, trusted host keys, and sudo permission.' : server.connection_source === 'agent' ? 'This connection belongs to this agent. Changes to server SSH defaults do not replace it.' : 'Starts with the current server settings and saved key. Saving creates an independent override.'}</FieldDescription></div></Field>}
      <fieldset disabled={busy || inherited}><FieldGroup>
        <Field><FieldLabel htmlFor="ssh-user">{agentId ? 'SSH user' : 'Default SSH user'}</FieldLabel><Input id="ssh-user" value={server.username ?? ''} onChange={e => update({ username: e.target.value })} maxLength={65} placeholder="operations" /></Field>
        <Field><FieldLabel htmlFor="ssh-port">SSH port</FieldLabel><Input id="ssh-port" type="number" min={1} max={65535} required value={server.port} onChange={e => update({ port: Number(e.target.value) })} /></Field>
        <Field>
          <FieldLabel htmlFor={!server.has_private_key || replaceKey ? 'ssh-key' : undefined}>SSH private key</FieldLabel>
          {server.has_private_key && <>
            <p className="text-sm font-medium">{removeKey ? 'Private key marked for removal' : 'Private key saved'}</p>
            <FieldDescription>{removeKey ? 'Saving will remove the stored key and disable SSH connections until another key is added.' : 'Saving settings keeps the existing key unless you enter a replacement or choose Remove saved key.'}</FieldDescription>
            {!replaceKey && !removeKey && <Button type="button" variant="outline" className="self-start" onClick={() => setReplaceKey(true)}>Replace key</Button>}
          </>}
          {(!server.has_private_key || replaceKey) && <>
            <Textarea id="ssh-key" autoComplete="off" spellCheck={false} rows={4} maxLength={40000} className="font-mono text-xs" value={key} onChange={e => { setKey(e.target.value); clearFeedback(); }} placeholder={server.has_private_key ? 'Paste a replacement private key, or leave blank to keep the saved key' : 'Paste an OpenSSH or PEM private key'} />
            <FieldDescription>{server.has_private_key ? (key.trim() ? 'Saving will replace the stored private key.' : 'No replacement entered. Saving will keep the existing private key.') : 'No private key saved yet.'} Keys are encrypted in the database and never displayed again. Use a key without a passphrase.</FieldDescription>
            {server.has_private_key && <Button type="button" variant="ghost" className="self-start" onClick={() => { setKey(''); setReplaceKey(false); clearFeedback(); }}>Cancel replacement</Button>}
          </>}
        </Field>
        {server.has_private_key && <Field orientation="horizontal"><FieldLabel htmlFor="ssh-remove-key">Remove saved key</FieldLabel><Switch id="ssh-remove-key" checked={removeKey} onCheckedChange={value => { setRemoveKey(value); setKey(''); setReplaceKey(false); clearFeedback(); }} /></Field>}
        <Field><FieldLabel htmlFor="ssh-known-hosts">Trusted host keys (optional)</FieldLabel><Textarea id="ssh-known-hosts" rows={3} maxLength={40000} spellCheck={false} className="font-mono text-xs" value={server.known_hosts ?? ''} onChange={e => update({ known_hosts: e.target.value })} placeholder={`${server.port === 22 ? server.hostname : `[${server.hostname}]:${server.port}`} ssh-ed25519 AAAA…`} /><FieldDescription>Leave blank to allow tests and agent runs without host verification. Saved known_hosts entries enable verification; a different key will be rejected. Test the connection to obtain the public host key.</FieldDescription></Field>
        <Field orientation="horizontal"><FieldLabel htmlFor="ssh-sudo">Allow sudo to root</FieldLabel><Switch id="ssh-sudo" checked={server.allow_sudo} onCheckedChange={value => update({ allow_sudo: value })} /></Field>
        <p className="text-sm text-muted-foreground">Root commands require passwordless sudo. Normal commands run with privilege escalation disabled. Linux with setpriv and GNU timeout is required.</p>
        <p className="text-sm text-muted-foreground">Server-specific instructions are configured in each agent’s SSH tab.</p>
      </FieldGroup></fieldset>
      <div className="flex flex-wrap gap-2"><Button disabled={busy || inherited}>{busy && <Spinner />}Save settings</Button><Button type="button" variant="outline" disabled={busy || !server.ssh_allowed || !server.username?.trim() || !Number.isInteger(server.port) || server.port < 1 || server.port > 65535 || removeKey || !(key.trim() || server.has_private_key)} onClick={() => void test()}>Test connection</Button><Button type="button" variant="ghost" disabled={busy} onClick={onClose}>Close</Button></div>
      <p className="text-sm text-muted-foreground">Testing uses the current form values. {hasChanges ? 'You have unsaved changes. ' : ''}Save settings to keep changes.</p>
      {testResult?.host_key && <FieldGroup>
        <Field><FieldLabel htmlFor="ssh-observed-key">Server public host key</FieldLabel>
          <Textarea id="ssh-observed-key" readOnly rows={3} value={testResult.host_key.known_hosts_line} />
          <FieldDescription>{testResult.host_key.algorithm} · {testResult.host_key.verified ? 'Verified against trusted hosts' : 'Received with host verification off'}</FieldDescription>
          <p className="break-all font-mono text-xs">{testResult.host_key.fingerprint}</p>
        </Field>
        <Button type="button" variant="outline" disabled={busy || inherited || server.known_hosts.split('\n').some(line => line.trim() === testResult.host_key?.known_hosts_line)} onClick={() => {
          const line = testResult.host_key?.known_hosts_line;
          if (!line) return;
          update({ known_hosts: [server.known_hosts.trim(), line].filter(Boolean).join('\n') });
          setNotice('Public host key added to the field. Save settings to trust it for future connections.');
        }}>Add to trusted hosts</Button>
      </FieldGroup>}
    </>}
  </form></SheetContent></Sheet>
    <AlertDialog open={removingOverride} onOpenChange={open => { if (!busy) setRemovingOverride(open); }}><AlertDialogContent>
      <AlertDialogHeader><AlertDialogTitle>Use server SSH configuration?</AlertDialogTitle><AlertDialogDescription>This removes the SSH connection override for {agentName ?? 'this agent'} on {server?.name}. It will use the server’s current SSH configuration. Server-specific instructions and allowed-server selections are kept.</AlertDialogDescription></AlertDialogHeader>
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      <AlertDialogFooter><AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel><AlertDialogAction disabled={busy} onClick={event => {
        event.preventDefault(); if (!agentId || !server?.server_revision) return;
        setBusy(true); setError(null);
        void deleteAgentSSH(id, agentId, server.revision, server.server_revision).then(saved => {
          setServer(saved); setSavedServer(saved); setOverrideConnection(false); setRemovingOverride(false);
          setKey(''); setRemoveKey(false); setReplaceKey(false); setDirty(false); setTestResult(null); setNotice('Using the server SSH configuration.');
        }).catch(reason => setError(reason instanceof ApiError ? reason.message : 'Could not remove the SSH override.')).finally(() => setBusy(false));
      }}>Use server configuration</AlertDialogAction></AlertDialogFooter>
    </AlertDialogContent></AlertDialog></>;
}
