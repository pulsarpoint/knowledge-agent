import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';
import { Code2, Copy, History, MessageSquarePlus, Pencil, Plus } from 'lucide-react';
import { closeSession, getSessionOptions, getSessions, reviewSession, sendMessage, startSession, statusLabels, stopSession, type RunSummary, type SessionDetail, type SessionReview } from '../lib/sessions';
import { defaultSettings, startInput } from '../lib/agentSettings';
import { getAgentConfig, getRunResourceOptions, NO_RUN_RESOURCES, RUN_RESOURCE_KINDS, type RunResourceOptions, type RunResourcesInput } from '../lib/profiles';
import { runPath } from '../lib/runs';
import type { Turn } from '../lib/turns';
import { useFillViewport } from '../lib/useFillViewport';
import { useSessionEvents, useSessionNotice } from '../lib/useSessionEvents';
import Markdown from './Markdown';
import RunWorkspace from './RunWorkspace';
import SuggestionApply from './SuggestionApply';
import { Alert, AlertDescription } from './ui/alert';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from './ui/alert-dialog';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Checkbox } from './ui/checkbox';
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from './ui/field';
import { Separator } from './ui/separator';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from './ui/sheet';
import { Spinner } from './ui/spinner';
import { Textarea } from './ui/textarea';

/** The names of what a per-run session was given, from its pinned configuration. */
function runSummary(config: Record<string, unknown>) {
  const listed = (section: string, list: string) =>
    ((config[section] as Record<string, unknown> | undefined)?.[list] as { name: string }[] | undefined) ?? [];
  const names = [...listed('code', 'projects'), ...listed('documents', 'collections'), ...listed('ssh', 'servers'),
    ...listed('services', 'items')].map(item => item.name);
  return names.length ? names.join(', ') : 'nothing';
}

type Panel = 'history' | 'config' | 'feedback' | 'resources' | null;

/** The agent Test tab: Test sessions on the run workspace. The first message creates a session with the agent's
 * current configuration (a per-run agent's narrowed to the resources chosen on the Resources line), pinned for the
 * whole session; the agent session worker runs it. History, Show config, feedback with Suggestions, Review and apply. */
// Temporary: Task 5 rewrites this tab on the saved-agent settings; until then a test session starts through /api/sessions.
async function createTestSession(agentId: string, message: string, runResources?: RunResourcesInput): Promise<SessionDetail> {
  const options = await getSessionOptions({ agentId });
  const base = defaultSettings(options);
  const settings = { ...base, chosen: { ...base.chosen, ...(runResources ?? {}) } };
  return startSession(startInput({ kind: 'agent', id: agentId, name: options.agent?.name ?? agentId }, options, settings, message));
}

