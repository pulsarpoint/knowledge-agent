import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { ApiError } from '~/lib/api';
import { closeSession, formatTokens, getSession, getSessionHistory, saveSessionDocument, sendMessage, sessionEventsUrl, statusLabels,
  stopSession, type SavedDocument, type SessionDetail, type SessionEvent, type SessionState } from '~/lib/claudeCode';
import { SAME_DOCUMENT_NAME } from '~/lib/documents';
import Markdown from './Markdown';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle } from '~/components/ui/alert-dialog';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Field, FieldLabel } from '~/components/ui/field';
import { Skeleton } from '~/components/ui/skeleton';
import { Textarea } from '~/components/ui/textarea';

function EventLine({ event }: { event: SessionEvent }) {
  const payload = event.payload as Record<string, unknown>;
  const text = (key: string) => String(payload[key] ?? '');
  switch (event.kind) {
    case 'input': return <div className="rounded-md bg-muted p-3"><p className="text-xs font-medium text-muted-foreground">You · turn {event.turn}</p>
      <p className="line-clamp-6 whitespace-pre-wrap text-sm">{text('task')}</p></div>;
    case 'message': return <p className="whitespace-pre-wrap text-sm">{text('text')}</p>;
    case 'reasoning': return <p className="whitespace-pre-wrap text-sm italic text-muted-foreground">{text('text')}</p>;
    case 'tool_call': return <div className="text-sm"><Badge variant="outline" className="mr-2">{text('name')}</Badge><code className="break-all">{text('summary')}</code></div>;
    case 'tool_result': return payload.preview ? <pre className={`max-h-48 overflow-auto rounded-md border p-2 text-xs${payload.is_error ? ' text-destructive' : ''}`}>{text('preview')}</pre> : null;
    case 'error': return <p className="text-sm text-destructive">{text('message')}</p>;
    case 'end': return <p className="text-xs text-muted-foreground">Turn {event.turn} {text('status')}</p>;
    default: return null;
  }
}

const STOPPING = 'Stopping the current turn…';
const NEAR_TOP = 80;  // px: scrolling this close to the top of Activity loads the previous page
const NEAR_BOTTOM = 40;  // px: this close to the bottom, Activity follows new events

/** One generation: the agent's activity (the latest page, earlier pages as you scroll up, live events after it),
 * messages, Stop, the draft, Save document and Close. The page fits the screen; Activity and Draft scroll on their own. */
