import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, expect, test, vi } from 'vitest';
import AdminLayout from '../AdminLayout';
import ClaudeCodeSessionsView from '../ClaudeCodeSessionsView';

afterEach(() => vi.unstubAllGlobals());
const row = { id: 's1', title: 'Payments – API reference', agent_id: 'writer', agent_name: 'Technical writer', model_name: 'Claude on Bedrock',
  status: 'waiting', status_message: '', turns: 2, input_tokens: 12345, output_tokens: 678, cached_tokens: 9000, cost_usd: 0.4,
  document_id: 'doc', document_title: 'Payments API reference.md', folder: 'payments-api-docs', worker_active: true, queued: 0,
  created_at: '2026-10-07T10:00:00Z', last_activity_at: '2026-10-07T11:00:00Z', closed_at: null };

test('Docs → Sessions lists sessions with status, agent, tokens and document', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.startsWith('/api/claude-code/sessions')
    ? Response.json({ sessions: [row, { ...row, id: 's2', title: 'Jira overview', status: 'closed', document_id: null, document_title: null }] })
    : Response.json({ is_admin: true })));
  render(<MemoryRouter initialEntries={['/documentation/sessions']}><AdminLayout><ClaudeCodeSessionsView /></AdminLayout></MemoryRouter>);
  const first = (await screen.findByRole('link', { name: 'Payments – API reference' })).closest('tr')!;
  expect(within(first).getByRole('link', { name: 'Payments – API reference' })).toHaveAttribute('href', '/documentation/sessions/s1');
  expect(within(first).getByText('Waiting for you')).toBeVisible();
  expect(within(first).getByText('Technical writer')).toBeVisible();
  expect(within(first).getByText('12,345 in · 678 out')).toBeVisible();
  expect(within(first).getAllByRole('link').map(link => link.getAttribute('href'))).toContain('/documents/doc');
  const second = screen.getByRole('link', { name: 'Jira overview' }).closest('tr')!;
  expect(within(second).getByText('Finished')).toBeVisible();
  expect(within(second).getByText('Not saved')).toBeVisible();
  expect(screen.getByRole('link', { name: 'New session' })).toHaveAttribute('href', '/documentation/generations/new?scope=agent');
  const docs = within(screen.getByRole('navigation', { name: 'Docs' }));
  expect(docs.getAllByRole('link').map(link => link.textContent)).toEqual(['Documents', 'Prompts', 'Generate documents', 'Sessions', 'Folders']);
  expect(docs.getByRole('link', { name: 'Sessions' })).toHaveAttribute('href', '/documentation/sessions');
  expect(screen.getByRole('heading', { name: 'Documentation sessions' })).toBeVisible();
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);  // the layout's heading only
});