export default function AgentTestView({ agentId, agentName, available, ready, dirty, active, revision, onAgentChanged }: { agentId: string; agentName: string; available: boolean; ready: boolean; dirty: boolean; active: boolean; revision?: number; onAgentChanged?: () => void }) {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const sessionId = params.get('session_id');
  const run = useSessionEvents(sessionId);
  const { session, setSession } = run;
  const foreign = session !== null && (session.agent_id !== agentId || (session.purpose !== 'test' && session.purpose !== 'event'));
  const [message, setMessage] = useState('');
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');  // Copy suggestions and Review and apply; the send/Stop notice is below
  const [panel, setPanel] = useState<Panel>(null);
  const [feedbackTurn, setFeedbackTurn] = useState<number | null>(null);
  const [applying, setApplying] = useState<SessionReview | null>(null);
  const [closing, setClosing] = useState(false);
  const [history, setHistory] = useState<RunSummary[] | null>(null);
  const [historyError, setHistoryError] = useState('');
  const [config, setConfig] = useState<Record<string, unknown> | null>(null);
  const [configError, setConfigError] = useState('');
  const [runOptions, setRunOptions] = useState<RunResourceOptions | null>(null);
  const [runOptionsError, setRunOptionsError] = useState('');
  const [runOptionsRetry, setRunOptionsRetry] = useState(0);
  const [runResources, setRunResources] = useState<RunResourcesInput>(NO_RUN_RESOURCES);
  const perRun = runOptions?.resources === 'per_run';
  const { notice, clear, stopping, sent } = useSessionNotice(session?.status);
  const fill = useFillViewport<HTMLDivElement>();

  useEffect(() => {
    let live = true; setRunOptionsError('');  // the old options stay visible while these load
    getRunResourceOptions(agentId).then(value => {
      if (!live) return;
      setRunOptions(value);
      // keep only the choices the new options still offer
      setRunResources(current => Object.fromEntries(RUN_RESOURCE_KINDS.map(([option, key]) => {
        const offered = new Set((value[option] ?? []).map(item => item.id));
        return [key, current[key].filter(id => offered.has(id))];
      })) as unknown as RunResourcesInput);
    }).catch(reason => { if (live) setRunOptionsError(reason instanceof Error ? reason.message : 'Could not load the resources for this run.'); });
    return () => { live = false; };
  }, [agentId, revision, runOptionsRetry]);
  useEffect(() => {
    setConfig(null); setConfigError('');
    if (panel !== 'config' || !active) return;
    if (session) { setConfig(session.config ?? {}); return; }
    if (dirty) return;
    let live = true;
    getAgentConfig(agentId).then(value => { if (live) setConfig(value); })
      .catch(reason => { if (live) setConfigError(reason instanceof Error ? reason.message : 'Could not load the configuration.'); });
    return () => { live = false; };
  }, [panel, agentId, active, dirty, revision, session?.id]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (panel !== 'history') return;
    let live = true; setHistory(null); setHistoryError('');
    getSessions({ agentId }).then(value => { if (live) setHistory(value.sessions); })
      .catch(reason => { if (live) setHistoryError(reason instanceof Error ? reason.message : 'Could not load the sessions.'); });
    return () => { live = false; };
  }, [panel, agentId]);

  function selectSession(id: string | null) {
    setError(''); clear(); setInfo(''); setMessage(''); setComment(''); setFeedbackTurn(null); setPanel(null); setRunResources(NO_RUN_RESOURCES);
    setParams(current => { current.set('view', 'test'); if (id) current.set('session_id', id); else current.delete('session_id'); return current; });
  }
  async function act(action: () => Promise<void>) {
    setBusy(true); setError(''); clear(); setInfo('');
    try { await action(); } catch (reason) { setError(reason instanceof Error ? reason.message : 'The operation failed.'); }
    finally { setBusy(false); }
  }
  const send = () => act(async () => {
    const text = message.trim();
    if (!session) {
      const created = await createTestSession(agentId, text, perRun ? runResources : undefined);
      setMessage('');
      setParams(current => { current.set('view', 'test'); current.set('session_id', created.id); return current; });
      return;
    }
    const result = await sendMessage(session.id, text);
    setMessage('');
    sent(result.working);
  });
  const stop = () => act(async () => { if (session) { await stopSession(session.id); stopping(); } });

  const reviews = session?.reviews ?? [];
  const closed = session?.status === 'closed';
  const working = session?.status === 'starting' || session?.status === 'working';
  const chosen = RUN_RESOURCE_KINDS.flatMap(([option, key]) => (runOptions?.[option] ?? [])
    .filter(item => runResources[key].includes(item.id)).map(item => item.name));
  const resources = session ? (session.config && 'run_resources' in session.config ? `Per run: ${runSummary(session.config)}` : 'Fixed')
    : perRun ? `Per run: ${chosen.length ? chosen.join(', ') : 'nothing'}` : 'Fixed';
  const blockReason = !available ? 'This agent is disabled. Enable it in General to run a task.'
    : !ready ? 'Loading agent configuration…'
    : dirty ? 'Save or discard changes in the configuration tabs before starting a task.'
    : sessionId && !session ? (run.error ? 'Could not open this session. Start a new session.' : 'Loading conversation…')
    : closed ? 'This session is closed. Start a new session to continue.'
    : !sessionId && runOptionsError ? runOptionsError
    : !sessionId && !runOptions ? 'Loading the resources for this run…'
    : null;

  if (foreign) return <div className="flex flex-col gap-3">
    <Alert variant="destructive"><AlertDescription>Session not found for this agent.</AlertDescription></Alert>
    <Button variant="outline" size="sm" className="self-start" onClick={() => selectSession(null)}><Plus data-icon="inline-start" />New session</Button>
  </div>;

  const answerActions = (turn: Turn) => session && <>
    <Button variant="ghost" size="sm" asChild><Link to={runPath(`cc:${session.id}:${turn.turn}`)}>Full record</Link></Button>
    <Button variant="ghost" size="sm" disabled={busy || working} onClick={() => {
      setFeedbackTurn(turn.turn); setComment(''); setPanel('feedback'); setError('');
    }}><MessageSquarePlus data-icon="inline-start" />Add feedback</Button>
  </>;
  const shownReviews = reviews.filter(review => feedbackTurn === null || !review.response_turn || review.response_turn === feedbackTurn);
  const preview = session ? { prompt: message, config: config ?? {}, ...(run.toolCounts ? { tool_counts: run.toolCounts } : {}) }
    : perRun ? { prompt: message, config, run_resources: runResources } : { prompt: message, config };

  // The tab fills the screen below the agent page's header, title and tabs and never grows past it, so the message box
  // stays visible; the main pane scrolls on its own.
  return <div ref={fill.ref} style={fill.style} className="flex min-w-0 flex-col gap-3">
    {error && panel !== 'feedback' && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {!sessionId && runOptionsError && <Alert variant="destructive"><AlertDescription className="flex flex-wrap items-center gap-2">
      <span>{runOptionsError}</span><Button variant="outline" size="sm" onClick={() => setRunOptionsRetry(count => count + 1)}>Retry</Button>
    </AlertDescription></Alert>}
    <div className="min-h-0 flex-1"><RunWorkspace run={run} agentName={agentName} resources={resources} notice={notice || info}
      heading={session ? <p className="truncate text-sm font-medium">{session.title}</p> : undefined}
      headerActions={<>
        <Button variant="ghost" size="sm" onClick={() => setPanel('history')}><History data-icon="inline-start" />History</Button>
        <Button variant="ghost" size="sm" disabled={dirty && !session} onClick={() => setPanel('config')}><Code2 data-icon="inline-start" />Show config</Button>
        <Button variant="outline" size="sm" disabled={busy} onClick={() => selectSession(null)}><Plus data-icon="inline-start" />New session</Button>
        {session && !closed && <Button variant="ghost" size="sm" disabled={busy} onClick={() => setClosing(true)}>Close session</Button>}
      </>}
      sidebar={session && <section aria-label="Suggestions" className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold">Test</h2>
        <Button variant="outline" size="sm" className="self-start" disabled={!reviews.length} onClick={() => { setFeedbackTurn(null); setPanel('feedback'); }}>Suggestions ({reviews.length})</Button>
      </section>}
      answerActions={answerActions}
      empty={<div className="m-auto flex max-w-md flex-col items-center gap-2 text-center">
        <h3 className="font-medium">Chat with {agentName}</h3>
        <p className="text-sm text-muted-foreground">Ask a question, give a task, or explore an idea together.</p>
        {perRun && !session && <p aria-label="Resources for this run" className="flex items-center gap-1 text-sm">
          Resources: {chosen.length ? chosen.join(', ') : 'nothing'}
          <Button variant="ghost" size="sm" aria-label="Choose resources" disabled={busy} onClick={() => setPanel('resources')}><Pencil /></Button></p>}
      </div>}
      composer={{ value: message, onChange: setMessage, onSend: () => void send(), onStop: session ? () => void stop() : undefined,
        busy, blockedReason: blockReason, label: 'Message', placeholder: 'Ask a question or give a task…', sendLabel: session ? 'Send message' : 'Run task' }} /></div>

    <Sheet open={panel !== null} onOpenChange={open => { if (!open) setPanel(null); }}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle>{panel === 'history' ? 'Test sessions' : panel === 'config' ? 'Configuration preview' : panel === 'resources' ? 'Resources for this run'
            : feedbackTurn !== null ? 'Improve this response' : 'Saved suggestions'}</SheetTitle>
          <SheetDescription>{panel === 'history' ? 'Reopen a session to read it or continue it.'
            : panel === 'config' ? 'The configuration for this session and your next message. Secrets are credential references.'
            : panel === 'resources' ? 'The agent works only with what you choose; fixed for the session.'
            : 'Optional feedback for the reviewer. It reads the whole session and its configuration; changes are never applied automatically.'}</SheetDescription>
        </SheetHeader>
        <div className="mt-6 flex flex-col gap-6">
          {panel === 'config' && <>
            <p className="text-xs text-muted-foreground">{session ? 'Saved session configuration' : 'Current agent configuration'}</p>
            {configError ? <Alert variant="destructive"><AlertDescription>{configError}</AlertDescription></Alert>
              : config ? <pre aria-label="Agent configuration" tabIndex={0} className="overflow-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify(preview, null, 2)}</pre>
              : <p role="status"><Spinner />Loading configuration…</p>}
          </>}
          {panel === 'history' && (historyError ? <Alert variant="destructive"><AlertDescription>{historyError}</AlertDescription></Alert>
            : history === null ? <p role="status"><Spinner />Loading sessions…</p>
            : history.length ? history.map(saved => <div key={saved.id} className="flex items-center justify-between gap-3">
              <div className="min-w-0">{saved.purpose === 'event' && <Badge variant="outline">Event</Badge>}<p className="break-words text-sm">{saved.title}</p>
                <p className="text-xs text-muted-foreground">{statusLabels[saved.status]} · {saved.started_by} · {new Date(saved.created_at).toLocaleString()}</p></div>
              <Button variant="outline" size="sm" onClick={() => selectSession(saved.id)}>Open session</Button>
            </div>) : <p className="text-sm text-muted-foreground">No Test sessions yet.</p>)}
          {panel === 'resources' && <div className="flex flex-col gap-4">
            {RUN_RESOURCE_KINDS.map(([option, key, legend]) => {
              const items = runOptions?.[option];
              if (!items) return null;  // a kind the agent does not use
              return <FieldSet key={key} className="gap-1"><FieldLegend variant="label" className="mb-1">{legend}</FieldLegend>
                {items.length === 0 && <FieldDescription>None allowed. Choose them in the agent’s tabs.</FieldDescription>}
                <div className="flex flex-wrap gap-x-4 gap-y-1">{items.map(item => <Field key={item.id} orientation="horizontal" className="w-auto">
                  <Checkbox id={`run-${key}-${item.id}`} checked={runResources[key].includes(item.id)}
                    onCheckedChange={checked => setRunResources(current => ({ ...current,
                      [key]: checked ? [...current[key], item.id] : current[key].filter(id => id !== item.id) }))} />
                  <FieldLabel htmlFor={`run-${key}-${item.id}`}>{item.name}</FieldLabel></Field>)}</div>
              </FieldSet>;
            })}
            {chosen.length === 0 && <p className="text-xs text-muted-foreground">Nothing chosen: the agent’s tools will have nothing to work on</p>}
          </div>}
          {panel === 'feedback' && <>
            {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
            {feedbackTurn !== null && session && <form onSubmit={event => { event.preventDefault(); if (comment.trim() && !busy && !working) void act(async () => {
              const review = await reviewSession(session.id, comment, feedbackTurn);
              setSession(current => current && { ...current, reviews: [...(current.reviews ?? []), review] }); setComment('');
            }); }}>
              <FieldGroup><Field><FieldLabel htmlFor="agent-feedback">Your feedback</FieldLabel>
                <Textarea id="agent-feedback" rows={4} maxLength={20000} disabled={busy} value={comment} onChange={event => setComment(event.target.value)} placeholder="What should the agent do differently or remember?" />
                <FieldDescription>{`About the answer of turn ${feedbackTurn}. Describe what a better response would do.`}</FieldDescription></Field>
                <Button className="self-start" disabled={busy || working || !comment.trim()}>{busy && <Spinner data-icon="inline-start" />}Suggest improvements</Button>
              </FieldGroup>
            </form>}
            {shownReviews.map(review => <div key={review.id} className="flex flex-col gap-4">
              <Separator /><p className="text-xs text-muted-foreground">{new Date(review.created_at).toLocaleString()}{review.response_turn ? ` · turn ${review.response_turn}` : ''}</p>
              <p className="whitespace-pre-wrap text-sm"><strong>Your feedback:</strong> {review.comment}</p><Markdown content={review.suggestion} />
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" size="sm" onClick={() => void act(async () => { await navigator.clipboard.writeText(review.suggestion); setInfo('Suggestions copied.'); })}><Copy data-icon="inline-start" />Copy suggestions</Button>
                <Button size="sm" onClick={() => setApplying(review)}>Review and apply</Button>
              </div>
            </div>)}
          </>}
        </div>
      </SheetContent>
    </Sheet>
    <AlertDialog open={closing} onOpenChange={setClosing}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Close this session?</AlertDialogTitle>
      <AlertDialogDescription>The session and its suggestions remain available in History. A running turn stops, and further messages need a new session.</AlertDialogDescription></AlertDialogHeader>
      <AlertDialogFooter><AlertDialogCancel disabled={busy}>Keep session</AlertDialogCancel><AlertDialogAction disabled={busy} onClick={event => { event.preventDefault(); if (session) void act(async () => {
        await closeSession(session.id); setSession(current => current && { ...current, status: 'closed' }); setClosing(false);
      }); }}>Close session</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
    <SuggestionApply agentId={agentId} review={applying} onClose={() => setApplying(null)} onApplied={(target, id) => {
      setApplying(null); setPanel(null);
      if (target === 'clone') navigate(`/agents/${encodeURIComponent(id)}?view=test`);
      else { setInfo('Applied to this agent. New conversations use it.'); onAgentChanged?.(); }
    }} />
  </div>;
}
