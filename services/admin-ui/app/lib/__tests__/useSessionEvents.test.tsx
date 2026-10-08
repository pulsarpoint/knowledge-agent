import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import type { SessionStatus } from '../claudeCode';
import { sentNotice, STOPPING, useSessionEvents, useSessionNotice } from '../useSessionEvents';

class Stream extends EventTarget {
  static instances: Stream[] = [];
  close = vi.fn();
  readyState = 1;
  constructor(public url: string) { super(); Stream.instances.push(this); }
  emit(type: string, value: unknown) { this.dispatchEvent(new MessageEvent(type, { data: JSON.stringify(value) })); }
}
beforeEach(() => { Stream.instances = []; vi.stubGlobal('EventSource', Stream); });
afterEach(() => vi.unstubAllGlobals());
const state = { status: 'starting', status_message: '', turns: 0, input_tokens: 0, output_tokens: 0, cached_tokens: 0, cost_usd: 0,
  model_calls: 0, reasoning_tokens: 0, draft: null, draft_turn: null, document_id: null, worker_active: false, queued: 1 };
const detail = { ...state, id: 's1', title: 'Payments', purpose: 'documents', tool_counts: [{ name: 'Read', calls: 5 }], tool_counts_through_id: 10 };
const message = (id: number) => ({ id, turn: 1, seq: id, kind: 'message', payload: { text: `Message ${id}` } });
const call = (id: number, name: string) => ({ id, turn: 1, seq: id, kind: 'tool_call', payload: { name, summary: `call ${id}` } });
const page = (events: unknown[], has_more = false) => Response.json({ events, has_more });
const opened = () => waitFor(() => { expect(Stream.instances).toHaveLength(1); return Stream.instances[0]; });
const ids = (events: { id: number }[]) => events.map(event => event.id);

test('without a session it loads nothing', () => {
  const fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
  const { result } = renderHook(() => useSessionEvents(null));
  expect([result.current.session, result.current.events, result.current.toolCounts]).toEqual([null, [], undefined]);
  expect(fetchMock).not.toHaveBeenCalled();
  expect(Stream.instances).toHaveLength(0);
});

test('opens with the latest page and streams after its newest event', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.includes('/events/history') ? page([message(150), message(151)], true) : Response.json(detail)));
  const { result } = renderHook(() => useSessionEvents('s1'));
  const stream = await opened();
  expect(stream.url).toBe('/api/claude-code/sessions/s1/events?after=151');
  expect(ids(result.current.events)).toEqual([150, 151]);
  expect(result.current.older).toBe('more');
  act(() => stream.emit('run_event', message(152)));
  await waitFor(() => expect(ids(result.current.events)).toEqual([150, 151, 152]));
  act(() => stream.emit('session', { ...state, status: 'working', turns: 1, model_calls: 2 }));
  expect(result.current.session).toMatchObject({ status: 'working', model_calls: 2, title: 'Payments' });
});

test('a replayed batch is kept once, a stale session load does not undo the stream, and a reconnect is reported', async () => {
  let resolve: (response: Response) => void = () => {};
  vi.stubGlobal('fetch', vi.fn((url: string) => url.includes('/events/history') ? Promise.resolve(page([]))
    : new Promise<Response>(done => { resolve = done; })));
  const { result } = renderHook(() => useSessionEvents('s1'));
  const stream = await opened();
  act(() => { stream.emit('session', { ...state, status: 'working', turns: 1 }); [1, 2, 3].forEach(id => stream.emit('run_event', message(id))); });
  await act(async () => { resolve(Response.json(detail)); });  // loaded after the stream said working
  await waitFor(() => expect(result.current.session?.status).toBe('working'));
  act(() => { stream.dispatchEvent(new Event('error')); });
  expect(result.current.reconnecting).toBe(true);
  act(() => { [1, 2, 3, 4].forEach(id => stream.emit('run_event', message(id))); });
  await waitFor(() => expect(ids(result.current.events)).toEqual([1, 2, 3, 4]));
  expect(result.current.reconnecting).toBe(false);
});

test('live tool counts: the loaded counts plus streamed calls after the last one counted, replays counted once', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.includes('/events/history') ? page([call(9, 'Read'), call(11, 'Grep')]) : Response.json(detail)));
  const { result } = renderHook(() => useSessionEvents('s1'));
  const stream = await opened();
  await waitFor(() => expect(result.current.toolCounts).toEqual([{ name: 'Read', calls: 5 }, { name: 'Grep', calls: 1 }]));
  act(() => { stream.emit('run_event', call(12, 'Bash')); stream.emit('run_event', call(12, 'Bash')); stream.emit('run_event', call(13, 'Bash')); });
  await waitFor(() => expect(result.current.toolCounts).toEqual([{ name: 'Read', calls: 5 }, { name: 'Bash', calls: 2 }, { name: 'Grep', calls: 1 }]));
});

test('live tool counts: earlier pages are never added; one earlier page loads at a time and leaving aborts it', async () => {
  const older: { url: string; signal?: AbortSignal }[] = [];
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    if (url.includes('before=')) { older.push({ url, signal: init?.signal ?? undefined }); return new Promise<Response>(() => {}); }
    if (url.includes('/events/history')) return Promise.resolve(page([message(150)], true));
    return Promise.resolve(Response.json({ ...detail, tool_counts: [], tool_counts_through_id: 0 }));
  }));
  const { result, unmount } = renderHook(() => useSessionEvents('s1'));
  await opened();
  await waitFor(() => expect(result.current.session).not.toBeNull());
  const before = vi.fn();
  act(() => { void result.current.loadOlder(before); void result.current.loadOlder(before); });
  expect(older.map(item => item.url)).toEqual(['/api/claude-code/sessions/s1/events/history?before=150&limit=100']);
  expect(result.current.older).toBe('loading');
  expect(result.current.toolCounts).toEqual([]);
  unmount();
  expect(older[0].signal?.aborted).toBe(true);
  expect(before).not.toHaveBeenCalled();
});

test('the session notice: Stop and send notices, cleared by the next status but not by other notices', () => {
  expect(sentNotice(false)).toBe('Sent. The agent starts a new turn with your message.');
  expect(sentNotice(true, 'openai')).toBe('Queued. Your comment reaches the agent at its next step.');
  expect(sentNotice(true, 'claude_code')).toBe('Queued. The agent gets your message when it finishes its current response.');
  const { result, rerender } = renderHook(({ status }) => useSessionNotice(status), { initialProps: { status: 'working' as SessionStatus } });
  act(() => result.current.stopping());
  expect(result.current.notice).toBe(STOPPING);
  rerender({ status: 'working' });
  expect(result.current.notice).toBe(STOPPING);
  rerender({ status: 'waiting' });
  expect(result.current.notice).toBe('');
  act(() => result.current.sent(true, 'openai'));
  rerender({ status: 'working' });
  expect(result.current.notice).toBe('Queued. Your comment reaches the agent at its next step.');
  act(() => result.current.clear());
  expect(result.current.notice).toBe('');
});
