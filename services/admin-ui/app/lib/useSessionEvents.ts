import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import { getSession, getSessionHistory, sessionEventsUrl, type SessionDetail, type SessionEvent, type SessionState, type SessionStatus } from './generations';
import { withLaterCalls, type ToolCount } from './runs';

export type OlderState = 'more' | 'loading' | 'start';
export interface SessionRun {
  session: SessionDetail | null;
  setSession: Dispatch<SetStateAction<SessionDetail | null>>;
  events: SessionEvent[];
  older: OlderState;
  /** Load the page before the oldest event; `beforePrepend` runs just before it is added (keep the scroll position). */
  loadOlder: (beforePrepend?: () => void) => Promise<void>;
  /** The loaded counts plus the live tool calls after the last one they include. */
  toolCounts: ToolCount[] | undefined;
  reconnecting: boolean;
  error: string;
  setError: Dispatch<SetStateAction<string>>;
}

/** One session's events for a page: the latest page first, then the live stream after its newest event (nothing shown
 * twice or skipped: event ids increase, so anything at or below the last one is a replay after a reconnect), earlier
 * pages on demand (one at a time, aborted when the page closes), the session state from the stream, and live tool
 * counts. Without a session id it loads nothing. Leaving closes only this stream; the session keeps running. */
export function useSessionEvents(sessionId: string | null): SessionRun {
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [events, setEvents] = useState<SessionEvent[]>([]);
  // Tool calls of the opening page and the stream (never earlier pages), by event id: a replay is counted once.
  const [liveCalls, setLiveCalls] = useState<Map<number, string>>(() => new Map());
  const [older, setOlder] = useState<OlderState>('more');
  const [reconnecting, setReconnecting] = useState(false);
  const [error, setError] = useState('');
  const loadingOlder = useRef<AbortController | null>(null);
  const oldest = useRef<SessionEvent | undefined>(undefined);
  oldest.current = events[0];
  const olderState = useRef<OlderState>(older);
  olderState.current = older;

  useEffect(() => {
    setSession(null); setEvents([]); setLiveCalls(new Map()); setError(''); setReconnecting(false); setOlder('more');
    if (!sessionId) return;
    const controller = new AbortController();
    let streamed: SessionState | null = null;  // newer than a page load that finishes after it
    getSession(sessionId, controller.signal).then(detail => setSession(streamed
      ? { ...detail, ...streamed, tool_counts: detail.tool_counts, tool_counts_through_id: detail.tool_counts_through_id } : detail))
      .catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not open the session'); });
    let stream: EventSource | null = null;
    let lastId = 0, frame = 0;
    let pending: SessionEvent[] = [];
    const addCalls = (batch: SessionEvent[]) => {
      const found = batch.filter(event => event.kind === 'tool_call');
      if (found.length) setLiveCalls(current => {
        const next = new Map(current);
        for (const event of found) next.set(event.id, String(event.payload.name ?? ''));
        return next;
      });
    };
    // New events are appended once per animation frame, so a burst renders in a few frames.
    const flush = () => { frame = 0; const batch = pending; pending = []; setEvents(current => [...current, ...batch]); addCalls(batch); };
    getSessionHistory(sessionId, undefined, controller.signal).then(page => {
      if (controller.signal.aborted) return;
      lastId = page.events.length ? page.events[page.events.length - 1].id : 0;
      setEvents(page.events);
      addCalls(page.events);
      setOlder(page.has_more ? 'more' : 'start');
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
        setSession(current => current && { ...current, ...next, tool_counts: current.tool_counts,
          tool_counts_through_id: current.tool_counts_through_id });
      });
      source.addEventListener('open', () => setReconnecting(false));
      source.addEventListener('error', () => { if (source.readyState !== 2) setReconnecting(true); });
      source.addEventListener('closed', () => { setReconnecting(false); source.close(); });
      source.addEventListener('unavailable', () => setError('The session stream is unavailable. Reload the page.'));
    }).catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not load the session activity'); });
    return () => {
      controller.abort(); loadingOlder.current?.abort(); loadingOlder.current = null;
      stream?.close(); if (frame) cancelAnimationFrame(frame);
    };
  }, [sessionId]);

  const loadOlder = useCallback(async (beforePrepend?: () => void) => {
    const first = oldest.current;
    if (!sessionId || !first || olderState.current !== 'more' || loadingOlder.current) return;
    const controller = new AbortController();
    loadingOlder.current = controller;
    olderState.current = 'loading';
    setOlder('loading');
    try {
      const page = await getSessionHistory(sessionId, first.id, controller.signal);
      if (controller.signal.aborted) return;
      beforePrepend?.();
      setEvents(current => [...page.events.filter(event => event.id < (current[0]?.id ?? Infinity)), ...current]);
      setOlder(page.has_more ? 'more' : 'start');
    } catch (e) {
      if (controller.signal.aborted) return;
      setOlder('more');
      setError(e instanceof Error ? e.message : 'Could not load earlier activity');
    } finally {
      if (loadingOlder.current === controller) loadingOlder.current = null;
    }
  }, [sessionId]);

  const toolCounts = session?.tool_counts && withLaterCalls(session.tool_counts, session.tool_counts_through_id,
    [...liveCalls].map(([id, name]) => ({ id, name })));
  return { session, setSession, events, older, loadOlder, toolCounts, reconnecting, error, setError };
}

export const STOPPING = 'Stopping the current turn…';

/** The notice after a message was sent: an agent reads comments before its next model call. */
export const sentNotice = (working: boolean) =>
  !working ? 'Sent. The agent starts a new turn with your message.' : 'Queued. Your comment reaches the agent at its next step.';

/** The send/Stop notice of a session page, shared by the session page and the Test tab: `stopping()` and
 * `sent(working)` set it, `clear()` empties it (call before a new request), and the next status change
 * (the answer to Stop) clears the Stopping notice but leaves other notices alone. */
export function useSessionNotice(status: SessionStatus | undefined) {
  const [notice, setNotice] = useState('');
  const previous = useRef(status);
  useEffect(() => {
    if (previous.current !== status) setNotice(current => current === STOPPING ? '' : current);
    previous.current = status;
  }, [status]);
  const clear = useCallback(() => setNotice(''), []);
  const stopping = useCallback(() => setNotice(STOPPING), []);
  const sent = useCallback((working: boolean) => setNotice(sentNotice(working)), []);
  return { notice, clear, stopping, sent };
}
