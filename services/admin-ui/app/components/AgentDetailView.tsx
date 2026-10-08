import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { ArrowLeft } from 'lucide-react';
import { PER_RUN_NOTES, getAgent, type AgentProfile } from '../lib/profiles';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Skeleton } from '~/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '~/components/ui/tabs';
import { claudeCodeNotes } from '~/lib/claudeCode';
import { Card, CardDescription, CardHeader, CardTitle } from '~/components/ui/card';
import AgentModelSelection from './AgentModelSelection';
import AgentProfileSettings from './AgentProfileSettings';
import OperationsView from './OperationsView';
import AgentPromptCard from './AgentPromptCard';
import AgentToolsView from './AgentToolsView';
import AgentActions from './AgentActions';
import { TOOLS_TAB_NOTE } from '../lib/tools';

export default function AgentDetailView({ agentId }: { agentId: string }) {
  const [params, setParams] = useSearchParams();
  const [profile, setProfile] = useState<AgentProfile>();
  const sections = ['general','tools','skills','documents','git','ssh','mcp','services','test'];
  const selected = params.get('view') ?? '';
  const view = sections.includes(selected) ? selected : 'general';
  const claudeCode = profile?.runtime === 'claude_code';
  const nativeTest = view === 'test' && !claudeCode;
  const [error, setError] = useState('');
  const [profileDirty, setProfileDirty] = useState(false);
  const [configurationDirty, setConfigurationDirty] = useState(false);
  const [toolsDirty, setToolsDirty] = useState(false);
  const [toolsOpened, setToolsOpened] = useState(false);
  useEffect(() => { if (view === 'tools') setToolsOpened(true); }, [view]);
  const [reload, setReload] = useState(0);
  const [promptDirty, setPromptDirty] = useState(false);
  useEffect(() => { let live = true; getAgent(agentId).then(value => { if (live) { setProfile(value); setError(''); } }).catch(e => { if (live) setError(e.message); }); return () => { live = false; }; }, [agentId, reload]);
  return <section className="flex flex-col gap-6" aria-label="Agent details">
    <Button asChild variant="link" className="self-start px-0"><Link to="/agents"><ArrowLeft />All agents</Link></Button>
    {error && <Alert variant="destructive"><AlertDescription>{error}<Button variant="outline" onClick={() => setReload(n => n + 1)}>Reload agent</Button></AlertDescription></Alert>}
    {!profile ? <Skeleton className="h-64 w-full" /> : <>
      <div className="flex flex-col gap-2"><div className="flex flex-wrap items-center justify-between gap-3"><div className="flex flex-wrap items-center gap-3"><h2 className="text-xl font-semibold">{profile.name}</h2>{claudeCode && <Badge variant="secondary">Claude Code</Badge>}{!profile.enabled && <Badge variant="outline">Disabled</Badge>}</div>
        <AgentActions profile={profile} onChanged={() => setReload(n => n + 1)} /></div>{profile.description && view !== 'test' && <p className="text-sm text-muted-foreground">{profile.description}</p>}</div>
      <Tabs value={view} onValueChange={next => setParams(current => { current.set('view', next); return current; })} className="flex flex-col gap-6">
        <div className="overflow-x-auto"><TabsList aria-label="Agent sections" className="w-max">{sections.map(section => <TabsTrigger key={section} value={section}>{({ git: 'Code projects', ssh: 'SSH', mcp: 'MCP' } as Record<string,string>)[section] ?? section[0].toUpperCase()+section.slice(1)}</TabsTrigger>)}</TabsList></div>
        <TabsContent value={view} forceMount className="flex flex-col gap-6">
          {view === 'general' && profile.original && <p className="text-sm text-muted-foreground">Clone of <Link className="underline underline-offset-4" to={`/agents/${encodeURIComponent(profile.original.id)}`}>{profile.original.name}</Link></p>}
          {claudeCode && claudeCodeNotes[view] && <Alert><AlertDescription>{claudeCodeNotes[view]}</AlertDescription></Alert>}
          {profile.resources === 'per_run' && PER_RUN_NOTES[view] && <Alert><AlertDescription>{PER_RUN_NOTES[view]}</AlertDescription></Alert>}
          {!claudeCode && ['git','documents','ssh','mcp'].includes(view) && <p className="text-sm text-muted-foreground"><span>{TOOLS_TAB_NOTE}</span> <Link className="underline underline-offset-4" to="?view=tools">Open Tools</Link></p>}
          {toolsOpened && <div hidden={view !== 'tools'}><AgentToolsView key={reload} agentId={agentId} revision={profile.revision} onDirtyChange={setToolsDirty}
            onSaved={revision => setProfile(current => current && { ...current, revision })} /></div>}
          <div hidden={['test','tools'].includes(view)}><AgentProfileSettings key={reload} profile={profile} view={view} onSaved={setProfile} onDirtyChange={setProfileDirty} configurationDirty={configurationDirty} /></div>
          <div hidden={view !== 'general'}><AgentPromptCard profile={profile} onSaved={setProfile} onDirtyChange={setPromptDirty} /></div>
          <div hidden={view !== 'general'}><AgentModelSelection profile={profile} onRefresh={() => setReload(n => n + 1)} onSaved={saved => { if (saved) setProfile(saved); }} /></div>
          <div hidden={!(['general','ssh','mcp'].includes(view) || nativeTest)}><OperationsView view={nativeTest ? 'test' : 'settings'} section={view} agentId={agentId} profile={profile} unsavedProfile={profileDirty || promptDirty} onDirtyChange={setConfigurationDirty} onAgentChanged={() => setReload(n => n + 1)} available={profile.enabled} active={['general','ssh','mcp'].includes(view) || nativeTest} /></div>
          {claudeCode && view === 'test' && <Card><CardHeader><CardTitle>Test in a Claude Code session</CardTitle>
            <CardDescription>Claude Code agents can no longer start sessions. Use a native agent.</CardDescription></CardHeader></Card>}
        </TabsContent>
      </Tabs>
    </>}
  </section>;
}
