import { runPath } from '../lib/runs';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Link } from 'react-router';
import { ArrowRight, Bot, Play, Plus, RefreshCw, Settings2, Square } from 'lucide-react';
import { getAgents, type AgentSummary } from '../lib/profiles';
import { cancelBoardTask, getBoard, getCoordinatorSettings, submitBoardTask, type CoordinatorSettings as CoordinatorConfig, type AgentActivity, type BoardTask, type TeamTask } from '../lib/board';
import AgentWork from './AgentWork';
import Conversation from '@knowledge-agent/chat-ui/conversation';
import Markdown from './Markdown';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '~/components/ui/card';
import { Checkbox } from '~/components/ui/checkbox';
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '~/components/ui/empty';
import { Field, FieldContent, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from '~/components/ui/field';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/select';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '~/components/ui/sheet';
import { Skeleton } from '~/components/ui/skeleton';
import { Spinner } from '~/components/ui/spinner';
import { Textarea } from '~/components/ui/textarea';

const columns = [
  { title: 'Queued', statuses: ['queued'], empty: 'Subtasks waiting for an available agent will appear here.' },
  { title: 'Running', statuses: ['running'], empty: 'Subagents working on this task will appear here.' },
  { title: 'Results', statuses: ['completed', 'failed', 'cancelled'], empty: 'Each subagent’s result will appear here when its work finishes.' },
];
const phases = { agent: 'Working', planning: 'Planning subtasks', working: 'Subagents working', reviewing: 'Reviewing results', done: 'Finished' };
const emptyDraft = { subagent_ids: [] as string[], task: '' };

export default function AgentBoardView() {
  const [coordinator, setCoordinator] = useState<CoordinatorConfig>();
  const [agents, setAgents] = useState<AgentSummary[]>();
  const [tasks, setTasks] = useState<TeamTask[]>();
  const [draft, setDraft] = useState(emptyDraft);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [reload, setReload] = useState(0);
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [mainId, setMainId] = useState<string>();
  const [newTask, setNewTask] = useState(false);
  const [selectedId, setSelectedId] = useState<string>();
  const requestId = useRef<string | undefined>(undefined);
  const [activity, setActivity] = useState<AgentActivity[]>([]);
  const [connection, setConnection] = useState('Connecting to live activity…');

  useEffect(() => {
    let live = true;
    Promise.all([getAgents(), getCoordinatorSettings()]).then(([data, settings]) => { if (live) { setAgents(data.agents.filter(agent => !agent.is_coordinator)); setCoordinator(settings); } })
      .catch(reason => { if (live) setError(reason.message); });
    return () => { live = false; };
  }, [reload]);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const data = await getBoard(offset, controller.signal);
        if (!controller.signal.aborted) {
          setTasks(data.tasks); setHasMore(data.has_more); setLoadError('');
        }
      } catch (reason) {
        if (!controller.signal.aborted) setLoadError(reason instanceof Error ? reason.message : 'Could not refresh the board.');
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(() => void refresh(), 2000);
      }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [offset, reload]);

  const ready = coordinator?.enabled && coordinator.model_enabled && coordinator.system_prompt.trim() && draft.subagent_ids.length > 0 &&
    draft.subagent_ids.every(id => agents?.some(a => a.id === id && a.enabled && a.model_id && a.prompt_version));
  const main = tasks?.find(item => item.id === mainId) ?? tasks?.[0];
  const selected: BoardTask | undefined = main?.subtasks.find(item => item.id === selectedId);
  const showForm = newTask || tasks?.length === 0;
  const active = Boolean(main && ['queued', 'running'].includes(main.status));

  useEffect(() => { setActivity([]); }, [main?.id]);
  useEffect(() => {
    if (!main || !active) return;
    setConnection('Connecting to live activity…');
    const stream = new EventSource(`/api/agent-board/tasks/${encodeURIComponent(main.id)}/activity`);
    stream.addEventListener('connected', () => setConnection('Live updates connected.'));
    stream.addEventListener('activity', event => {
      const item = JSON.parse((event as MessageEvent).data) as AgentActivity;
      setActivity(previous => {
        const index = previous.findIndex(existing => existing.id === item.id && existing.task_id === item.task_id);
        if (index < 0) return [...previous, item].slice(-200);
        return previous.map((existing, i) => i === index ? item : existing);
      });
    });
    stream.addEventListener('done', () => {
      stream.close(); setConnection('Execution finished.'); setReload(value => value + 1);
    });
    stream.addEventListener('unavailable', () => setConnection('Live updates interrupted. Reconnecting; Phoenix retains recorded work.'));
    stream.onerror = () => setConnection('Live updates interrupted. Reconnecting; Phoenix retains recorded work.');
    return () => stream.close();
  }, [main?.id, active]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (submitting || !ready || !draft.task.trim()) return;
    setSubmitting(true); setError('');
    requestId.current ??= crypto.randomUUID();
    try {
      const result = await submitBoardTask({ ...draft, id: requestId.current });
      requestId.current = undefined;
      setMainId(result.id); setDraft(emptyDraft); setNewTask(false);
      setOffset(0); setReload(value => value + 1);
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not submit this task.'); }
    finally { setSubmitting(false); }
  }

  async function cancel(task: TeamTask) {
    setCancelling(true); setError('');
    try { await cancelBoardTask(task.id); setReload(value => value + 1); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not stop this task.'); }
    finally { setCancelling(false); }
  }

  return <section className="flex min-w-0 flex-col gap-6" aria-label="Multi-agent board">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p className="text-sm text-muted-foreground">One task. A coordinator, a team of subagents, and one final answer.</p>
      <div className="flex gap-2">
        <Button variant="outline" size="sm" asChild><Link to={coordinator ? `/agents/${encodeURIComponent(coordinator.agent_id)}` : '/agents'}><Settings2 data-icon="inline-start" />Coordinator settings</Link></Button>
        <Button variant="outline" size="sm" onClick={() => setReload(value => value + 1)}><RefreshCw data-icon="inline-start" />Refresh board</Button>
        {!showForm && <Button size="sm" onClick={() => { setNewTask(true); setDraft(emptyDraft); requestId.current = undefined; }}><Plus data-icon="inline-start" />New task</Button>}
      </div>
    </div>
    {(error || loadError) && <Alert variant="destructive"><AlertDescription>{error || loadError}</AlertDescription></Alert>}
    {!tasks && <Skeleton className="h-72 w-full" />}

    {showForm && <Card>
      <CardHeader><CardTitle>Start a team task</CardTitle><CardDescription>The coordinator breaks down your task, delegates work to the selected agents, and combines their findings. Each agent uses its own saved configuration.</CardDescription></CardHeader>
      <form onSubmit={event => void submit(event)}>
        <CardContent className="pb-6"><FieldGroup>
          <Field><FieldLabel htmlFor="board-task">Main task</FieldLabel>
            <Textarea id="board-task" required rows={4} maxLength={40000} value={draft.task} disabled={submitting}
              placeholder="Describe the goal for the whole team…"
              onChange={event => { requestId.current = undefined; setDraft(current => ({ ...current, task: event.target.value })); }} />
          </Field>
          <Field><FieldLabel>Coordinator</FieldLabel>
            <p className="text-sm">{coordinator && !coordinator.enabled ? 'The coordinator is disabled. Enable it on its agent page to start tasks.' : coordinator?.model_enabled ? `${coordinator.name} · ${coordinator.model_name}` : 'Choose a model in Coordinator settings before starting.'}</p>
            <FieldDescription>Your request always goes to the coordinator, with one or more specialists selected below.</FieldDescription>
          </Field>
          <FieldSet><FieldLegend>Subagents</FieldLegend><FieldDescription>Select up to 10 agents the coordinator can delegate to.</FieldDescription>
            <FieldGroup className="grid gap-4 md:grid-cols-2">
              {agents?.map(item => {
                const checked = draft.subagent_ids.includes(item.id);
                const disabled = submitting || !item.enabled || !item.model_id || !item.prompt_version || (!checked && draft.subagent_ids.length >= 10);
                return <Field key={item.id} orientation="horizontal" data-disabled={disabled}>
                  <Checkbox id={`member-${item.id}`} checked={checked} disabled={disabled} onCheckedChange={value => {
                    requestId.current = undefined; setDraft(current => ({ ...current, subagent_ids: value === true ? [...current.subagent_ids, item.id] : current.subagent_ids.filter(id => id !== item.id) }));
                  }} />
                  <FieldContent><FieldLabel htmlFor={`member-${item.id}`}>{item.name}</FieldLabel>
                    <FieldDescription>{!item.enabled ? 'Disabled' : !item.model_id || !item.prompt_version ? 'Needs a model and active prompt' : item.description || item.model_name}</FieldDescription>
                  </FieldContent>
                </Field>;
              })}
            </FieldGroup>
          </FieldSet>
        </FieldGroup></CardContent>
        <CardFooter className="flex-wrap gap-3"><Button disabled={submitting || !ready || !draft.task.trim()}>
          {submitting ? <Spinner data-icon="inline-start" /> : <Play data-icon="inline-start" />}{submitting ? 'Submitting…' : 'Start task'}
        </Button>{main && <Button type="button" variant="ghost" disabled={submitting} onClick={() => setNewTask(false)}>Cancel</Button>}
          <p className="text-sm text-muted-foreground">Up to 3 specialists run at once. Results are saved automatically.</p>
        </CardFooter>
      </form>
    </Card>}

    {main && !showForm && <>
      <Field><FieldLabel htmlFor="board-history">Task history</FieldLabel>
        <Select value={main.id} onValueChange={id => { setMainId(id); setSelectedId(undefined); }}>
          <SelectTrigger id="board-history" className="w-full"><SelectValue /></SelectTrigger>
          <SelectContent><SelectGroup>{tasks?.map(task => <SelectItem key={task.id} value={task.id}>
            {task.task.slice(0, 100)} · {new Date(task.created_at).toLocaleString()}
          </SelectItem>)}</SelectGroup></SelectContent>
        </Select>
      </Field>
      <Card>
        <CardHeader><CardTitle>Main task</CardTitle><CardDescription className="flex flex-wrap items-center gap-2">
          <Badge variant={main.status === 'failed' ? 'destructive' : 'secondary'}>
            {main.status === 'running' && <Spinner data-icon="inline-start" />}
            {main.cancel_requested && main.status === 'running' ? 'Stopping…' : main.status === 'running' ? phases[main.phase] : main.status}
          </Badge>
          Coordinator: {main.agent_id ? <Link to={`/agents/${encodeURIComponent(main.agent_id)}`} className="underline">{main.agent_name}</Link> : <span>{main.model_name ?? coordinator?.model_name ?? 'Waiting to start'}</span>}
        </CardDescription></CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-wrap gap-2" aria-label="Selected subagents">{main.subagents.map(item => <Badge key={item.id} variant="outline">{item.name}</Badge>)}</div>
          <Conversation key={main.id} className="h-[32rem]" messages={[
            { id: `${main.id}:user`, role: 'user', content: main.task },
            { id: main.id, role: 'assistant', name: main.agent_name || coordinator?.name || 'Coordinator',
              content: main.result || main.plan || '', pending: active, error: main.error ?? undefined,
              status: active ? phases[main.phase] : main.status === 'completed' ? undefined : main.status,
              details: <AgentWork task={main} items={activity.filter(item => item.task_id === main.id)} connection={connection} />,
            },
          ]} />
        </CardContent>
        <CardFooter className="flex-wrap gap-3">
          {['queued', 'running'].includes(main.status) && <Button variant="outline" disabled={cancelling || main.cancel_requested} onClick={() => void cancel(main)}><Square data-icon="inline-start" />Stop task</Button>}
          <Button variant="ghost" asChild><Link to={runPath(`board:${main.id}`)}>Full record</Link></Button>
          <Button variant="ghost" onClick={() => { requestId.current = undefined; setDraft({ subagent_ids: main.subagents.map(a => a.id), task: main.task }); setNewTask(true); }}>Use task again</Button>
          <p className="text-sm text-muted-foreground">{main.subtasks.filter(t => t.status === 'completed').length} of {main.subtasks.length} subtasks completed{main.subtasks.some(t => t.status === 'failed') && ` · ${main.subtasks.filter(t => t.status === 'failed').length} failed`}</p>
        </CardFooter>
      </Card>

      <div className="grid min-w-0 gap-5 xl:grid-cols-3">
        {columns.map(column => {
          const items = main.subtasks.filter(task => column.statuses.includes(task.status));
          return <section key={column.title} className="flex min-w-0 flex-col gap-3" aria-label={column.title}>
            <div className="flex items-center gap-2"><h2 className="font-semibold">{column.title}</h2><Badge variant="secondary">{items.length}</Badge></div>
            {items.length === 0 && <Empty className="min-h-40 border"><EmptyHeader><EmptyMedia variant="icon"><Bot /></EmptyMedia><EmptyTitle>No {column.title.toLowerCase()} yet</EmptyTitle><EmptyDescription>{column.empty}</EmptyDescription></EmptyHeader></Empty>}
            {items.map(task => <Card key={task.id}>
              <CardHeader><CardTitle><Link to={`/agents/${encodeURIComponent(task.agent_id ?? '')}`} className="underline-offset-4 hover:underline">{task.agent_name}</Link></CardTitle>
                <CardDescription><Badge variant={task.status === 'failed' ? 'destructive' : 'secondary'}>{task.status === 'running' && <Spinner data-icon="inline-start" />}{task.status}</Badge></CardDescription>
              </CardHeader>
              <CardContent className="flex min-w-0 flex-col gap-3">
                <p className="line-clamp-4 whitespace-pre-wrap break-words text-sm">{task.task}</p>
                {task.result && <p className="line-clamp-4 whitespace-pre-wrap break-words text-sm text-muted-foreground">{task.result}</p>}
                {task.error && <Alert variant="destructive"><AlertDescription>{task.error}</AlertDescription></Alert>}
              </CardContent>
              <CardFooter><Button variant="outline" size="sm" onClick={() => setSelectedId(task.id)}>View work<ArrowRight data-icon="inline-end" /></Button></CardFooter>
            </Card>)}
          </section>;
        })}
      </div>
      {(offset > 0 || hasMore) && <div className="flex justify-end gap-2"><Button variant="outline" disabled={offset === 0} onClick={() => { setMainId(undefined); setOffset(value => Math.max(0, value - 20)); }}>Newer tasks</Button><Button variant="outline" disabled={!hasMore} onClick={() => { setMainId(undefined); setOffset(value => value + 20); }}>Older tasks</Button></div>}
    </>}



    <Sheet open={Boolean(selected)} onOpenChange={open => { if (!open) setSelectedId(undefined); }}>
      <SheetContent className="flex w-full flex-col gap-6 overflow-y-auto sm:max-w-3xl">
        <SheetHeader><SheetTitle>{selected?.agent_name} · Subagent work</SheetTitle><SheetDescription>
          {selected?.model_name ?? 'Waiting to start'}{selected?.prompt_version ? ` · Prompt v${selected.prompt_version}` : ''}
        </SheetDescription></SheetHeader>
        {selected && <>
          <Badge className="self-start" variant={selected.status === 'failed' ? 'destructive' : 'secondary'}>{selected.status}</Badge>
          <section className="flex flex-col gap-2"><h3 className="font-semibold">Assigned subtask</h3><p className="whitespace-pre-wrap break-words text-sm">{selected.task}</p></section>
          {selected.error && <Alert variant="destructive"><AlertDescription>{selected.error}</AlertDescription></Alert>}
          <AgentWork task={selected} items={activity.filter(item => item.task_id === selected.id)} connection={connection} />
          <section className="flex min-w-0 flex-col gap-2" aria-label="Agent result"><h3 className="font-semibold">Result</h3>
            {selected.result ? <Markdown content={selected.result} /> : <p className="text-sm text-muted-foreground">{['queued', 'running'].includes(selected.status) ? 'The result will appear when this agent finishes.' : 'This subtask returned no result.'}</p>}
          </section>
        </>}
      </SheetContent>
    </Sheet>
  </section>;
}
