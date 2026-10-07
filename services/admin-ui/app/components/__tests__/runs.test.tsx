import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, expect, test, vi } from 'vitest';
import RunsView from '../RunsView';
import RunRecordView from '../RunRecordView';

afterEach(() => vi.unstubAllGlobals());
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

const runs = { next: null, runs: [
  { run_key: 'run:1', agent_id: 'search', agent: 'Search agent', kind: 'test', title: 'How many retries?', user: 'alice@example.test', status: 'completed', total_tokens: 15, started_at: '2026-10-04T10:00:00Z' },
] };

test('runs list shows each run and filters by kind', async () => {
  const fetchMock = vi.fn(async (_url: string) => json(runs));
  vi.stubGlobal('fetch', fetchMock);
  render(<MemoryRouter><RunsView /></MemoryRouter>);
  expect(await screen.findByText('How many retries?')).toBeInTheDocument();
  expect(screen.getByText('alice@example.test')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'How many retries?' })).toHaveAttribute('href', '/activity/runs/run%3A1');
  fireEvent.change(screen.getByLabelText('Run type'), { target: { value: 'board' } });
  await waitFor(() => expect(fetchMock.mock.calls.at(-1)?.[0]).toContain('kind=board'));
});

test('run types have readable names in the filter and the list', async () => {
  vi.stubGlobal('fetch', vi.fn(async (_url: string) => json({ next: null, runs: [
    { ...runs.runs[0], run_key: 'cc:s1:1', kind: 'claude_code', title: 'Payments API reference' }] })));
  render(<MemoryRouter><RunsView /></MemoryRouter>);
  const row = (await screen.findByText('Payments API reference')).closest('tr')!;
  expect(within(row).getByText('Claude Code')).toBeInTheDocument();
  const options = within(screen.getByLabelText('Run type')).getAllByRole('option');
  expect(options.map(option => [option.getAttribute('value'), option.textContent])).toEqual([
    ['', 'All'], ['test', 'Agent test'], ['board', 'Board task'], ['claude_code', 'Claude Code']]);
});

test('runs list passes a key prefix from the URL', async () => {
  const fetchMock = vi.fn(async (_url: string) => json(runs));
  vi.stubGlobal('fetch', fetchMock);
  render(<MemoryRouter initialEntries={['/activity/runs?key_prefix=cc%3As1%3A']}><RunsView /></MemoryRouter>);
  await screen.findByText('How many retries?');
  expect(fetchMock.mock.calls[0][0]).toContain('key_prefix=cc%3As1%3A');
});

test('run record shows the full timeline', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ run_key: 'run:1', agent_id: 'search', user: 'alice@example.test', events: [
    { seq: 1, kind: 'start', agent_id: 'search', payload: { kind: 'test', title: 'How many retries?', agent: 'Search agent' }, created_at: '2026-10-04T10:00:00Z' },
    { seq: 2, kind: 'instructions', agent_id: 'search', payload: { agent: 'Search agent', text: 'SYSTEM PROMPT TEXT' }, created_at: '2026-10-04T10:00:00Z' },
    { seq: 3, kind: 'reasoning', agent_id: 'search', payload: { agent: 'Search agent', available: false }, created_at: '2026-10-04T10:00:01Z' },
    { seq: 4, kind: 'tool_call', agent_id: 'search', payload: { agent: 'Search agent', name: 'search_documents', arguments: '{"query":"retries"}' }, created_at: '2026-10-04T10:00:01Z' },
    { seq: 5, kind: 'tool_result', agent_id: 'search', payload: { agent: 'Search agent', name: 'search_documents', output: 'LONG OUTPUT ' + 'x'.repeat(5000) }, created_at: '2026-10-04T10:00:02Z' },
    { seq: 6, kind: 'message', agent_id: 'search', payload: { agent: 'Search agent', text: 'Three retries [1].' }, created_at: '2026-10-04T10:00:03Z' },
    { seq: 7, kind: 'usage', agent_id: 'search', payload: { agent: 'Search agent', input_tokens: 10, output_tokens: 5, total_tokens: 15 }, created_at: '2026-10-04T10:00:03Z' },
    { seq: 8, kind: 'end', agent_id: 'search', payload: { status: 'completed' }, created_at: '2026-10-04T10:00:03Z' },
  ] })));
  render(<MemoryRouter><RunRecordView runKey="run:1" /></MemoryRouter>);
  expect(await screen.findByRole('heading', { name: 'How many retries?' })).toBeInTheDocument();
  expect(screen.getByText('completed')).toBeInTheDocument();
  expect(screen.getByText('15 tokens')).toBeInTheDocument();
  expect(screen.getByText('SYSTEM PROMPT TEXT')).not.toBeVisible();
  expect(screen.getByText('Reasoning not returned by the provider.')).toBeInTheDocument();
  expect(screen.getByText('{"query":"retries"}')).toBeInTheDocument();
  expect(screen.getByText(/LONG OUTPUT x{5000}/)).toBeInTheDocument();
  expect(screen.getByText('Three retries [1].')).toBeInTheDocument();
});
