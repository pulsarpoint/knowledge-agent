import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
vi.mock('../Markdown', () => ({ default: ({ content }: { content: string }) => <div data-testid="markdown">{content}</div> }));
import ClaudeCodeSessionView from '../ClaudeCodeSessionView';
import { SAME_DOCUMENT_NAME } from '~/lib/documents';

class Stream extends EventTarget {
  static instances: Stream[] = [];
  close = vi.fn();
  constructor(public url: string) { super(); Stream.instances.push(this); }
  emit(type: string, value: unknown) { this.dispatchEvent(new MessageEvent(type, { data: JSON.stringify(value) })); }
}
beforeEach(() => { Stream.instances = []; vi.stubGlobal('EventSource', Stream); });
afterEach(() => vi.unstubAllGlobals());
const state = { status: 'starting', status_message: '', turns: 0, input_tokens: 0, output_tokens: 0, cached_tokens: 0, cost_usd: 0,
  draft: null, draft_turn: null, document_id: null, worker_active: false, queued: 1 };
const detail = { ...state, id: 's1', title: 'Payments – API reference', document_name: 'Payments – API reference', folder: 'payments-api-docs',
  agent_id: 'writer', agent_name: 'Technical writer', model_id: 'claude', model_name: 'Claude on Bedrock', document_title: null,
  prompt_snapshot: { system_prompt: 'You are an expert technical writer.', claude_code_tools: ['Read'], agent_version: null },
  context_snapshot: { folders: [{ path: '/work/data/code/app', kind: 'code_project', name: 'Payments API', code_project_id: 'repo' }],
    code_project_ids: ['repo'], instructions: '' },
  tool_counts: [{ name: 'Read', calls: 5 }, { name: 'Grep', calls: 2 }],
  inputs: [], created_at: '2026-10-07T10:00:00Z', last_activity_at: '2026-10-07T10:00:00Z', closed_at: null };
const message = (id: number) => ({ id, turn: 1, seq: id, kind: 'message', payload: { text: `Message ${id}` } });
const page = (events: unknown[], has_more = false) => Response.json({ events, has_more });
const opened = () => waitFor(() => { expect(Stream.instances).toHaveLength(1); return Stream.instances[0]; });

test('the session page shows the tools the session used', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.includes('/events/history') ? page([]) : Response.json(detail)));
  render(<MemoryRouter><ClaudeCodeSessionView sessionId="s1" /></MemoryRouter>);
  expect(await screen.findByLabelText('Tools used')).toHaveTextContent('Read 5 · Grep 2');
});

