import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
vi.mock('../Markdown', () => ({ default: ({ content }: { content: string }) => <div data-testid="markdown">{content}</div> }));
import SessionView from '../SessionView';

class Stream extends EventTarget {
  static instances: Stream[] = [];
  close = vi.fn();
  constructor(public url: string) { super(); Stream.instances.push(this); }
  emit(type: string, value: unknown) { this.dispatchEvent(new MessageEvent(type, { data: JSON.stringify(value) })); }
}
beforeEach(() => { Stream.instances = []; vi.stubGlobal('EventSource', Stream); });
afterEach(() => vi.unstubAllGlobals());
const state = { status: 'starting', status_message: '', turns: 0, input_tokens: 0, output_tokens: 0, cached_tokens: 0,
  model_calls: 0, reasoning_tokens: 0, draft: null, draft_turn: null, document_id: null, document_draft: null, worker_active: false, queued: 1 };
const detail = { ...state, id: 's1', title: 'Payments – API reference', purpose: 'documents', document_name: 'Payments – API reference',
  folder: 'payments-api-docs', agent_id: 'writer', agent_name: 'Technical writer', model_id: 'claude', model_name: 'Claude on Bedrock',
  document_title: null, run_resources: null,
  prompt_snapshot: { system_prompt: 'You are an expert technical writer.' },
  context_snapshot: { folders: [{ path: '/work/data/code/app', kind: 'code_project', name: 'Payments API', code_project_id: 'repo' }],
    code_project_ids: ['repo'], instructions: '' },
  tool_counts: [{ name: 'Read', calls: 5 }, { name: 'Grep', calls: 2 }],
  inputs: [], created_at: '2026-10-07T10:00:00Z', last_activity_at: '2026-10-07T10:00:00Z', closed_at: null };
/** One finished turn per id, its answer `Message <id>`: every event is its own turn in the main pane. */
const answer = (id: number) => ({ id, turn: id, seq: 1, kind: 'message', payload: { text: `Message ${id}` } });
const page = (events: unknown[], has_more = false) => Response.json({ events, has_more });
const opened = () => waitFor(() => { expect(Stream.instances).toHaveLength(1); return Stream.instances[0]; });

test('the session page shows the tools the session used', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.includes('/events/history') ? page([]) : Response.json(detail)));
  render(<MemoryRouter><SessionView sessionId="s1" /></MemoryRouter>);
  expect(await screen.findByLabelText('Tools used')).toHaveTextContent('Read 5 · Grep 2');
  expect(screen.getByText('Resources: Fixed')).toBeVisible();
});

