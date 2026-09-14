import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { getPrompts, agentNames, type AgentName, type PromptVersion } from '../lib/prompts';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '~/components/ui/card';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '~/components/ui/collapsible';
import { Skeleton } from '~/components/ui/skeleton';

export default function AgentInstructions({ agent = "search", agentId = agent, agentLabel, revision = 0 }: { agent?: AgentName; agentLabel?: string; agentId?: string; revision?: number }) {
  const [prompt, setPrompt] = useState<PromptVersion>();
  const [error, setError] = useState<string>();
  const [open, setOpen] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let live = true;
    setError(undefined); setPrompt(undefined);
    getPrompts().then(data => {
      if (!live) return;
      const current = data.agents.find(row => row.agent === agentId);
      const version = data.versions.find(row => row.agent === agentId && row.id === current?.active_version_id);
      if (version) setPrompt(version); else setError('No active system prompt is available for this agent.');
    }).catch(() => { if (live) setError('Could not load the active system prompt.'); });
    return () => { live = false; };
  }, [agentId, reload, revision]);
  return <Card>
    <CardHeader><CardTitle>System prompt</CardTitle><CardDescription>Required for this agent. Changes are saved as drafts until activated.</CardDescription></CardHeader>
    <CardContent className="flex flex-col gap-3">
      {error ? <Alert variant="destructive"><AlertDescription>{error}<Button variant="outline" size="sm" onClick={() => setReload(value => value + 1)}>Retry</Button></AlertDescription></Alert> : !prompt ? <Skeleton className="h-10 w-full" /> : <>
        <div className="flex flex-wrap items-center gap-3"><Badge>Active system prompt</Badge><Link className="break-words underline underline-offset-4" to={`/agents/${encodeURIComponent(agentId)}?view=prompts#system-prompts-${agentId}`} aria-label={`Active system prompt: ${prompt.title}, version ${prompt.version}`}>{prompt.title} · v{prompt.version}</Link></div>
        <Collapsible open={open} onOpenChange={setOpen}>
          <CollapsibleTrigger asChild><Button variant="ghost" size="sm">{open ? 'Hide instructions' : 'Show instructions'}</Button></CollapsibleTrigger>
          <CollapsibleContent><pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words text-sm">{prompt.content}</pre></CollapsibleContent>
        </Collapsible>
      </>}
    </CardContent>
  </Card>;
}