test('the session page streams activity, sends messages, stops, saves the draft and closes', async () => {
  const posts: { url: string; body?: unknown }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes('/events/history')) return page([]);
    if (init?.method !== 'POST') return Response.json(detail);
    const body = init.body ? JSON.parse(init.body as string) : undefined;
    posts.push({ url, body });
    if (url.endsWith('/messages')) return Response.json({ seq: 2, status: 'queued', working: true }, { status: 202 });
    if (url.endsWith('/stop')) return Response.json({ id: 's1', discarded: 0 }, { status: 202 });
    if (url.endsWith('/document')) return body.update_existing
      ? Response.json({ id: 'doc', revision: 2, version_id: 'v2', folder: 'payments-api-docs', output_folder: '/work/data/generated-documents/payments-api-docs/payments-api-reference', export_error: null }, { status: 201 })
      : Response.json({ error: { message: SAME_DOCUMENT_NAME } }, { status: 409 });
    return Response.json({ id: 's1' });
  }));
  render(<MemoryRouter><ClaudeCodeSessionView sessionId="s1" /></MemoryRouter>);
  expect(await screen.findByText('Starting')).toBeVisible();
  expect(screen.getByText(/Waiting for the Claude Code worker/)).toBeVisible();
  const stream = await opened();
  expect(stream.url).toBe('/api/claude-code/sessions/s1/events?after=0');  // nothing yet: the stream starts at the beginning
  expect(screen.getByText('Beginning of the session')).toBeVisible();
  act(() => {
    stream.emit('session', { ...state, status: 'working', turns: 1, worker_active: true, queued: 0 });
    stream.emit('run_event', { id: 1, turn: 1, seq: 3, kind: 'input', payload: { task: 'Document the payments API.' } });
    stream.emit('run_event', { id: 2, turn: 1, seq: 4, kind: 'reasoning', payload: { text: 'Start with the routes.' } });
    stream.emit('run_event', { id: 3, turn: 1, seq: 5, kind: 'tool_call', payload: { name: 'Read', summary: '/work/data/code/app/main.py' } });
    stream.emit('run_event', { id: 4, turn: 1, seq: 6, kind: 'tool_result', payload: { name: 'Bash', preview: 'total 8\ndrwxr-xr-x app', is_error: false } });
    stream.emit('run_event', { id: 4, turn: 1, seq: 6, kind: 'tool_result', payload: { name: 'Bash', preview: 'total 8\ndrwxr-xr-x app', is_error: false } });
  });
  expect(screen.getByText('Working')).toBeVisible();
  expect(screen.queryByText(/Waiting for the Claude Code worker/)).toBeNull();
  const activity = within(screen.getByRole('region', { name: 'Agent activity' }));
  expect(await activity.findByText('Document the payments API.')).toBeVisible();  // appended once per animation frame
  expect(activity.getByText('Start with the routes.')).toBeVisible();
  expect(activity.getByText('/work/data/code/app/main.py')).toBeVisible();
  expect(activity.getAllByText(/drwxr-xr-x app/)).toHaveLength(1);  // a replayed event is shown once
  expect(screen.getByRole('button', { name: 'Save document' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'Also cover retries.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
  expect(await screen.findByText('Queued. The agent gets your message when it finishes its current response.')).toBeVisible();
  expect(screen.getByLabelText('Message')).toHaveValue('');
  fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
  await waitFor(() => expect(posts.map(p => p.url)).toContain('/api/claude-code/sessions/s1/stop'));
  expect(await screen.findByText('Stopping the current turn…')).toBeVisible();
  act(() => {
    stream.emit('run_event', { id: 5, turn: 1, seq: 7, kind: 'message', payload: { text: '# Payments API' } });
    stream.emit('session', { ...state, status: 'waiting', turns: 1, worker_active: true, queued: 0, draft: '# Payments API\n\nRetries back off.',
      draft_turn: 1, input_tokens: 1050, output_tokens: 40, cached_tokens: 900 });
  });
  expect(screen.getByText('Waiting for you')).toBeVisible();
  expect(screen.queryByText('Stopping the current turn…')).toBeNull();  // the status changed: the notice is stale
  expect(within(screen.getByRole('region', { name: 'Draft' })).getByTestId('markdown')).toHaveTextContent('# Payments API Retries back off.');
  expect(screen.getByText('In 1,050 · Out 40 · Cached 900')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Stop' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Save document' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Save new version' }));
  expect(await screen.findByText(/Saved as version 2 in payments-api-docs/)).toBeVisible();
  expect(screen.getByRole('link', { name: 'Open document' })).toHaveAttribute('href', '/documents/doc');
  expect(posts.filter(p => p.url.endsWith('/document')).map(p => p.body)).toEqual([{ update_existing: false }, { update_existing: true }]);
  fireEvent.click(screen.getByRole('button', { name: 'Close session' }));
  await waitFor(() => expect(posts.map(p => p.url)).toContain('/api/claude-code/sessions/s1/close'));
  act(() => { stream.emit('session', { ...state, status: 'closed', turns: 1, draft: '# Payments API\n\nRetries back off.' }); stream.emit('closed', {}); });
  expect(screen.getByText('Finished')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled();
  expect(stream.close).toHaveBeenCalled();
});

test('a replayed batch of events is shown once and a stale session load does not undo the stream', async () => {
  let resolve: (response: Response) => void = () => {};
  vi.stubGlobal('fetch', vi.fn((url: string) => url.includes('/events/history') ? Promise.resolve(page([]))
    : new Promise<Response>(done => { resolve = done; })));
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => setTimeout(() => callback(0), 0));
  vi.stubGlobal('cancelAnimationFrame', (handle: number) => clearTimeout(handle));
  render(<MemoryRouter><ClaudeCodeSessionView sessionId="s1" /></MemoryRouter>);
  const stream = await opened();
  const batch = [1, 2, 3].map(message);
  act(() => {
    stream.emit('session', { ...state, status: 'working', turns: 1, worker_active: true, queued: 0 });
    batch.forEach(event => stream.emit('run_event', event));
  });
  await act(async () => { resolve(Response.json(detail)); });  // loaded after the stream said working: still working
  expect(await screen.findByText('Working')).toBeVisible();
  act(() => {  // the stream reconnects and replays from an earlier id
    stream.dispatchEvent(new Event('error'));
  });
  expect(await screen.findByText('Reconnecting…')).toBeVisible();
  act(() => {
    [...batch, message(4)].forEach(event => stream.emit('run_event', event));
  });
  const activity = within(screen.getByRole('region', { name: 'Agent activity' }));
  expect(await activity.findByText('Message 4')).toBeVisible();
  for (const id of [1, 2, 3, 4]) expect(activity.getAllByText(`Message ${id}`)).toHaveLength(1);
  expect(screen.queryByText('Reconnecting…')).toBeNull();
});

test('opening shows the latest page, streams after its newest event and loads earlier events near the top', async () => {
  const urls: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    urls.push(url);
    if (url.endsWith('/events/history?before=150&limit=100')) return page([message(120)]);
    if (url.endsWith('/events/history?limit=100')) return page([message(150), message(151)], true);
    return Response.json({ ...detail, status: 'waiting', turns: 1, queued: 0 });
  }));
  render(<MemoryRouter><ClaudeCodeSessionView sessionId="s1" /></MemoryRouter>);
  expect(await screen.findByText('Message 151')).toBeVisible();
  const stream = await opened();
  expect(stream.url).toBe('/api/claude-code/sessions/s1/events?after=151');  // the stream continues after the newest
  expect(screen.queryByText('Beginning of the session')).toBeNull();
  const panel = screen.getByTestId('activity-scroll');
  Object.defineProperty(panel, 'scrollHeight', { configurable: true, get: () => panel.querySelectorAll('[data-event]').length * 100 });
  Object.defineProperty(panel, 'clientHeight', { configurable: true, value: 50 });
  Object.defineProperty(panel, 'scrollTop', { configurable: true, writable: true, value: 0 });
  fireEvent.scroll(panel);  // near the top, away from the bottom
  expect(await screen.findByText('Message 120')).toBeVisible();
  expect(screen.getByText('Beginning of the session')).toBeVisible();
  expect(panel.scrollTop).toBe(100);  // the earlier event went above: what was on screen stays in place
  const history = () => urls.filter(url => url.includes('/events/history'));
  expect(history()).toEqual(['/api/claude-code/sessions/s1/events/history?limit=100',
    '/api/claude-code/sessions/s1/events/history?before=150&limit=100']);
  panel.scrollTop = 0;
  fireEvent.scroll(panel);  // the beginning is reached: nothing more to load
  expect(history()).toHaveLength(2);
  fireEvent.click(screen.getByRole('button', { name: 'Jump to latest' }));
  expect(panel.scrollTop).toBe(300);
  expect(screen.queryByRole('button', { name: 'Jump to latest' })).toBeNull();
  act(() => stream.emit('run_event', message(152)));
  expect(await screen.findByText('Message 152')).toBeVisible();
  await waitFor(() => expect(panel.scrollTop).toBe(400));  // following the latest events again
});

