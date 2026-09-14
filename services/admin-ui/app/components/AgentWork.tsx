import { ChevronDown, ExternalLink } from 'lucide-react';
import type { AgentActivity, BoardTask } from '../lib/board';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '~/components/ui/collapsible';

export default function AgentWork({ task, items, connection }: {
  task: BoardTask; items: AgentActivity[]; connection: string;
}) {
  const running = ['queued', 'running'].includes(task.status);
  return <section className="flex min-w-0 flex-col gap-3" aria-label={`${task.agent_name} activity`}>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="font-semibold">{task.parent_id ? 'Agent activity' : 'Coordinator activity'}</h3>
      {task.trace_url && <Button asChild variant="outline" size="sm">
        <a href={task.trace_url} target="_blank" rel="noopener noreferrer">View trace<ExternalLink data-icon="inline-end" /></a>
      </Button>}
    </div>
    <p className="text-sm text-muted-foreground">
      {running ? connection : 'Execution finished.'} Activity is shown from when you open this task. Phoenix keeps the recorded history; active spans appear after they finish.
    </p>
    {items.length === 0 && <p className="text-sm text-muted-foreground">
      {running ? 'Waiting for an agent update or tool call…' : 'No live activity captured in this view.'}
    </p>}
    <div className="flex max-h-96 min-w-0 flex-col gap-2 overflow-y-auto">
      {items.map(item => <Collapsible key={item.id} defaultOpen={item.kind === 'plan' || item.kind === 'agentMessage'} className="rounded-md border">
        <CollapsibleTrigger className="flex w-full min-w-0 items-center gap-2 p-3 text-left text-sm">
          <ChevronDown className="size-4 shrink-0" />
          <span className="min-w-0 flex-1 truncate font-medium">{item.title}</span>
          <Badge variant={item.status === 'failed' ? 'destructive' : 'secondary'}>
            {item.status === 'inProgress' ? running ? 'Running' : 'Interrupted' : item.status}
          </Badge>
          <time className="shrink-0 text-xs text-muted-foreground" dateTime={item.time}>{new Date(item.time).toLocaleTimeString()}</time>
        </CollapsibleTrigger>
        <CollapsibleContent className="min-w-0 px-3 pb-3">
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words font-mono text-xs">{item.detail || 'Waiting for output…'}</pre>
        </CollapsibleContent>
      </Collapsible>)}
    </div>
    {items.length >= 200 && <p className="text-sm text-muted-foreground">Showing the most recent 200 activity items. Open the trace for earlier work.</p>}
  </section>;
}
