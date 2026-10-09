import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, expect, test, vi } from 'vitest';
import AdminLayout from '../AdminLayout';
import GenerationsView from '../GenerationsView';

afterEach(() => vi.unstubAllGlobals());
const row = { id: 's1', title: 'Payments – API reference', agent_id: 'writer', agent_name: 'Technical writer', model_name: 'Claude on Bedrock',
  status: 'waiting', status_message: '', turns: 2, input_tokens: 12345, output_tokens: 678, cached_tokens: 9000,
  document_id: 'doc', document_title: 'Payments API reference.md', folder: 'payments-api-docs', worker_active: true, queued: 0,
  created_at: '2026-10-07T10:00:00Z', last_activity_at: '2026-10-07T11:00:00Z', closed_at: null };

test('Docs → Generate documents lists generations with status, agent, tokens and document', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.startsWith('/api/generations/sessions')
    ? Response.json({ sessions: [row, { ...row, id: 's2', title: 'Jira overview', status: 'closed', document_id: null, document_title: null },
      { ...row, id: 's3', title: 'Native overview', model_name: 'GPT' }] })
    : Response.json({ is_admin: true })));
  render(<MemoryRouter initialEntries={['/documentation/generations']}><AdminLayout><GenerationsView /></AdminLayout></MemoryRouter>);
  const first = (await screen.findByRole('link', { name: 'Payments – API reference' })).closest('tr')!;
  expect(within(first).getByRole('link', { name: 'Payments – API reference' })).toHaveAttribute('href', '/documentation/generations/s1');
  expect(within(first).getByText('Waiting for you')).toBeVisible();
  expect(within(first).getByText('Technical writer')).toBeVisible();
  expect(within(first).getByText('Claude on Bedrock')).toBeVisible();
  const native = screen.getByRole('link', { name: 'Native overview' }).closest('tr')!;
  expect(within(native).getByText('GPT')).toBeVisible();
  expect(within(first).getByText('12,345 in · 678 out')).toBeVisible();
  expect(within(first).getAllByRole('link').map(link => link.getAttribute('href'))).toContain('/documents/doc');
  const second = screen.getByRole('link', { name: 'Jira overview' }).closest('tr')!;
  expect(within(second).getByText('Finished')).toBeVisible();
  expect(within(second).getByText('Not saved')).toBeVisible();
  expect(screen.getByRole('link', { name: 'New' })).toHaveAttribute('href', '/documentation/generations/new');
  const docs = within(screen.getByRole('navigation', { name: 'Docs' }));
  expect(docs.getAllByRole('link').map(link => link.textContent)).toEqual(['Documents', 'Prompts', 'Generate documents', 'Folders']);
  expect(screen.getByRole('heading', { name: 'Generate documents', level: 1 })).toBeVisible();
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);  // the layout's heading only
});