test('the session page has one page heading, the session title', async () => {
  const { default: AdminLayout } = await import('../AdminLayout');
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.includes('/events/history') ? page([])
    : url.startsWith('/api/claude-code/') ? Response.json(detail) : Response.json({ is_admin: true })));
  render(<MemoryRouter initialEntries={['/documentation/generations/s1']}><AdminLayout><ClaudeCodeSessionView sessionId="s1" /></AdminLayout></MemoryRouter>);
  expect(await screen.findByRole('heading', { name: 'Payments – API reference', level: 1 })).toBeVisible();
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
  // the back link, besides the sidebar's own Generate documents link
  expect(screen.getAllByRole('link', { name: 'Generate documents' }).map(link => link.getAttribute('href'))).toEqual(['/documentation/generations', '/documentation/generations']);
});

function panelSize(clientHeight: number) {  // the Activity panel's size, before it renders: each event is 100px tall
  const restore = ['clientHeight', 'scrollHeight'].map(name => [name, Object.getOwnPropertyDescriptor(HTMLElement.prototype, name)] as const);
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true,
    get() { return (this as HTMLElement).dataset.testid === 'activity-scroll' ? clientHeight : 0; } });
  Object.defineProperty(HTMLElement.prototype, 'scrollHeight', { configurable: true,
    get() { return (this as HTMLElement).dataset.testid === 'activity-scroll' ? (this as HTMLElement).querySelectorAll('[data-event]').length * 100 : 0; } });
  return () => restore.forEach(([name, descriptor]) => descriptor ? Object.defineProperty(HTMLElement.prototype, name, descriptor)
    : delete (HTMLElement.prototype as unknown as Record<string, unknown>)[name]);
}

