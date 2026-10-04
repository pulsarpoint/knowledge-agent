import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, expect, test, vi } from 'vitest';
import DocumentPublication from '../DocumentPublication';
import DocumentsView from '../DocumentsView';

afterEach(() => vi.unstubAllGlobals());
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const base = { id: 'doc-1', slug: 'payments-runbook', version: 2, published_path: null, published_version: null, publish_error: null, index_pending: false };

test('publishes an unpublished document to a folder and shows where it is', async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => init?.method === 'POST'
    ? json({ path: 'runbooks/payments-runbook.md', status: 'created', index_pending: false })
    : json({ ...base, published_path: 'runbooks/payments-runbook.md', published_version: 2 }));
  vi.stubGlobal('fetch', fetchMock);
  render(<DocumentPublication document={base} />);
  expect(screen.getByText('Not in the knowledge base.')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Publish to knowledge base' }));
  const folder = screen.getByLabelText('RAG folder');
  expect(folder).toHaveValue('documents');
  fireEvent.change(folder, { target: { value: 'runbooks' } });
  fireEvent.click(screen.getByRole('button', { name: 'Publish' }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/documentation/documents/doc-1/publish',
    expect.objectContaining({ method: 'POST', body: JSON.stringify({ folder: 'runbooks' }) })));
  expect(await screen.findByText('runbooks/payments-runbook.md')).toBeInTheDocument();
  expect(screen.getByText('v2')).toBeInTheDocument();
});

test('shows a failed republish with Retry, and pending indexing', async () => {
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => init?.method === 'POST'
    ? json({ path: 'documents/payments-runbook.md', status: 'updated', index_pending: false })
    : json({ ...base, published_path: 'documents/payments-runbook.md', published_version: 2 }));
  vi.stubGlobal('fetch', fetchMock);
  render(<DocumentPublication document={{ ...base, version: 3, published_path: 'documents/payments-runbook.md', published_version: 2,
    publish_error: 'RAG service is unavailable. Retry to check the file', index_pending: true }} />);
  expect(screen.getByText('Indexing pending')).toBeInTheDocument();
  expect(screen.getByText(/Publishing v3 failed: RAG service is unavailable/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/documentation/documents/doc-1/publish',
    expect.objectContaining({ method: 'POST', body: JSON.stringify({ folder: 'documents' }) })));
});

test('unpublish asks for confirmation before removing the file', async () => {
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => init?.method === 'DELETE'
    ? json({ path: 'documents/payments-runbook.md' }) : json(base));
  vi.stubGlobal('fetch', fetchMock);
  render(<DocumentPublication document={{ ...base, published_path: 'documents/payments-runbook.md', published_version: 2 }} />);
  fireEvent.click(screen.getByRole('button', { name: 'Unpublish' }));
  const dialog = screen.getByRole('alertdialog');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
  expect(fetchMock).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Unpublish' }));
  fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Remove from knowledge base' }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/documentation/documents/doc-1/publish', expect.objectContaining({ method: 'DELETE' })));
  expect(await screen.findByText('Not in the knowledge base.')).toBeInTheDocument();
});

test('documents list marks published documents', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => String(url).includes('/resources')
    ? json({ code_projects: [], services: [], servers: [], deployments: [] })
    : json({ total: 1, documents: [{ ...base, title: 'Payments runbook', description: '', purpose: 'runbook', audience: 'both', tags: [],
        code_project_ids: [], service_ids: [], server_ids: [], deployment_ids: [], revision: 2, current_version_id: 'v2', published_version_id: 'v2',
        content_hash: 'x', created_at: '2026-10-04T10:00:00Z', updated_at: '2026-10-04T10:00:00Z',
        published_path: 'documents/payments-runbook.md', published_version: 2 }] })));
  render(<MemoryRouter><DocumentsView /></MemoryRouter>);
  expect(await screen.findByText('Payments runbook')).toBeInTheDocument();
  expect(screen.getByText('Published')).toBeInTheDocument();
});
