import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { PanelRight } from 'lucide-react';
import { formatTokens, statusLabels } from '~/lib/claudeCode';
import { toolUsageLine } from '~/lib/runs';
import { compactTokens, groupTurns, type Turn, type WorkItem } from '~/lib/turns';
import type { SessionRun } from '~/lib/useSessionEvents';
import GroundingSources from './GroundingSources';
import Markdown from './Markdown';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Field, FieldDescription, FieldLabel } from '~/components/ui/field';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '~/components/ui/sheet';
import { Textarea } from '~/components/ui/textarea';

const NEAR_TOP = 80;  // px: scrolling this close to the top loads the previous page
const NEAR_BOTTOM = 40;  // px: this close to the bottom, the main pane follows new events

export interface WorkspaceComposer {
  value: string; onChange: (value: string) => void; onSend: () => void;
  /** Shown as Stop; enabled while the session is working. */
  onStop?: () => void;
  busy?: boolean; blockedReason?: string | null; label?: string; placeholder?: string; sendLabel?: string;
}
export interface RunWorkspaceProps {
  run: SessionRun;
  agentName: string;
  agentType: string;          // 'Native' | 'Claude Code'
  resources: string;          // 'Fixed' | 'Per run: App, Worker'
  heading?: ReactNode;        // the page's title area, above the header line
  headerActions?: ReactNode;
  sidebar?: ReactNode;        // page sections below "Agents in this run"
  answerActions?: (turn: Turn) => ReactNode;  // under every finished turn
  empty?: ReactNode;          // the main pane without events
  notice?: string;
  composer: WorkspaceComposer;
}

const plural = (count: number, one: string) => `${count} ${one}${count === 1 ? '' : 's'}`;

function WorkLine({ item }: { item: WorkItem }) {
  switch (item.kind) {
    case 'reasoning': return <p className="whitespace-pre-wrap text-sm italic text-muted-foreground">{item.text}</p>;
    case 'comment': return <p className="whitespace-pre-wrap text-sm"><span className="font-medium">Your comment: </span>{item.text}</p>;
    case 'message': return <p className="whitespace-pre-wrap text-sm">{item.text}</p>;
    case 'tool': return <div className="flex flex-col gap-1 text-sm">
      <div><Badge variant="outline" className="mr-2">{item.name}</Badge><code className="break-all">{item.summary}</code></div>
      {item.preview ? <pre className={`max-h-48 overflow-auto rounded-md border p-2 text-xs${item.isError ? ' text-destructive' : ''}`}>{item.preview}</pre> : null}
    </div>;
  }
}

/** `modelCalls` is false for Claude Code: it records one aggregate usage per turn, so a call count would read "1". */
function TurnView({ turn, live, modelCalls, actions }: { turn: Turn; live: boolean; modelCalls: boolean; actions?: ReactNode }) {
  const worked = turn.work.length > 0 || turn.usage.total > 0;
  return <li data-turn={turn.turn} className="flex flex-col gap-3">
    {turn.input !== null && <div aria-label="Your message" className="self-end rounded-md bg-muted p-3 sm:max-w-[85%]">
      <p className="text-xs font-medium text-muted-foreground">You · turn {turn.turn}</p>
      <p className="whitespace-pre-wrap break-words text-sm">{turn.input}</p></div>}
    {worked && <details className="rounded-md border px-3 py-2">
      <summary className="cursor-pointer text-sm text-muted-foreground">
        {`${live ? 'Working' : 'Worked'} · ${plural(turn.toolCalls, 'tool call')} · ${compactTokens(turn.usage.total)} tokens`}</summary>
      <div className="mt-3 flex flex-col gap-3">
        {turn.usage.total > 0 && <p aria-label={`Turn ${turn.turn} usage`} className="text-xs text-muted-foreground">
          {`In ${formatTokens(turn.usage.input)} · Out ${formatTokens(turn.usage.output)} · Cached ${formatTokens(turn.usage.cached)}`
            + (modelCalls ? ` · Model calls ${formatTokens(turn.usage.calls)}` : '')}</p>}
        {turn.work.map(item => <WorkLine key={item.id} item={item} />)}</div>
    </details>}
    {turn.answer !== null && <article aria-label="Agent answer" className="flex flex-col gap-2">
      <Markdown content={turn.answer} />
      {turn.grounding?.sources.length ? <GroundingSources sources={turn.grounding.sources} /> : null}
      {turn.grounding?.not_covered && <p className="text-xs text-muted-foreground">The documentation does not cover this{turn.grounding.reason ? `: ${turn.grounding.reason}` : '.'}</p>}
    </article>}
    {live && <p role="status" className="text-sm text-muted-foreground">Working…</p>}
    {turn.status === 'failed' && <Alert variant="destructive"><AlertDescription>{turn.error ?? 'This turn failed.'}</AlertDescription></Alert>}
    {turn.status === 'stopped' && <p className="text-sm text-muted-foreground">Stopped.</p>}
    {turn.status !== 'working' && actions && <div className="flex flex-wrap gap-1">{actions}</div>}
  </li>;
}