test('a short history fills the Activity panel by loading earlier pages until it overflows or the beginning', async () => {
  const restore = panelSize(450);
  try {
    const urls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      urls.push(url);
      if (url.endsWith('?before=150&limit=100')) return page([message(120)], true);
      if (url.endsWith('?before=120&limit=100')) return page([message(90), message(91)], true);
      if (url.endsWith('?limit=100')) return page([message(150), message(151)], true);
      return Response.json({ ...detail, status: 'waiting', turns: 1, queued: 0 });
    }));
    render(<MemoryRouter><ClaudeCodeSessionView sessionId="s1" /></MemoryRouter>);
    expect(await screen.findByText('Message 90')).toBeVisible();  // 2 + 1 events do not fill 450px; 5 do
    await act(async () => {});
    expect(urls.filter(url => url.includes('/events/history'))).toEqual([
      '/api/claude-code/sessions/s1/events/history?limit=100',
      '/api/claude-code/sessions/s1/events/history?before=150&limit=100',
      '/api/claude-code/sessions/s1/events/history?before=120&limit=100']);
    expect(screen.queryByText('Beginning of the session')).toBeNull();
  } finally { restore(); }
});

test('scrolling near the top again while an earlier page loads does not load it twice; leaving aborts the load', async () => {
  const restore = panelSize(50);
  try {
    const older: { url: string; signal?: AbortSignal }[] = [];
    vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
      if (url.includes('before=')) { older.push({ url, signal: init?.signal ?? undefined }); return new Promise<Response>(() => {}); }
      if (url.includes('/events/history')) return Promise.resolve(page([message(150), message(151)], true));
      return Promise.resolve(Response.json({ ...detail, status: 'waiting', turns: 1, queued: 0 }));
    }));
    const view = render(<MemoryRouter><ClaudeCodeSessionView sessionId="s1" /></MemoryRouter>);
    expect(await screen.findByText('Message 151')).toBeVisible();
    const panel = screen.getByTestId('activity-scroll');
    Object.defineProperty(panel, 'scrollTop', { configurable: true, writable: true, value: 0 });
    act(() => {  // two scroll events before React renders the first load's state
      fireEvent.scroll(panel);
      fireEvent.scroll(panel);
    });
    await act(async () => {});
    fireEvent.scroll(panel);
    expect(older.map(item => item.url)).toEqual(['/api/claude-code/sessions/s1/events/history?before=150&limit=100']);
    expect(older[0].signal?.aborted).toBe(false);
    view.unmount();
    expect(older[0].signal?.aborted).toBe(true);
  } finally { restore(); }
});

test('a native session waits for the agent session worker', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.includes('/events/history') ? page([]) : Response.json({ ...detail, runtime: 'openai' })));
  render(<MemoryRouter><ClaudeCodeSessionView sessionId="s1" /></MemoryRouter>);
  expect(await screen.findByText('Waiting for the agent session worker. The session starts when the worker is running.')).toBeVisible();
  expect(screen.queryByText(/Claude Code worker/)).toBeNull();
});

test('a comment to a working native session reaches the agent at its next step', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes('/events/history')) return page([]);
    if (url.endsWith('/messages')) return Response.json({ seq: 2, status: 'queued', working: true }, { status: 202 });
    return Response.json({ ...detail, runtime: 'openai', status: 'working', worker_active: true });
  }));
  render(<MemoryRouter><ClaudeCodeSessionView sessionId="s1" /></MemoryRouter>);
  const box = await screen.findByLabelText('Message');
  expect(box).toBeEnabled();  // the message box stays usable while the agent works
  fireEvent.change(box, { target: { value: 'Also cover retries' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
  expect(await screen.findByText('Queued. Your comment reaches the agent at its next step.')).toBeVisible();
});
