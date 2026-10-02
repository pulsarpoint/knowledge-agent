import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { Code2, Copy, History, MessageSquarePlus, Plus, X } from 'lucide-react';
import Conversation, { type ConversationMessage } from '@knowledge-agent/chat-ui/conversation';
import { getAgentConfig } from '../lib/profiles';
import { cancelOperationsRun, closeAgentSession, getAgentSession, getAgentSessions, getOperationsRun, getOperationsRuns, reviewAgentSession, startOperationsRun, type AgentConversation, type AgentSession, type OperationsRun, type RunSummary, type RunUsage } from '../lib/operations';
import Markdown from './Markdown';
import { Alert, AlertDescription } from './ui/alert';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from './ui/alert-dialog';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from './ui/sheet';
import { Separator } from './ui/separator';
import { Field, FieldDescription, FieldGroup, FieldLabel } from './ui/field';
import { Spinner } from './ui/spinner';
import GroundingSources from './GroundingSources';
import { Textarea } from './ui/textarea';

export default function AgentTestView({ agentId, agentName, available, ready, dirty, active, revision }: { agentId: string; agentName: string; available: boolean; ready: boolean; dirty: boolean; active: boolean; revision?: number }) {
  const [params, setParams] = useSearchParams();
  const sessionId = params.get('session_id');
  const versionId = params.get('version_id');
  const [conversation, setConversation] = useState<AgentConversation | null>(null);
  const [sessions, setSessions] = useState<AgentSession[]>([]);
  const [history, setHistory] = useState<RunSummary[]>([]);
  const [legacy, setLegacy] = useState<OperationsRun | null>(null);
  const [task, setTask] = useState('');
  const [comment, setComment] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [closing, setClosing] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [panel, setPanel] = useState<'history' | 'config' | 'feedback' | null>(null);
  const [feedbackRunId, setFeedbackRunId] = useState<string | null>(null);
  const showConfig = panel === 'config';
  const [config, setConfig] = useState<Record<string, unknown> | null>(null);
  const [configError, setConfigError] = useState('');
  const request = useRef<AbortController | null>(null);
  const running = streaming || conversation?.runs.some(run => run.status === 'running') === true || legacy?.status === 'running';
  useEffect(() => () => { request.current?.abort(); }, [agentId]);

  async function refreshHistory() {
    const [recent, saved] = await Promise.all([getOperationsRuns(agentId), getAgentSessions(agentId)]);
    setHistory(recent.runs); setSessions(saved.sessions);
  }
  useEffect(() => { void refreshHistory().catch(reason => setError(reason.message)); }, [agentId]);
  useEffect(() => {
    if (!sessionId) { setConversation(null); return; }
    if (streaming) return;
    let live = true;
    async function load() {
      try {
        const value = await getAgentSession(agentId, sessionId!);
        if (live) { setConversation(value); setLegacy(null); }
      } catch (reason) { if (live) setError(reason instanceof Error ? reason.message : 'Could not load session.'); }
    }
    void load();
    const timer = active ? setInterval(() => void load(), running ? 2000 : 30000) : undefined;
    return () => { live = false; clearInterval(timer); };
  }, [sessionId, agentId, streaming, active, running]);
  useEffect(() => {
    setConfig(null); setConfigError('');
    if ((!showConfig && !versionId) || !active || dirty) return;
    if (conversation) { setConfig(conversation.config); return; }
    let live = true;
    getAgentConfig(agentId,versionId ?? undefined).then(value => { if (live) setConfig(value); })
      .catch(reason => { if (live) setConfigError(reason.message); });
    return () => { live = false; };
  }, [showConfig, agentId, active, dirty, revision, versionId, conversation]);

  function selectSession(id: string | null) {
    setConversation(null); setLegacy(null); setError(''); setNotice(''); setTask(''); setComment(''); setFeedbackRunId(null); setPanel(null);
    setParams(current => { current.set('view','test'); if (id) { current.set('session_id',id); current.delete('version_id'); } else current.delete('session_id'); return current; });
  }
  async function act(action: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('');
    try { await action(); } catch (reason) { setError(reason instanceof Error ? reason.message : 'The operation failed.'); }
    finally { setBusy(false); }
  }
  async function send() {
    if (request.current) return;
    const controller = new AbortController(); request.current = controller;
    setStreaming(true); setError(''); setNotice(''); setLegacy(null);
    try {
      await startOperationsRun(task, agentId, controller.signal, event => {
        if (event.event === 'activity') {
          setConversation(current => current && { ...current, runs: current.runs.map(run => run.status !== 'running' ? run : { ...run, progress: event.data.title,
            context: { ...run.context, activity: [...(run.context.activity ?? []).filter(item => item.id !== event.data.id), event.data] } }) });
        } else {
          const run = event.data;
          if (run.session_id) {
            const id = run.session_id;
            if (event.event === 'started') setParams(current => { current.set('session_id',id); return current; }, { replace: true });
            setConversation(current => current?.id === id ? { ...current, runs: [...current.runs.filter(item => item.id !== run.id), run] } : {
              id, is_test:true, title: run.task.slice(0,200), status:'active', created_at:run.created_at,
              last_activity_at:run.created_at, expires_at:new Date(Date.now()+3600000).toISOString(),
              config:run.context.config ?? {}, runs:[run], reviews:[],
            });
          } else setLegacy(run);
          if (event.event === 'started') setTask('');
          setHistory(current => [run, ...current.filter(item => item.id !== run.id)].slice(0,50));
        }
      }, sessionId ?? undefined, sessionId ? undefined : versionId ?? undefined);
      await refreshHistory();
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Could not run the agent.');
    } finally {
      if (request.current === controller) request.current = null;
      if (!controller.signal.aborted) setStreaming(false);
    }
  }
  const blockReason = !available ? 'This agent is disabled. Enable it in General to run a task.' : !ready ? 'Loading agent configuration…'
    : dirty ? 'Save or discard changes in the configuration tabs before starting a task.'
    : sessionId && conversation?.id !== sessionId ? 'Loading conversation…'
    : conversation && conversation.status !== 'active' ? 'This session is closed or expired. Start a new session to continue.'
    : running ? null
    : busy ? 'Please wait for the current operation to finish.'
    : null;
  const pinnedVersion = (conversation?.config.config_version ?? config?.config_version) as { id: string; version: number; label: string } | undefined;
  const turns = conversation?.runs ?? (legacy ? [legacy] : []);
  const legacyHistory = history.filter(run => !run.session_id);
  const finishedTurns = turns.filter(run => run.status !== 'running');
  const recordedUsage = finishedTurns.map(run => run.context.usage)
    .filter((usage): usage is RunUsage => Boolean(usage && (usage.requests > 0 || usage.total_tokens > 0)));
  const usage = recordedUsage.reduce((sum, turn) => ({
    requests:sum.requests + turn.requests, input:sum.input + turn.input_tokens,
    output:sum.output + turn.output_tokens, total:sum.total + turn.total_tokens,
    cached:sum.cached + (turn.input_tokens_details?.cached_tokens ?? 0),
    cacheWrite:sum.cacheWrite + (turn.input_tokens_details?.cache_write_tokens ?? 0),
    reasoning:sum.reasoning + (turn.output_tokens_details?.reasoning_tokens ?? 0),
  }), {requests:0,input:0,output:0,total:0,cached:0,cacheWrite:0,reasoning:0});
  const missingUsage = finishedTurns.length - recordedUsage.length;
  const usageAvailable = recordedUsage.length > 0 || finishedTurns.length === 0;

  const feedbackRun = turns.find(run => run.id === feedbackRunId);
  const messages: ConversationMessage[] = turns.flatMap(run => [
    { id: `${run.id}:user`, role: 'user', content: run.task },
    {
      id: run.id, role: 'assistant', name: agentName, content: run.answer,
      pending: run.status === 'running', error: run.error,
      status: run.status === 'running' ? run.progress : run.status === 'completed' ? undefined : run.status,
      details: <>
        {run.context.grounding?.sources?.length ? <GroundingSources sources={run.context.grounding.sources} /> : null}
        {(run.context.activity?.length || run.commands.length) ? <details className="text-sm text-muted-foreground">
        <summary className="cursor-pointer py-1">View activity · {run.context.model_name} · {run.context.config?.config_version ? `Agent v${(run.context.config.config_version as { version: number }).version}` : `Prompt v${run.context.prompt.version}`}</summary>
        <div aria-label="Agent activity" className="mt-3 flex flex-col gap-3">
          {run.context.activity?.map(item => <details key={item.id}><summary className="cursor-pointer">{item.title} · {item.status}</summary>{item.detail && <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs">{item.detail}</pre>}</details>)}
          {run.commands.map(command => <details key={command.id}><summary>{command.server_name} · {command.status} · exit {command.exit_code}</summary><pre>{command.command}</pre><pre aria-label="Standard output">{command.stdout}</pre>{command.stderr && <pre aria-label="Standard error">{command.stderr}</pre>}{command.error && <p>{command.error}</p>}</details>)}
        </div>
      </details> : undefined}
      </>,
      actions: conversation && <Button variant="ghost" size="sm" disabled={busy || running} onClick={() => {
        setFeedbackRunId(run.id); setComment(''); setPanel('feedback'); setError('');
      }}><MessageSquarePlus data-icon="inline-start" />Add feedback</Button>,
    },
  ]);

  return <div className="flex min-w-0 flex-col gap-3">
    {error && panel !== 'feedback' && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {notice && <p role="status" className="text-sm text-muted-foreground">{notice}</p>}
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="flex min-w-0 items-center gap-2">
        <span className="truncate text-sm font-medium">{conversation?.title ?? 'New conversation'}</span>
        {conversation && <Badge variant="outline" title={conversation.status === 'active' ? `Expires after one hour of inactivity (${new Date(conversation.expires_at).toLocaleTimeString()})` : undefined}>{conversation.status}</Badge>}
      </div>
      <div className="flex flex-wrap gap-1">
        <Button variant="ghost" size="sm" disabled={busy || streaming} onClick={() => setPanel('history')}><History data-icon="inline-start" />History</Button>
        {Boolean(conversation?.reviews.length) && <Button variant="ghost" size="sm" onClick={() => { setFeedbackRunId(null); setPanel('feedback'); }}>Suggestions ({conversation!.reviews.length})</Button>}
        <Button variant="ghost" size="sm" disabled={dirty} onClick={() => setPanel('config')}><Code2 data-icon="inline-start" />Show config</Button>
        {conversation?.status === 'active' && <Button variant="ghost" size="sm" aria-label="Close session" title="Close session" disabled={busy} onClick={() => setClosing(true)}><X data-icon="inline-start" /></Button>}
        <Button variant="outline" size="sm" disabled={busy || streaming} onClick={() => selectSession(null)}><Plus data-icon="inline-start" />New session</Button>
      </div>
    </div>
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <span>{pinnedVersion ? `Agent v${pinnedVersion.version} · ${pinnedVersion.label}` : conversation ? 'Saved session configuration' : versionId ? 'Selected version' : 'Active configuration'}</span>
      <span>· {conversation ? 'Pinned to this conversation' : 'Pinned when the conversation starts'}</span>
      <Link className="underline" to="?view=versions">Choose version</Link>
    </div>
    {(conversation || legacy) && <section aria-label="Session usage" className="flex flex-col gap-2">
      <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        {([['Total tokens',usage.total],['Input tokens',usage.input],['Output tokens',usage.output],['Model calls',usage.requests]] as const).map(([label,value]) => <div key={label}>
          <dt className="text-xs text-muted-foreground">{label}</dt>
          <dd className="font-medium tabular-nums">{usageAvailable ? value.toLocaleString() : 'Unavailable'}</dd>
        </div>)}
      </dl>
      {(usage.cached > 0 || usage.cacheWrite > 0 || usage.reasoning > 0) && <p className="text-xs text-muted-foreground">
        {[
          usage.cached > 0 && `${usage.cached.toLocaleString()} cached input tokens`,
          usage.cacheWrite > 0 && `${usage.cacheWrite.toLocaleString()} cache-write tokens`,
          usage.reasoning > 0 && `${usage.reasoning.toLocaleString()} reasoning tokens`,
        ].filter(Boolean).join(' · ')}. Included in the totals above.
      </p>}
      <p className="text-xs text-muted-foreground">Conversation usage across model calls, including repeated context. Updates after each reply; tuning reviews are excluded.</p>
      {missingUsage > 0 && <p className="text-xs text-muted-foreground">Usage was not recorded for {missingUsage} {missingUsage === 1 ? 'turn' : 'turns'}.{recordedUsage.length > 0 ? ' Totals are partial.' : ''}</p>}
      {running && <p role="status" className="text-xs text-muted-foreground">Current reply’s usage is pending.</p>}
    </section>}
    {sessionId && !conversation && !error && <p role="status" className="flex items-center gap-2 text-sm"><Spinner />Loading conversation…</p>}
    <Conversation key={sessionId ?? legacy?.id ?? 'new'} messages={messages}
      className="h-[calc(100dvh-22rem)] min-h-96"
      emptyTitle={`Chat with ${agentName}`} emptyDescription="Ask a question, give a task, or explore an idea together."
      composer={{ value: task, onChange: setTask, onSend: () => void send(), pending: running, disabled: busy,
        blockedReason: blockReason, label: 'Task', sendLabel: conversation ? 'Send message' : 'Run task', stopLabel: 'Cancel run',
        onStop: () => { const run = turns.find(item => item.status === 'running'); if (run) void act(async () => {
          await cancelOperationsRun(run.id,agentId);
          if (run.session_id) setConversation(await getAgentSession(agentId,run.session_id)); else setLegacy(await getOperationsRun(run.id,agentId));
          await refreshHistory();
        }); },
      }} />

    <Sheet open={panel !== null} onOpenChange={open => { if (!open) setPanel(null); }}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle>{panel === 'history' ? 'Conversation history' : panel === 'config' ? 'Configuration preview' : feedbackRun ? 'Improve this response' : 'Saved suggestions'}</SheetTitle>
          <SheetDescription>{panel === 'history' ? 'Reopen a session to continue chatting. Closed conversations remain available to read.' : panel === 'config' ? 'The configuration for this conversation and your next message. Secrets are credential references.' : 'Optional feedback for the reviewer. It uses the full conversation and agent configuration; changes are never applied automatically.'}</SheetDescription>
        </SheetHeader>
        <div className="mt-6 flex flex-col gap-6">
          {panel === 'config' && (configError ? <Alert variant="destructive"><AlertDescription>{configError}</AlertDescription></Alert> : config ? <pre aria-label="Agent configuration" tabIndex={0} className="overflow-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify({prompt:task,config},null,2)}</pre> : <p role="status"><Spinner />Loading configuration…</p>)}
          {panel === 'history' && <>
            <h3 className="text-sm font-medium">Sessions</h3>
            {sessions.length ? sessions.map(saved => <div key={saved.id} className="flex items-center justify-between gap-3">
              <div className="min-w-0"><p className="break-words text-sm">{saved.title}</p><p className="text-xs text-muted-foreground">{saved.status} · {new Date(saved.created_at).toLocaleString()}</p></div>
              <Button variant="outline" size="sm" onClick={() => selectSession(saved.id)}>Open session</Button>
            </div>) : <p className="text-sm text-muted-foreground">No saved sessions yet.</p>}
            {legacyHistory.length > 0 && <><Separator /><h3 className="text-sm font-medium">Earlier tests</h3>{legacyHistory.map(row => <div key={row.id} className="flex items-center justify-between gap-3"><div className="min-w-0"><p className="break-words text-sm">{row.task.slice(0,180)}</p><p className="text-xs text-muted-foreground">{row.status} · {row.actor_email}</p></div><Button variant="outline" size="sm" onClick={() => void act(async () => { selectSession(null); setLegacy(await getOperationsRun(row.id,agentId)); })}>View run</Button></div>)}</>}
          </>}
          {panel === 'feedback' && <>
            {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
            {feedbackRun && <>
              <p className="text-sm text-muted-foreground">Response to: {feedbackRun.task}</p>
              <form onSubmit={event => { event.preventDefault(); if (conversation && comment.trim() && !busy && !running) void act(async () => {
                const review = await reviewAgentSession(agentId,conversation.id,comment,feedbackRun.id);
                setConversation(current => current && {...current,reviews:[...current.reviews,review]}); setComment('');
              }); }}>
                <FieldGroup><Field><FieldLabel htmlFor="agent-feedback">Your feedback</FieldLabel><Textarea id="agent-feedback" rows={4} maxLength={20000} disabled={busy} value={comment} onChange={event => setComment(event.target.value)} placeholder="What should the agent do differently or remember?" /><FieldDescription>Describe what a better response would do.</FieldDescription></Field>
                  <Button className="self-start" disabled={busy || running || !comment.trim()}>{busy && <Spinner data-icon="inline-start" />}Suggest improvements</Button>
                </FieldGroup>
              </form>
            </>}
            {conversation?.reviews.filter(review => !feedbackRunId || review.response_id === feedbackRunId || !review.response_id).map(review => <div key={review.id} className="flex flex-col gap-4">
              <Separator /><p className="text-xs text-muted-foreground">{new Date(review.created_at).toLocaleString()}</p>
              <p className="whitespace-pre-wrap text-sm"><strong>Your feedback:</strong> {review.comment}</p><Markdown content={review.suggestion} />
              <div className="flex flex-wrap gap-2"><Button variant="outline" size="sm" onClick={() => void act(async () => { await navigator.clipboard.writeText(review.suggestion); setNotice('Suggestions copied.'); })}><Copy data-icon="inline-start" />Copy suggestions</Button>
                <Button size="sm" asChild><Link to={`?view=versions&review_id=${review.id}`} onClick={() => setPanel(null)}>Save as new version</Link></Button>
                <Button variant="outline" size="sm" asChild><Link to={`?view=prompts&session_id=${conversation.id}`} onClick={() => setPanel(null)}>Edit system prompt</Link></Button>
                <Button variant="outline" size="sm" asChild><Link to={`?view=general&session_id=${conversation.id}`} onClick={() => setPanel(null)}>Edit instructions</Link></Button>
              </div>
            </div>)}
          </>}
        </div>
      </SheetContent>
    </Sheet>
    <AlertDialog open={closing} onOpenChange={setClosing}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Close this session?</AlertDialogTitle><AlertDialogDescription>The conversation and suggestions remain available for review. Any running message stops, and further messages require a new session.</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel disabled={busy}>Keep session</AlertDialogCancel><AlertDialogAction disabled={busy} onClick={event => { event.preventDefault(); if (conversation) void act(async () => { await closeAgentSession(agentId,conversation.id); setConversation(await getAgentSession(agentId,conversation.id)); setClosing(false); await refreshHistory(); }); }}>Close session</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </div>;
}