export default function ClaudeCodeSessionView({ sessionId }: { sessionId: string }) {
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [older, setOlder] = useState<'more' | 'loading' | 'start'>('more');
  const [following, setFollowing] = useState(true);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [saved, setSaved] = useState<SavedDocument | null>(null);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [reconnecting, setReconnecting] = useState(false);
  const activity = useRef<HTMLDivElement>(null);
  const anchor = useRef<number | null>(null);  // Activity's scroll height before an earlier page was added above
  const status = session?.status;
  const previousStatus = useRef(status);
  useEffect(() => {  // a new status answers the Stop request
    if (previousStatus.current !== status) setNotice(current => current === STOPPING ? '' : current);
    previousStatus.current = status;
  }, [status]);

  useEffect(() => {
    const controller = new AbortController();
    setSession(null); setEvents([]); setError(''); setReconnecting(false); setOlder('more'); setFollowing(true);
    let streamed: SessionState | null = null;  // newer than a page load that finishes after it
    getSession(sessionId, controller.signal).then(detail => setSession(streamed ? { ...detail, ...streamed } : detail))
      .catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not open the session'); });
    let stream: EventSource | null = null;
    let lastId = 0, frame = 0;
    let pending: SessionEvent[] = [];
    const flush = () => { frame = 0; const batch = pending; pending = []; setEvents(current => [...current, ...batch]); };
    // The latest page first, then the live stream after its newest event: nothing is shown twice or skipped. Event ids
    // increase, so anything at or below the last one is a replay after a reconnect. New events are appended once per
    // animation frame, so a burst renders in a few frames.
    getSessionHistory(sessionId, undefined, controller.signal).then(page => {
      if (controller.signal.aborted) return;
      lastId = page.events.length ? page.events[page.events.length - 1].id : 0;
      setEvents(page.events);
      setOlder(page.has_more ? 'more' : 'start');
      // Leaving the page closes only this stream; the session keeps running.
      const source = new EventSource(sessionEventsUrl(sessionId, lastId));
      stream = source;
      source.addEventListener('run_event', e => {
        setReconnecting(false);
        const event = JSON.parse((e as MessageEvent).data) as SessionEvent;
        if (event.id <= lastId) return;
        lastId = event.id;
        pending.push(event);
        if (!frame) frame = requestAnimationFrame(flush);
      });
      source.addEventListener('session', e => {
        setReconnecting(false);
        const next = JSON.parse((e as MessageEvent).data) as SessionState;
        streamed = next;
        setSession(current => current && { ...current, ...next });
      });
      source.addEventListener('open', () => setReconnecting(false));
      source.addEventListener('error', () => { if (source.readyState !== 2) setReconnecting(true); });
      source.addEventListener('closed', () => { setReconnecting(false); source.close(); });
      source.addEventListener('unavailable', () => setError('The session stream is unavailable. Reload the page.'));
    }).catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not load the session activity'); });
    return () => { controller.abort(); stream?.close(); if (frame) cancelAnimationFrame(frame); };
  }, [sessionId]);

  useLayoutEffect(() => {
    const panel = activity.current;
    if (!panel) return;
    if (anchor.current !== null) {  // an earlier page went above: keep what the reader was looking at in place
      panel.scrollTop += panel.scrollHeight - anchor.current;
      anchor.current = null;
    } else if (following) {
      panel.scrollTop = panel.scrollHeight;
    }
  }, [events, following]);

  async function loadOlder() {
    const first = events[0];
    if (!first || older !== 'more') return;
    setOlder('loading');
    try {
      const page = await getSessionHistory(sessionId, first.id);
      anchor.current = activity.current?.scrollHeight ?? null;
      setEvents(current => [...page.events.filter(event => event.id < (current[0]?.id ?? Infinity)), ...current]);
      setOlder(page.has_more ? 'more' : 'start');
    } catch (e) {
      setOlder('more');
      setError(e instanceof Error ? e.message : 'Could not load earlier activity');
    }
  }
  function scrolled() {
    const panel = activity.current;
    if (!panel) return;
    setFollowing(panel.scrollHeight - panel.scrollTop - panel.clientHeight <= NEAR_BOTTOM);
    if (panel.scrollTop <= NEAR_TOP) void loadOlder();
  }
  function jump() {
    setFollowing(true);
    const panel = activity.current;
    if (panel) panel.scrollTop = panel.scrollHeight;
  }

  async function run(action: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('');
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : 'The request failed'); } finally { setBusy(false); }
  }
  const send = () => run(async () => {
    const result = await sendMessage(sessionId, message.trim());
    setMessage('');
    setNotice(result.working ? 'Queued. The agent gets your message when it finishes its current response.' : 'Sent. The agent starts a new turn with your message.');
  });
  const stop = () => run(async () => { await stopSession(sessionId); setNotice(STOPPING); });
  const close = () => run(async () => { await closeSession(sessionId); setSession(current => current && { ...current, status: 'closed' }); });
  async function save(updateExisting: boolean) {
    setBusy(true); setError(''); setNotice('');
    try {
      const result = await saveSessionDocument(sessionId, updateExisting);
      setConfirming(false); setSaved(result);
      setSession(current => current && { ...current, document_id: result.id });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.message === SAME_DOCUMENT_NAME && !updateExisting) setConfirming(true);
      else { setConfirming(false); setError(e instanceof Error ? e.message : 'Could not save the document'); }
    } finally { setBusy(false); }
  }

  if (!session) return error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : <Skeleton className="h-60" />;
  const closed = session.status === 'closed';
  // Header 4rem + main padding 3rem: the page never grows past the screen, so the message box stays visible.
  return <div className="flex h-[calc(100dvh-7rem)] min-h-[36rem] flex-col gap-3">
    <div className="flex shrink-0 flex-col gap-2">
      <Link className="underline" to="/documentation/generations">Generate documents</Link>
      <div className="flex flex-wrap items-center gap-3"><h1 className="text-2xl font-semibold">{session.title}</h1>
        <Badge variant={session.status === 'failed' ? 'destructive' : 'secondary'}>{statusLabels[session.status]}</Badge></div>
      <p className="text-sm text-muted-foreground">{session.agent_name} · {session.model_name} · {session.folder}/</p>
      <p className="text-sm">{`In ${formatTokens(session.input_tokens)} · Out ${formatTokens(session.output_tokens)} · Cached ${formatTokens(session.cached_tokens)}`}</p>
      {session.status === 'starting' && !session.worker_active && <Alert><AlertDescription>Waiting for the Claude Code worker. The session starts when the worker is running.</AlertDescription></Alert>}
      {session.status_message && ['failed', 'stopped'].includes(session.status) && <Alert variant={session.status === 'failed' ? 'destructive' : 'default'}><AlertDescription>{session.status_message}</AlertDescription></Alert>}
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      {reconnecting && !closed && <Alert><AlertDescription role="status">Reconnecting…</AlertDescription></Alert>}
      {notice && <Alert><AlertDescription role="status">{notice}</AlertDescription></Alert>}
      {saved && <Alert><AlertDescription>Saved as version {saved.revision} in {saved.folder}. <Link className="underline" to={`/documents/${saved.id}`}>Open document</Link>{saved.export_error ? ` ${saved.export_error}` : ''}</AlertDescription></Alert>}
    </div>
    <div className="grid min-h-0 flex-1 grid-rows-2 gap-3 lg:grid-cols-2 lg:grid-rows-1">
      <section aria-label="Agent activity" className="flex min-h-0 min-w-0 flex-col rounded-lg border">
        <h2 className="shrink-0 border-b px-4 py-2 font-semibold">Activity</h2>
        <div ref={activity} data-testid="activity-scroll" onScroll={scrolled} className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
          {older === 'start' && <p className="text-center text-xs text-muted-foreground">Beginning of the session</p>}
          {older === 'loading' && <p role="status" className="text-center text-xs text-muted-foreground">Loading earlier activity…</p>}
          {events.length === 0 && <p className="text-sm text-muted-foreground">The agent's messages, thinking and tool calls appear here.</p>}
          {events.map(event => <div key={event.id} data-event><EventLine event={event} /></div>)}
          {session.queued > 0 && <p className="text-sm text-muted-foreground">{session.queued} message{session.queued === 1 ? '' : 's'} queued</p>}
        </div>
        {!following && <div className="shrink-0 border-t p-2 text-center"><Button type="button" size="sm" variant="secondary" onClick={jump}>Jump to latest</Button></div>}
      </section>
      <section aria-label="Draft" className="flex min-h-0 min-w-0 flex-col rounded-lg border">
        <h2 className="shrink-0 border-b px-4 py-2 font-semibold">Draft{session.draft_turn ? ` · turn ${session.draft_turn}` : ''}</h2>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {session.draft ? <Markdown content={session.draft} /> : <p className="text-sm text-muted-foreground">The draft appears when the agent finishes a turn.</p>}
        </div>
      </section>
    </div>
    <form className="flex shrink-0 flex-col gap-2" onSubmit={e => { e.preventDefault(); void send(); }}>
      <Field><FieldLabel htmlFor="session-message">Message</FieldLabel><Textarea id="session-message" rows={2} maxLength={20000} disabled={closed} value={message} onChange={e => setMessage(e.target.value)} placeholder="For example: shorten the API section" /></Field>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy || closed || !message.trim()}>Send message</Button>
        <Button type="button" variant="outline" disabled={busy || session.status !== 'working'} onClick={() => void stop()}>Stop</Button>
        <Button type="button" variant="outline" disabled={busy || !session.draft} onClick={() => void save(false)}>Save document</Button>
        <Button type="button" variant="outline" disabled={busy || closed} onClick={() => void close()}>Close session</Button>
      </div>
    </form>
    <AlertDialog open={confirming} onOpenChange={setConfirming}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Save a new version?</AlertDialogTitle>
      <AlertDialogDescription>{`${session.folder} already has a document named “${session.document_name}”. Saving adds a new version of it; earlier versions stay available.`}</AlertDialogDescription></AlertDialogHeader>
      <AlertDialogFooter><AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel><AlertDialogAction disabled={busy} onClick={e => { e.preventDefault(); void save(true); }}>Save new version</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </div>;
}
