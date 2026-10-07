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
  prompt_snapshot: { id: 'p', name: 'Code analysis', version: 3, comments: '', system_prompt: 'You are an expert technical writer.' },
  context_snapshot: { folders: [{ path: '/work/data/code/app', kind: 'code_project', name: 'Payments API' }], code_project_ids: ['repo'], servers: [], notes: '' },
  inputs: [], created_at: '2026-10-07T10:00:00Z', last_activity_at: '2026-10-07T10:00:00Z', closed_at: null };

test('the session page streams activity, sends messages, stops, saves the draft and closes', async () => {
  const posts: { url: string; body?: unknown }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
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
  const [stream] = Stream.instances;
  expect(stream.url).toBe('/api/claude-code/sessions/s1/events');
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
  expect(activity.getByText('Document the payments API.')).toBeVisible();
  expect(activity.getByText('Start with the routes.')).toBeVisible();
  expect(activity.getByText('/work/data/code/app/main.py')).toBeVisible();
  expect(activity.getAllByText(/drwxr-xr-x app/)).toHaveLength(1);  // a replayed event is shown once
  expect(screen.getByRole('button', { name: 'Save document' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'Also cover retries.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
  expect(await screen.findByText(/^Queued\./)).toBeVisible();
  expect(screen.getByLabelText('Message')).toHaveValue('');
  fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
  await waitFor(() => expect(posts.map(p => p.url)).toContain('/api/claude-code/sessions/s1/stop'));
  act(() => {
    stream.emit('run_event', { id: 5, turn: 1, seq: 7, kind: 'message', payload: { text: '# Payments API' } });
    stream.emit('session', { ...state, status: 'waiting', turns: 1, worker_active: true, queued: 0, draft: '# Payments API\n\nRetries back off.',
      draft_turn: 1, input_tokens: 1050, output_tokens: 40, cached_tokens: 900 });
  });
  expect(screen.getByText('Waiting for you')).toBeVisible();
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