test('the session page follows the turn, sends messages, stops, previews the draft and closes', async () => {
  const posts: { url: string; body?: unknown }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes('/events/history')) return page([]);
    if (init?.method !== 'POST') return Response.json(detail);
    const body = init.body ? JSON.parse(init.body as string) : undefined;
    posts.push({ url, body });
    if (url.endsWith('/messages')) return Response.json({ seq: 2, status: 'queued', working: true }, { status: 202 });
    if (url.endsWith('/stop')) return Response.json({ id: 's1', discarded: 0 }, { status: 202 });
    return Response.json({ id: 's1' });
  }));
  render(<MemoryRouter><SessionView sessionId="s1" /></MemoryRouter>);
  expect((await screen.findAllByText('Starting'))[0]).toBeVisible();
  expect(screen.getByText(/Waiting for the agent session worker/)).toBeVisible();
  const stream = await opened();
  expect(stream.url).toBe('/api/sessions/s1/events?after=0');
  act(() => {
    stream.emit('session', { ...state, status: 'working', turns: 1, worker_active: true, queued: 0 });
    stream.emit('run_event', { id: 1, turn: 1, seq: 3, kind: 'input', payload: { task: 'Document the payments API.' } });
    stream.emit('run_event', { id: 2, turn: 1, seq: 4, kind: 'reasoning', payload: { text: 'Start with the routes.' } });
    stream.emit('run_event', { id: 3, turn: 1, seq: 5, kind: 'tool_call', payload: { name: 'Read', summary: '/work/data/code/app/main.py' } });
    stream.emit('run_event', { id: 4, turn: 1, seq: 6, kind: 'tool_result', payload: { name: 'Read', preview: 'import app', is_error: false } });
  });
  expect(screen.queryByText(/Waiting for the agent session worker/)).toBeNull();
  const run = within(screen.getByRole('region', { name: 'Run' }));
  expect(await run.findByLabelText('Your message')).toHaveTextContent('Document the payments API.');
  fireEvent.click(run.getByText('Working · 1 tool call · 0 tokens'));
  expect(run.getByText('/work/data/code/app/main.py')).toBeVisible();
  expect(screen.queryByRole('region', { name: 'Draft' })).toBeNull();  // no draft yet
  fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'Also cover retries.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }));  // usable while the agent works
  expect(await screen.findByText('Queued. Your comment reaches the agent at its next step.')).toBeVisible();
  expect(screen.getByLabelText('Message')).toHaveValue('');
  fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
  await waitFor(() => expect(posts.map(p => p.url)).toContain('/api/sessions/s1/stop'));
  expect(await screen.findByText('Stopping the current turn…')).toBeVisible();
  act(() => {
    stream.emit('run_event', { id: 5, turn: 1, seq: 7, kind: 'message', payload: { text: '# Payments API' } });
    stream.emit('run_event', { id: 6, turn: 1, seq: 8, kind: 'end', payload: { status: 'completed' } });
    stream.emit('session', { ...state, status: 'waiting', turns: 1, worker_active: true, queued: 0, draft: '# Payments API\n\nRetries back off.',
      draft_turn: 1, input_tokens: 1050, output_tokens: 40, cached_tokens: 900, model_calls: 3 });
  });
  expect(await run.findByRole('link', { name: 'Full record' })).toHaveAttribute('href', '/activity/runs/cc%3As1%3A1');
  expect(screen.queryByText('Stopping the current turn…')).toBeNull();  // the status changed: the notice is stale
  expect(screen.getByLabelText('Token totals')).toHaveTextContent('In 1,050 · Out 40 · Cached 900');
  expect(screen.getByRole('button', { name: 'Stop' })).toBeDisabled();
  const draft = within(await screen.findByRole('region', { name: 'Draft' }));
  expect(draft.getByRole('heading', { name: 'Draft · turn 1' })).toBeVisible();
  fireEvent.click(draft.getByRole('button', { name: 'Preview draft' }));
  const sheet = within(screen.getByRole('dialog'));
  expect(sheet.getByTestId('markdown')).toHaveTextContent('# Payments API Retries back off.');
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  fireEvent.click(screen.getByRole('button', { name: 'Close session' }));
  await waitFor(() => expect(posts.map(p => p.url)).toContain('/api/sessions/s1/close'));
  act(() => { stream.emit('session', { ...state, status: 'closed', turns: 1, draft: '# Payments API\n\nRetries back off.' }); stream.emit('closed', {}); });
  expect(screen.getAllByText('Finished')[0]).toBeVisible();
  expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled();
  expect(stream.close).toHaveBeenCalled();
});

