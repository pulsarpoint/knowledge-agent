import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { RefreshCw } from 'lucide-react';
import { type AgentProfile } from '~/lib/profiles';
import { ApiError } from '~/lib/api';
import { getOperationsSettings, saveOperationsSettings, saveOperationsServerConfig, deleteOperationsServerConfig, type OperationsConfiguration, type OperationsSettings, type ServerConfigInput } from './operations';
import AgentTestView from '~/components/AgentTestView';
import ServerSSHForm from './ServerSSHForm';
import MCPSelection from "~/features/llm/MCPSelection";
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '~/components/ui/card';
import { Checkbox } from '~/components/ui/checkbox';
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldSet, FieldLegend } from '~/components/ui/field';
import { Spinner } from '~/components/ui/spinner';
import { Textarea } from '~/components/ui/textarea';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/select';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '~/components/ui/alert-dialog';

export default function OperationsView({ view, active = true, agentId = "operations", available = true, section = "settings", profile, unsavedProfile = false, onDirtyChange, onAgentChanged }: { view: 'settings' | 'test'; active?: boolean; agentId?: string; available?: boolean; section?: string; profile?: AgentProfile; unsavedProfile?: boolean; onDirtyChange?: (dirty: boolean) => void; onAgentChanged?: () => void }) {
  const [data, setData] = useState<OperationsConfiguration | null>(null);
  const [settings, setSettings] = useState<OperationsSettings | null>(null);
  const [scope, setScope] = useState('agent');
  const [override, setOverride] = useState<Omit<ServerConfigInput, 'settings_revision'> | null>(null);
  const [connectionServer, setConnectionServer] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [dirtyScope, setDirtyScope] = useState<string | null>(null);
  const dirty = dirtyScope !== null;
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  async function refresh() {
    const configuration = await getOperationsSettings(agentId);
    setData(configuration); setSettings(configuration.settings); setDirtyScope(null);
    const saved = configuration.configs.find(c => c.server_id === scope);
    setOverride(saved ? { revision: saved.revision, instructions: saved.instructions, skill_ids: saved.skill_ids, mcp_server_ids: saved.mcp_server_ids } : null);
    if (scope !== 'agent' && !configuration.servers.some(server => server.id === scope && server.ssh_allowed)) setScope('agent');
  }
  useEffect(() => { if (!dirty) void refresh().catch(reason => setError(reason instanceof ApiError ? reason.message : 'Could not load operations settings.')); }, [profile?.revision]);
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  function update(value: Partial<OperationsSettings>) { setSettings(current => current && { ...current, ...value }); setDirtyScope('agent'); setNotice(null); }
  function select(field: 'server_ids' | 'skill_ids' | 'mcp_server_ids', id: string, checked: boolean) {
    if (settings) update({ [field]: checked ? [...settings[field], id] : settings[field].filter(value => value !== id) });
  }
  function chooseScope(value: string) {
    setScope(value); setError(null); setNotice(null);
    const saved = data?.configs.find(c => c.server_id === value);
    setOverride(saved ? { revision: saved.revision, instructions: saved.instructions, skill_ids: saved.skill_ids, mcp_server_ids: saved.mcp_server_ids } : null);
  }
  function updateOverride(value: Partial<Omit<ServerConfigInput, 'settings_revision'>>) {
    setOverride(current => current && { ...current, ...value }); setDirtyScope(scope); setNotice(null);
  }
  const sshOn = profile?.ssh_enabled ?? true;
  const serverSection = section === 'ssh' || section === 'settings';
  const agentScope = !serverSection || scope === 'agent';
  const editingOtherScope = dirty && dirtyScope !== (agentScope ? 'agent' : scope);
  const skillsOn = profile?.skills_enabled ?? true;
  const mcpOn = profile?.mcp_enabled ?? true;
  const showSSH = section === 'settings' || section === 'ssh';
  const showInstructions = section === 'settings' || section === 'general' || section === 'ssh' && !agentScope;
  const showSkills = section === 'settings' || section === 'ssh' && !agentScope;
  const showMCP = section === 'settings' || section === 'mcp' || section === 'ssh' && !agentScope;
  const selectedServer = data?.servers.find(server => server.id === scope);
  const editable = agentScope || override !== null;
  const instructions = agentScope ? settings?.instructions ?? '' : override?.instructions ?? settings?.instructions ?? '';
  const selectedSkills = agentScope ? settings?.skill_ids ?? [] : override?.skill_ids ?? settings?.skill_ids ?? [];
  const selectedMCP = agentScope ? settings?.mcp_server_ids ?? [] : override?.mcp_server_ids ?? settings?.mcp_server_ids ?? [];
  async function act(action: () => Promise<void>) {
    setBusy(true); setError(null); setNotice(null);
    try { await action(); } catch (reason) { setError(reason instanceof ApiError ? reason.message : 'The operation failed. Try refreshing.'); }
    finally { setBusy(false); }
  }
  return <div className="flex flex-col gap-6">
    {connectionServer && <ServerSSHForm id={connectionServer} agentId={agentId} agentName={profile?.name} onClose={() => { setConnectionServer(null); void act(refresh); }} />}
    <div hidden={view !== 'settings' || !serverSection}>{data && <Field>
      <FieldLabel htmlFor="operations-scope">SSH configuration</FieldLabel>
      <Select value={scope} disabled={busy || dirty} onValueChange={chooseScope}>
        <SelectTrigger id="operations-scope" className="w-full"><SelectValue /></SelectTrigger>
        <SelectContent><SelectGroup><SelectItem value="agent">Allowed servers</SelectItem>
          {data.servers.filter(server => server.ssh_allowed).map(server => <SelectItem key={server.id} value={server.id}>{server.name}</SelectItem>)}
        </SelectGroup></SelectContent>
      </Select>
      <FieldDescription>{dirty ? 'Save or discard your changes before selecting another configuration.' : 'Choose allowed servers or configure a server’s SSH connection, instructions, and tools for this agent.'}</FieldDescription>
    </Field>}</div>
    <div hidden={view !== 'settings'} className="flex flex-wrap justify-end gap-3">{view === 'settings' && serverSection && <Button variant="outline" className="mr-auto" asChild><Link to="/servers">Server SSH settings</Link></Button>}<Button variant="outline" disabled={busy || dirty} onClick={() => void act(refresh)}><RefreshCw />Refresh</Button></div>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {view === 'settings' && serverSection && !sshOn && <Alert><AlertDescription>SSH access is off for this agent. You can configure servers here; enable and save SSH access before the agent can connect.</AlertDescription></Alert>}
    {editingOtherScope && <Alert><AlertDescription>There are unsaved changes in another configuration tab. Save or discard them before editing this configuration.</AlertDescription></Alert>}
    {notice && <Alert><AlertDescription role="status">{notice}</AlertDescription></Alert>}
    {!data && !error && <Spinner />}
    <div hidden={view !== 'settings'}>{data && settings && <Card>
      <CardHeader><CardTitle>{!agentScope ? `${selectedServer?.name} configuration` : section === 'ssh' ? 'Allowed SSH servers' : section === 'mcp' ? 'MCP connections' : section === 'general' ? 'Instructions' : 'Instructions and skills'}</CardTitle><CardDescription>
        {!agentScope ? (override ? 'Instructions and tools used by this agent on this server. Fields can use the agent’s settings or a server-specific value.' : 'No server-specific configuration saved. This agent uses its own instructions, skills, and MCP selections on this server.') : section === 'ssh' ? 'Choose the servers this agent may access.' : section === 'mcp' ? 'Select MCP connections for this agent.' : 'Additional instructions for this agent. Its system prompt is managed in Prompts.'}
      </CardDescription></CardHeader>
      <CardContent><FieldGroup>
        {agentScope ? <div hidden={!showSSH}>
          <FieldSet><FieldLegend variant="label">Allowed servers</FieldLegend><FieldDescription>Select one or more SSH-enabled servers. Use Configure to set instructions for this agent and optionally override the server’s SSH connection.</FieldDescription>
            <div className="divide-y rounded-md border">{data.servers.length === 0 && <p className="p-4 text-sm text-muted-foreground">Register a server and enable SSH to get started.</p>}
              {data.servers.map(server => {
                const ready = server.ssh_allowed && server.enabled;
                return <div key={server.id} className="flex flex-wrap items-start gap-3 p-4"><label className="flex min-w-0 flex-1 items-start gap-3"><Checkbox aria-label={`Allow ${server.name}`} checked={settings.server_ids.includes(server.id)} disabled={busy || editingOtherScope || !server.ssh_allowed && !settings.server_ids.includes(server.id)} onCheckedChange={checked => select('server_ids', server.id, checked === true)} /><span className="min-w-0 flex-1"><span className="font-medium">{server.name}</span><span className="mt-1 block break-all text-xs text-muted-foreground">{server.username || 'No login'}@{server.hostname}:{server.port}</span><span className="block text-xs text-muted-foreground">{server.connection_source === 'agent' ? 'Agent SSH override' : 'Server SSH configuration'} · {data.configs.some(config => config.server_id === server.id && config.instructions !== null) ? 'Server-specific instructions' : 'Uses agent instructions'}</span>{!ready && <span className="block text-xs text-muted-foreground">{!server.ssh_allowed ? 'SSH is disabled' : 'SSH login or private key missing'}</span>}</span></label><Badge variant="outline">{server.allow_sudo ? 'Root allowed' : 'No sudo'}</Badge><Button variant="outline" size="sm" disabled={busy || dirty || !server.ssh_allowed} onClick={() => chooseScope(server.id)}>Configure<span className="sr-only"> {server.name}</span></Button></div>;
              })}
            </div>
            {dirty && <FieldDescription>Save or discard the selection before configuring an individual server.</FieldDescription>}
          </FieldSet>
        </div> : <>
          <Field><FieldLabel>SSH connection</FieldLabel><FieldDescription>{selectedServer?.connection_source === 'agent' ? 'This agent uses its own SSH connection override.' : 'This agent uses the server’s SSH configuration.'}</FieldDescription><Button variant="outline" className="self-start" disabled={busy || dirty} onClick={() => setConnectionServer(scope)}>Configure SSH connection</Button></Field>
          <p className="text-sm text-muted-foreground">{selectedServer?.username || 'No login'}@{selectedServer?.hostname}:{selectedServer?.port} · {selectedServer?.allow_sudo ? 'Root allowed' : 'No sudo'}</p>
          {!settings.server_ids.includes(scope) && <Alert><AlertDescription>This server is not in this agent’s allowed-server list. Saving instructions does not grant SSH access.</AlertDescription></Alert>}
          {override && showInstructions && <Field orientation="horizontal"><Checkbox id="inherit-instructions" checked={override.instructions === null} disabled={busy || editingOtherScope} onCheckedChange={checked => updateOverride({ instructions: checked === true ? null : settings.instructions })} /><FieldLabel htmlFor="inherit-instructions">Use agent instructions</FieldLabel></Field>}
        </>}
        <div hidden={!showInstructions}><FieldGroup>
        <Field><FieldLabel htmlFor="operations-instructions">{agentScope ? 'Instructions' : 'Server instructions'}</FieldLabel>
          <Textarea id="operations-instructions" rows={7} maxLength={40000} disabled={busy || editingOtherScope || !editable || !agentScope && override?.instructions === null} value={instructions} onChange={e => agentScope ? update({ instructions: e.target.value }) : updateOverride({ instructions: e.target.value })} placeholder="Task guidance, required checks, and how results should be verified…" />
          <FieldDescription>{agentScope ? 'These instructions belong to this agent. Its active system prompt always applies.' : override?.instructions == null ? 'Uses this agent’s configuration.' : 'Used by this agent on this server. An empty value means no additional instructions.'}</FieldDescription>
        </Field>
        </FieldGroup></div>
        <div hidden={!showSkills}><FieldGroup>
        {!agentScope && override && <Field orientation="horizontal"><Checkbox id="inherit-skills" checked={override.skill_ids === null} disabled={busy || editingOtherScope || !skillsOn} onCheckedChange={checked => updateOverride({ skill_ids: checked === true ? null : [...settings.skill_ids] })} /><FieldLabel htmlFor="inherit-skills">Use agent skills</FieldLabel></Field>}
        <Field><FieldLabel>Skills</FieldLabel><FieldDescription>{agentScope ? 'Selected skills are included in this agent’s context.' : override?.skill_ids == null ? 'Uses this agent’s configuration.' : 'Only these skills apply to this server. An empty selection means no skills.'} Manage runbooks in the <Link to="/skills" className="underline underline-offset-4">Skills library</Link>.</FieldDescription>
          <div className="divide-y rounded-md border">{data.skills.length === 0 && <p className="p-4 text-sm text-muted-foreground">No skills have been added yet.</p>}{data.skills.map(skill => <label key={skill.id} className="flex items-start gap-3 p-4"><Checkbox aria-label={`Use ${skill.name}`} disabled={busy || editingOtherScope || !skillsOn || !editable || !agentScope && override?.skill_ids === null || !skill.enabled && !selectedSkills.includes(skill.id)} checked={selectedSkills.includes(skill.id)} onCheckedChange={checked => agentScope ? select('skill_ids', skill.id, checked === true) : updateOverride({ skill_ids: checked === true ? [...selectedSkills, skill.id] : selectedSkills.filter(id => id !== skill.id) })} /><span><span className="font-medium">{skill.name}{!skill.enabled && ' (disabled)'}</span><span className="mt-1 block text-sm text-muted-foreground">{skill.description}</span></span></label>)}</div>
        </Field>
        </FieldGroup></div>
        <div hidden={!showMCP}><FieldGroup>
        {!agentScope && override && <Field orientation="horizontal"><Checkbox id="inherit-mcp" checked={override.mcp_server_ids === null} disabled={busy || editingOtherScope || !mcpOn} onCheckedChange={checked => updateOverride({ mcp_server_ids: checked === true ? null : [...settings.mcp_server_ids] })} /><FieldLabel htmlFor="inherit-mcp">Use agent MCP selection</FieldLabel></Field>}
        <MCPSelection servers={data.mcp_servers} selected={selectedMCP} disabled={busy || editingOtherScope || !mcpOn || !editable || !agentScope && override?.mcp_server_ids === null} onChange={ids => agentScope ? update({ mcp_server_ids: ids }) : updateOverride({ mcp_server_ids: ids })} />
        <FieldDescription>{!agentScope && 'For tasks spanning several servers, the agent receives their combined MCP tools and the selection for each server. '}Permissions on external systems are controlled by each MCP connection.</FieldDescription>
        </FieldGroup></div>
      </FieldGroup></CardContent>
      <CardFooter className="flex flex-wrap gap-3">
        {editable ? <Button disabled={busy || editingOtherScope || !dirty || section === "mcp" && !mcpOn} onClick={() => void act(async () => {
          if (agentScope) await saveOperationsSettings(settings, agentId);
          else if (override) await saveOperationsServerConfig(scope, { ...override, settings_revision: settings.revision }, agentId);
          await refresh(); setNotice(agentScope ? 'Agent configuration saved.' : 'Server configuration saved. New runs use its resolved instructions and skills.');
        })}>{busy && <Spinner />}{!agentScope ? 'Save server configuration' : section === 'general' ? 'Save instructions' : section === 'mcp' ? 'Save MCP selection' : section === 'ssh' ? 'Save allowed servers' : 'Save agent configuration'}</Button> : <Button disabled={busy || editingOtherScope} onClick={() => { setOverride({ revision: null, instructions: null, skill_ids: null, mcp_server_ids: null }); setDirtyScope(scope); }}>Create server configuration</Button>}
        {dirty && <Button variant="outline" disabled={busy} onClick={() => { setSettings(data.settings); chooseScope(scope); setDirtyScope(null); }}>Discard changes</Button>}
        {!agentScope && override?.revision != null && <Button variant="destructive" disabled={busy || dirty} onClick={() => setRemoving(true)}>Remove server configuration</Button>}
      </CardFooter>
    </Card>}</div>
    <AlertDialog open={removing && active && view === 'settings' && !agentScope} onOpenChange={open => { if (!busy) setRemoving(open); }}><AlertDialogContent>
      <AlertDialogHeader><AlertDialogTitle>Remove server configuration?</AlertDialogTitle><AlertDialogDescription>The saved instruction, skill, and MCP server overrides for {selectedServer?.name} will be removed. This agent will use its own instructions, skills, and MCP selections on this server. SSH credentials and server access permissions will stay unchanged.</AlertDialogDescription></AlertDialogHeader>
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      <AlertDialogFooter><AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel><AlertDialogAction disabled={busy} onClick={event => { event.preventDefault(); if (override?.revision != null && settings) void act(async () => { await deleteOperationsServerConfig(scope, override.revision!, settings.revision, agentId); setRemoving(false); await refresh(); setNotice('Server configuration removed. The agent’s configuration now applies.'); }); }}>Remove configuration</AlertDialogAction></AlertDialogFooter>
    </AlertDialogContent></AlertDialog>
    <div hidden={view !== 'test'}><AgentTestView agentId={agentId} agentName={profile?.name ?? 'Agent'} available={available} ready={data !== null && settings !== null} dirty={dirty || unsavedProfile} active={active && view === 'test'} revision={profile?.revision} onAgentChanged={onAgentChanged} /></div>
  </div>;
}
