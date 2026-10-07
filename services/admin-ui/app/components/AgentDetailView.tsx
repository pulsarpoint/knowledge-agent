import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { ArrowLeft } from 'lucide-react';
import { getAgent, type AgentProfile } from '../lib/profiles';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Skeleton } from '~/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '~/components/ui/tabs';
import ClaudeCodeAgentForm from './ClaudeCodeAgentForm';
import { CLAUDE_CODE_SDK } from '~/lib/claudeCode';
import AgentModelSelection from './AgentModelSelection';
import AgentProfileSettings from './AgentProfileSettings';
import AgentInstructions from './AgentInstructions';
import PromptsView from './PromptsView';
import OperationsView from './OperationsView';
import CoordinatorSettings from './CoordinatorSettings';
import AgentVersionsView from './AgentVersionsView';

export default function AgentDetailView({ agentId }: { agentId: string }) {
  const [params, setParams] = useSearchParams();
  const [profile, setProfile] = useState<AgentProfile>();
  const sections = profile?.is_coordinator ? ['general','prompts'] : ['general','skills','documents','git','ssh','mcp','services','prompts','versions','test'];
  const selected = params.get('view') ?? '';
  const view = sections.includes(selected) ? selected : 'general';
  const [error, setError] = useState('');
  const [profileDirty, setProfileDirty] = useState(false);
  const [configurationDirty, setConfigurationDirty] = useState(false);
  const [reload, setReload] = useState(0);
  const [promptRevision, setPromptRevision] = useState(0);
  useEffect(() => { let live = true; getAgent(agentId).then(value => { if (live) { setProfile(value); setError(''); } }).catch(e => { if (live) setError(e.message); }); return () => { live = false; }; }, [agentId, reload]);
  return <section className="flex flex-col gap-6" aria-label="Agent details">
    <Button asChild variant="link" className="self-start px-0"><Link to="/agents"><ArrowLeft />All agents</Link></Button>
    {error && <Alert variant="destructive"><AlertDescription>{error}<Button variant="outline" onClick={() => setReload(n => n + 1)}>Reload agent</Button></AlertDescription></Alert>}
    {!profile ? <Skeleton className="h-64 w-full" /> : profile.runtime === 'claude_code' ? <>
  <div className="flex flex-wrap items-center gap-3"><h2 className="text-xl font-semibold">{profile.name}</h2><Badge variant="secondary">{CLAUDE_CODE_SDK}</Badge></div>
  <ClaudeCodeAgentForm key={profile.revision} agent={profile} onSaved={saved => setProfile({ ...profile, ...saved })} />
  <p className="text-sm text-muted-foreground">Start a session in <Link className="underline" to="/documentation/generations/new?scope=agent">Docs → Generate documents → New documentation → With an agent</Link>.</p>
</> : <>
      <div className="flex flex-col gap-2"><div className="flex flex-wrap items-center gap-3"><h2 className="text-xl font-semibold">{profile.name}</h2>{profile.is_coordinator && <Badge>Coordinator</Badge>}{!profile.enabled && <Badge variant="outline">Disabled</Badge>}</div>{profile.description && view !== 'test' && <p className="text-sm text-muted-foreground">{profile.description}</p>}{profile.is_coordinator && <p className="text-sm text-muted-foreground">This agent coordinates board tasks and cannot be removed. <Link to="/agent-board" className="underline">Open multi-agent board</Link></p>}</div>
      <Tabs value={view} onValueChange={next => setParams(current => { current.set('view', next); return current; })} className="flex flex-col gap-6">
        <div className="overflow-x-auto"><TabsList aria-label="Agent sections" className="w-max">{sections.map(section => <TabsTrigger key={section} value={section}>{({ git: 'Code projects', ssh: 'SSH', mcp: 'MCP' } as Record<string,string>)[section] ?? section[0].toUpperCase()+section.slice(1)}</TabsTrigger>)}</TabsList></div>
        <TabsContent value={view} forceMount className="flex flex-col gap-6">
          {!profile.is_coordinator && profile.active_config_version_id && !['test','versions'].includes(view) && <Alert><AlertDescription>These tabs edit the working configuration. Save a draft in <Link className="underline" to="?view=versions">Versions</Link>, then activate it to use these changes in new runs.</AlertDescription></Alert>}
          {view === 'versions' && <AgentVersionsView agentId={agentId} dirty={profileDirty || configurationDirty} onChanged={() => setReload(n => n + 1)} />}
          <div hidden={['test','prompts','versions'].includes(view)}><AgentProfileSettings key={reload} profile={profile} view={view} onSaved={setProfile} onDirtyChange={setProfileDirty} configurationDirty={configurationDirty} /></div>
          <div hidden={view !== 'general'}><AgentModelSelection profile={profile} onRefresh={() => setReload(n => n + 1)} onSaved={saved => { if (saved) setProfile(saved); }} /></div>
          {profile.is_coordinator ? <div hidden={view !== 'general'}><CoordinatorSettings /></div> : <div hidden={!['general','ssh','mcp','test'].includes(view)}><OperationsView view={view === 'test' ? 'test' : 'settings'} section={view} agentId={agentId} profile={profile} unsavedProfile={profileDirty} onDirtyChange={setConfigurationDirty} available={profile.enabled} active={['general','ssh','mcp','test'].includes(view)} /></div>}
          <div hidden={view !== 'prompts'}><PromptsView agentId={agentId} agentLabel={profile.name} active={view === 'prompts'} onActivated={() => setPromptRevision(n => n + 1)} /></div>
          {view === 'general' && <AgentInstructions agentId={agentId} agentLabel={profile.name} revision={promptRevision} />}
        </TabsContent>
      </Tabs>
    </>}
  </section>;
}