test('opening shows the latest page, streams after its newest event and loads earlier turns near the top', async () => {
  const urls: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    urls.push(url);
    if (url.endsWith('/events/history?before=150&limit=100')) return page([answer(120)]);
    if (url.endsWith('/events/history?limit=100')) return page([answer(150), answer(151)], true);
    return Response.json({ ...detail, status: 'waiting', turns: 151, queued: 0 });
  }));
  render(<MemoryRouter><SessionView sessionId="s1" /></MemoryRouter>);
  expect(await screen.findByText('Message 151')).toBeVisible();
  const stream = await opened();
  expect(stream.url).toBe('/api/sessions/s1/events?after=151');
  expect(screen.queryByText('Beginning of the session')).toBeNull();
  const panel = screen.getByTestId('run-scroll');
  Object.defineProperty(panel, 'scrollHeight', { configurable: true, get: () => panel.querySelectorAll('[data-turn]').length * 100 });
  Object.defineProperty(panel, 'clientHeight', { configurable: true, value: 50 });
  Object.defineProperty(panel, 'scrollTop', { configurable: true, writable: true, value: 0 });
  fireEvent.scroll(panel);  // near the top, away from the bottom
  expect(await screen.findByText('Message 120')).toBeVisible();
  expect(screen.getByText('Beginning of the session')).toBeVisible();
  expect(panel.scrollTop).toBe(100);  // the earlier turn went above: what was on screen stays in place
  const history = () => urls.filter(url => url.includes('/events/history'));
  expect(history()).toEqual(['/api/sessions/s1/events/history?limit=100',
    '/api/sessions/s1/events/history?before=150&limit=100']);
  panel.scrollTop = 0;
  fireEvent.scroll(panel);  // the beginning is reached: nothing more to load
  expect(history()).toHaveLength(2);
  fireEvent.click(screen.getByRole('button', { name: 'Jump to latest' }));
  expect(panel.scrollTop).toBe(300);
  expect(screen.queryByRole('button', { name: 'Jump to latest' })).toBeNull();
  act(() => stream.emit('run_event', answer(152)));
  expect(await screen.findByText('Message 152')).toBeVisible();
  await waitFor(() => expect(panel.scrollTop).toBe(400));  // following the latest turns again
});

function paneSize(clientHeight: number) {  // the main pane's size, before it renders: each turn is 100px tall
  const restore = ['clientHeight', 'scrollHeight'].map(name => [name, Object.getOwnPropertyDescriptor(HTMLElement.prototype, name)] as const);
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true,
    get() { return (this as HTMLElement).dataset.testid === 'run-scroll' ? clientHeight : 0; } });
  Object.defineProperty(HTMLElement.prototype, 'scrollHeight', { configurable: true,
    get() { return (this as HTMLElement).dataset.testid === 'run-scroll' ? (this as HTMLElement).querySelectorAll('[data-turn]').length * 100 : 0; } });
  return () => restore.forEach(([name, descriptor]) => descriptor ? Object.defineProperty(HTMLElement.prototype, name, descriptor)
    : delete (HTMLElement.prototype as unknown as Record<string, unknown>)[name]);
}

test('a short history fills the main pane by loading earlier pages until it overflows or the beginning', async () => {
  const restore = paneSize(450);
  try {
    const urls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      urls.push(url);
      if (url.endsWith('?before=150&limit=100')) return page([answer(120)], true);
      if (url.endsWith('?before=120&limit=100')) return page([answer(90), answer(91)], true);
      if (url.endsWith('?limit=100')) return page([answer(150), answer(151)], true);
      return Response.json({ ...detail, status: 'waiting', turns: 151, queued: 0 });
    }));
    render(<MemoryRouter><SessionView sessionId="s1" /></MemoryRouter>);
    expect(await screen.findByText('Message 90')).toBeVisible();  // 2 + 1 turns do not fill 450px; 5 do
    await act(async () => {});
    expect(urls.filter(url => url.includes('/events/history'))).toEqual([
      '/api/sessions/s1/events/history?limit=100',
      '/api/sessions/s1/events/history?before=150&limit=100',
      '/api/sessions/s1/events/history?before=120&limit=100']);
    expect(screen.queryByText('Beginning of the session')).toBeNull();
  } finally { restore(); }
});

test('the session page has one page heading, the session title', async () => {
  const { default: AdminLayout } = await import('../AdminLayout');
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.includes('/events/history') ? page([])
    : url.startsWith('/api/sessions/') ? Response.json(detail) : Response.json({ is_admin: true })));
  render(<MemoryRouter initialEntries={['/sessions/s1']}><AdminLayout><SessionView sessionId="s1" /></AdminLayout></MemoryRouter>);
  expect(await screen.findByRole('heading', { name: 'Payments – API reference', level: 1 })).toBeVisible();
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
  expect(screen.getByText(/Started from the new document Payments – API reference/)).toBeVisible();
  expect(screen.queryByRole('link', { name: 'Generate documents' })).toBeNull();
});