/** One run of an agent: the header (agent, type, resources, status, tokens, tools used), the main pane of turns (it
 * scrolls on its own; earlier pages load near the top; it follows new events at the bottom), the sidebar (the agents
 * in this run, then the page's sections; a drawer on narrow screens) and the message box, always on screen. The Test
 * tab and the Generate documents session page pass their own parts as props. */
export default function RunWorkspace({ run, agentName, agentType, resources, heading, headerActions, sidebar, answerActions,
  empty, notice, composer }: RunWorkspaceProps) {
  const { session, events, older, toolCounts } = run;
  const turns = useMemo(() => groupTurns(events), [events]);
  const [following, setFollowing] = useState(true);
  const [drawer, setDrawer] = useState(false);
  const pane = useRef<HTMLDivElement>(null);
  const anchor = useRef<number | null>(null);  // the pane's scroll height before an earlier page went above
  const id = useId();

  useLayoutEffect(() => {
    const panel = pane.current;
    if (!panel) return;
    if (anchor.current !== null) {  // keep what the reader was looking at in place
      panel.scrollTop += panel.scrollHeight - anchor.current;
      anchor.current = null;
    } else if (following) {
      panel.scrollTop = panel.scrollHeight;
    }
  }, [events, following]);
  const loadOlder = () => run.loadOlder(() => { anchor.current = pane.current?.scrollHeight ?? null; });
  // While the pane does not overflow and there is more history, nothing can be scrolled to the top: load the next
  // earlier page. Runs on new events only, so a failed load is not retried in a loop.
  useEffect(() => {
    const panel = pane.current;
    if (panel && panel.clientHeight > 0 && panel.scrollHeight <= panel.clientHeight) void loadOlder();
  }, [events, session !== null]);  // eslint-disable-line react-hooks/exhaustive-deps
  function scrolled() {
    const panel = pane.current;
    if (!panel) return;
    setFollowing(panel.scrollHeight - panel.scrollTop - panel.clientHeight <= NEAR_BOTTOM);
    if (panel.scrollTop <= NEAR_TOP) void loadOlder();
  }
  function jump() {
    setFollowing(true);
    const panel = pane.current;
    if (panel) panel.scrollTop = panel.scrollHeight;
  }

  const working = session?.status === 'working';
  const closed = session?.status === 'closed';
  const modelCalls = session?.runtime !== 'claude_code';
  const toolCalls = (toolCounts ?? []).reduce((sum, count) => sum + count.calls, 0);
  const canSend = !composer.busy && !composer.blockedReason && Boolean(composer.value.trim());
  const agents = <div className="flex flex-col gap-4">
    <section aria-label="Agents in this run" className="flex flex-col gap-2">
      <h2 className="text-sm font-semibold">Agents in this run</h2>
      <div className="rounded-md border p-2 text-sm">
        <p className="font-medium">{agentName}</p>
        <p className="text-xs text-muted-foreground">{session
          ? `${statusLabels[session.status]} · ${plural(toolCalls, 'tool call')} · ${compactTokens(session.input_tokens + session.output_tokens)} tokens`
          : 'Not started'}</p>
      </div>
    </section>
    {sidebar}
  </div>;

  // The main pane shrinks (min-h-0) so the message box stays on screen; nothing in the header forces a horizontal
  // scroll on a phone (min-w-0, wrapping rows, long words break).
  return <div className="flex h-full min-h-0 min-w-0 flex-col gap-3">
    <header className="flex min-w-0 shrink-0 flex-col gap-2 break-words">
      {heading}
      <div data-testid="run-header" className="flex min-w-0 flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
          <span className="font-medium">{agentName}</span><Badge variant="outline">{agentType}</Badge>
          <span className="text-muted-foreground">Resources: {resources}</span>
          {session && <Badge variant={session.status === 'failed' ? 'destructive' : 'secondary'}>{statusLabels[session.status]}</Badge>}
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-1">{headerActions}
          <Button type="button" variant="ghost" size="sm" className="lg:hidden" onClick={() => setDrawer(true)}><PanelRight data-icon="inline-start" />Run details</Button>
        </div>
      </div>
      {session && <p aria-label="Token totals" className="text-sm">{`In ${formatTokens(session.input_tokens)} · Out ${formatTokens(session.output_tokens)} · Cached ${formatTokens(session.cached_tokens)}`
        + (modelCalls ? ` · Model calls ${formatTokens(session.model_calls)}` : '')
        + (session.reasoning_tokens > 0 ? ` · Reasoning ${formatTokens(session.reasoning_tokens)}` : '')}</p>}
      {toolCounts && <p aria-label="Tools used" className="text-sm text-muted-foreground">{toolUsageLine(toolCounts)}</p>}
      {session?.status === 'starting' && !session.worker_active && <Alert><AlertDescription>{`Waiting for the ${session.runtime === 'openai' ? 'agent session' : 'Claude Code'} worker. The session starts when the worker is running.`}</AlertDescription></Alert>}
      {session?.status_message && ['failed', 'stopped'].includes(session.status) && <Alert variant={session.status === 'failed' ? 'destructive' : 'default'}><AlertDescription>{session.status_message}</AlertDescription></Alert>}
      {run.error && <Alert variant="destructive"><AlertDescription>{run.error}</AlertDescription></Alert>}
      {run.reconnecting && !closed && <Alert><AlertDescription role="status">Reconnecting…</AlertDescription></Alert>}
      {notice && <Alert><AlertDescription role="status">{notice}</AlertDescription></Alert>}
    </header>
    <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[minmax(0,1fr)_18rem]">
      <section aria-label="Run" className="flex min-h-0 min-w-0 flex-col rounded-lg border">
        <div ref={pane} data-testid="run-scroll" onScroll={scrolled} className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4">
          {older === 'start' && events.length > 0 && <p className="text-center text-xs text-muted-foreground">Beginning of the session</p>}
          {older === 'loading' && <p role="status" className="text-center text-xs text-muted-foreground">Loading earlier activity…</p>}
          {turns.length === 0 ? empty : <ol className="flex flex-col gap-6">{turns.map((turn, index) =>
            <TurnView key={turn.turn} turn={turn} live={working && index === turns.length - 1 && turn.status === 'working'}
              modelCalls={modelCalls} actions={answerActions?.(turn)} />)}</ol>}
          {session && session.queued > 0 && <p className="text-sm text-muted-foreground">{plural(session.queued, 'message')} queued</p>}
        </div>
        {!following && <div className="shrink-0 border-t p-2 text-center"><Button type="button" size="sm" variant="secondary" onClick={jump}>Jump to latest</Button></div>}
      </section>
      <aside aria-label="Run sidebar" className="hidden min-h-0 overflow-y-auto rounded-lg border p-3 lg:block">{agents}</aside>
    </div>
    <form aria-label="Message box" className="flex shrink-0 flex-col gap-2" onSubmit={e => { e.preventDefault(); if (canSend) composer.onSend(); }}>
      <Field><FieldLabel htmlFor={id}>{composer.label ?? 'Message'}</FieldLabel>
        <Textarea id={id} rows={2} maxLength={20000} disabled={closed} value={composer.value} placeholder={composer.placeholder}
          onChange={e => composer.onChange(e.target.value)} onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); if (canSend) composer.onSend(); }
          }} />
        {composer.blockedReason && <FieldDescription>{composer.blockedReason}</FieldDescription>}</Field>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={!canSend}>{composer.sendLabel ?? 'Send message'}</Button>
        {composer.onStop && <Button type="button" variant="outline" disabled={composer.busy || !working} onClick={composer.onStop}>Stop</Button>}
      </div>
    </form>
    <Sheet open={drawer} onOpenChange={setDrawer}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-sm">
        <SheetHeader><SheetTitle>Run details</SheetTitle><SheetDescription>The agents in this run and this page's sections.</SheetDescription></SheetHeader>
        <div className="mt-4">{agents}</div>
      </SheetContent>
    </Sheet>
  </div>;
}