test('a native session waits for the agent session worker, and a comment to a working one reaches it at its next step', async () => {
  let working = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.includes('/events/history')) return page([]);
    if (url.endsWith('/messages')) return Response.json({ seq: 2, status: 'queued', working: true }, { status: 202 });
    return Response.json(working ? { ...detail, status: 'working', worker_active: true } : { ...detail });
  }));
  const view = render(<MemoryRouter><SessionView sessionId="s1" /></MemoryRouter>);
  expect(await screen.findByText('Waiting for the agent session worker. The session starts when the worker is running.')).toBeVisible();
  view.unmount();
  working = true;
  render(<MemoryRouter><SessionView sessionId="s1" /></MemoryRouter>);
  const box = await screen.findByLabelText('Message');
  expect(box).toBeEnabled();
  fireEvent.change(box, { target: { value: 'Also cover retries' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
  expect(await screen.findByText('Queued. Your comment reaches the agent at its next step.')).toBeVisible();
});

test('the session page fills the screen below the app header, so the message box stays visible', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.includes('/events/history') ? page([]) : Response.json(detail)));
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(() => ({ top: 100 } as DOMRect));
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: 700 });
  try {
    render(<MemoryRouter><SessionView sessionId="s1" /></MemoryRouter>);
    const box = await screen.findByRole('form', { name: 'Message box' });
    expect(box.closest<HTMLElement>('[style]')?.style.height).toBe('576px');  // 700 - 100 - 24 (the main padding)
  } finally { vi.mocked(HTMLElement.prototype.getBoundingClientRect).mockRestore(); }
});

test("a run whose turn wrote a document draft links to it; a new document's draft opens its own page", async () => {
  const withDraft = { ...detail, status: 'waiting', turns: 1, draft: '# Payments API', draft_turn: 1, document_draft: { id: 'd2', document_id: null, status: 'open' } };
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.includes('/events/history') ? page([]) : Response.json(withDraft)));
  render(<MemoryRouter><SessionView sessionId="s1" /></MemoryRouter>);
  const draft = within(await screen.findByRole('region', { name: 'Draft' }));
  expect(await draft.findByRole('link', { name: 'Open draft' })).toHaveAttribute('href', '/documents/drafts/d2');
  expect(draft.queryByRole('button', { name: 'Save as draft' })).toBeNull();
  expect(draft.getByRole('button', { name: 'Preview draft' })).toBeEnabled();
});

test('a run whose document draft was promoted links to the document and offers no Save as draft', async () => {
  const promoted = { ...detail, status: 'waiting', turns: 1, draft: '# Payments API', draft_turn: 1, document_id: 'doc1',
    document_draft: { id: 'd2', document_id: 'doc1', status: 'promoted' } };
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.includes('/events/history') ? page([]) : Response.json(promoted)));
  render(<MemoryRouter><SessionView sessionId="s1" /></MemoryRouter>);
  const draft = within(await screen.findByRole('region', { name: 'Draft' }));
  expect(await draft.findByRole('link', { name: 'Open document' })).toHaveAttribute('href', '/documents/doc1');
  expect(draft.queryByRole('button', { name: 'Save as draft' })).toBeNull();
  expect(draft.queryByRole('link', { name: 'Open draft' })).toBeNull();
});

test('a run whose document draft was discarded offers no Save as draft', async () => {
  const discarded = { ...detail, status: 'waiting', turns: 1, draft: '# Payments API', draft_turn: 1,
    document_draft: { id: 'd2', document_id: null, status: 'discarded' } };
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.includes('/events/history') ? page([]) : Response.json(discarded)));
  render(<MemoryRouter><SessionView sessionId="s1" /></MemoryRouter>);
  const draft = within(await screen.findByRole('region', { name: 'Draft' }));
  expect(await draft.findByText('The document draft was discarded.')).toBeVisible();
  expect(draft.queryByRole('button', { name: 'Save as draft' })).toBeNull();
  expect(draft.queryByRole('link', { name: 'Open draft' })).toBeNull();
});

const serve = (session: unknown, events: unknown[] = []) =>
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.includes('/events/history') ? page(events) : Response.json(session)));
const toolCall = (name: string) => ({ id: 1, turn: 1, seq: 1, kind: 'tool_call', payload: { name, call_id: 'c1', summary: 'x' } });
const toolResult = (name: string, preview: string, is_error = false) =>
  ({ id: 2, turn: 1, seq: 2, kind: 'tool_result', payload: { name, call_id: 'c1', preview, is_error } });

test('a document run shows where it started, with no Save as draft', async () => {
  serve({ ...detail, purpose: 'revision', target_document_id: 'doc-1', draft: null, document_draft: null });
  render(<MemoryRouter initialEntries={['/sessions/s1']}><Routes><Route path="/sessions/:id" element={<SessionView sessionId="s1" />} /></Routes></MemoryRouter>);
  expect(await screen.findByRole('heading', { name: detail.title })).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'the document' })).toHaveAttribute('href', '/documents/doc-1/agent/s1');
  expect(screen.queryByRole('button', { name: 'Save as draft' })).toBeNull();
  expect(screen.queryByRole('region', { name: 'Draft' })).toBeNull();
});

test('a revision run with an open document draft shows Open draft', async () => {
  serve({ ...detail, purpose: 'revision', target_document_id: 'doc-1', draft: null, document_draft: { id: 'x2', document_id: 'doc-1', status: 'open' } });
  render(<MemoryRouter><SessionView sessionId="s1" /></MemoryRouter>);
  expect(await screen.findByRole('link', { name: 'Open draft' })).toHaveAttribute('href', '/documents/doc-1?tab=drafts&draft=x2');
  expect(screen.queryByRole('button', { name: 'Preview draft' })).toBeNull();
});

test('a new-document run without final-answer text shows Open draft from its document draft', async () => {
  serve({ ...detail, purpose: 'documents', draft: null, document_draft: { id: 'x3', document_id: null, status: 'open' } });
  render(<MemoryRouter><SessionView sessionId="s1" /></MemoryRouter>);
  expect(await screen.findByRole('link', { name: 'Open draft' })).toHaveAttribute('href', '/documents/drafts/x3');
});

test('a promoted new-document run opens the document', async () => {
  serve({ ...detail, purpose: 'documents', draft: null, document_id: 'doc-9', document_draft: { id: 'x4', document_id: null, status: 'promoted' } });
  render(<MemoryRouter><SessionView sessionId="s1" /></MemoryRouter>);
  expect(await screen.findByRole('link', { name: 'Open document' })).toHaveAttribute('href', '/documents/doc-9');
});

test('an old run with final-answer text still offers Preview draft and opens the document draft', async () => {
  serve({ ...detail, purpose: 'documents', draft: '# Draft', draft_turn: 1, document_draft: { id: 'x1', document_id: null, status: 'open' } });
  render(<MemoryRouter><SessionView sessionId="s1" /></MemoryRouter>);
  expect(await screen.findByRole('link', { name: 'Open draft' })).toHaveAttribute('href', '/documents/drafts/x1');
});

test('a Test run is shown here, not redirected, and links to its agent', async () => {
  serve({ ...detail, purpose: 'test', agent_id: 'code', draft: null });
  render(<MemoryRouter><SessionView sessionId="s1" /></MemoryRouter>);
  expect(await screen.findByRole('link', { name: 'the agent' })).toHaveAttribute('href', '/agents/code?view=test&session_id=s1');
});

test('a discovery run links to its server', async () => {
  serve({ ...detail, purpose: 'discovery', server_id: 'srv 1', agent_id: null });
  render(<MemoryRouter><SessionView sessionId="s1" /></MemoryRouter>);
  expect(await screen.findByRole('link', { name: 'the server' })).toHaveAttribute('href', '/servers/srv%201/discovery/s1');
});

test('a refused tool call shows as an error on that call', async () => {
  serve({ ...detail, purpose: 'revision', target_document_id: 'doc-1' }, [toolCall('save_document'),
    toolResult('save_document', 'This document is not available in this run.', true)]);
  render(<MemoryRouter><SessionView sessionId="s1" /></MemoryRouter>);
  expect(await screen.findByText(/not available in this run/)).toBeInTheDocument();
});
